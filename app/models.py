import logging
import pathlib
import threading
import time
import urllib.request
import hashlib
import json

from . import APP_VERSION, config

log = logging.getLogger("models")

ECAPA = "speechbrain/spkrec-ecapa-voxceleb"
WHISPER = "Systran/faster-whisper-small"
REVISIONS = {
    ECAPA: "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286",
    WHISPER: "536b0662742c02347bc0e980a01041f333bce120",
}
SHA256 = {
    WHISPER: {
        "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
        "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    },
    ECAPA: {
        "hyperparams.yaml": "6f78854fa04ba59e761437b76a2575d3aba5e5016de3e9b69f0c9a5077fb1a41",
        "embedding_model.ckpt": "0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2",
        "mean_var_norm_emb.ckpt": "cd70225b05b37be64fc5a95e24395d804231d43f74b2e1e5a513db7b69b34c33",
        "classifier.ckpt": "fd9e3634fe68bd0a427c95e354c0c677374f62b3f434e45b78599950d860d535",
        "label_encoder.txt": "e13c3a167bb4112685670ee896d20e2b565af16b3a4ceeaa8689fa4d22adb8b9",
    },
}
FILES = {repo: tuple(hashes) for repo, hashes in SHA256.items()}
_locks = {}
_locks_guard = threading.Lock()
_items = {}
_status = {"active": "", "file": "", "done": 0, "total": 0, "error": "", "updated": 0.0}


def local_dir(repo):
    return config.DATA_DIR / "models" / repo.replace("/", "--")


def is_ready(repo):
    d = local_dir(repo)
    try:
        complete = json.loads((d / ".complete").read_text())
    except Exception:
        return False
    return complete == {"version": APP_VERSION, "sha256": SHA256.get(repo)} and all((d / f).exists() for f in FILES.get(repo, []))


def _digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def status():
    out = dict(_status)
    out["ready"] = {r: is_ready(r) for r in FILES}
    return out


def _lock(repo):
    with _locks_guard:
        return _locks.setdefault(repo, threading.Lock())


def _progress(key, done=None, total=None, finished=False):
    if finished:
        _items.pop(key, None)
    else:
        it = _items.setdefault(key, {"done": 0, "total": 0})
        if done is not None:
            it["done"] = done
        if total is not None:
            it["total"] = total
    cur = next(iter(_items), "")
    _publish(active=cur.split("/", 2)[0] + "/" + cur.split("/", 2)[1] if cur.count("/") >= 2 else "", file=cur.rsplit("/", 1)[-1] if cur else "",
             done=sum(v["done"] for v in _items.values()), total=sum(v["total"] for v in _items.values()))


def _publish(**kw):
    _status.update(kw, updated=time.time())
    try:
        from .events import bus
        bus.publish({"type": "models", **{k: _status[k] for k in ("active", "file", "done", "total", "error")},
                     "items": {k: dict(v) for k, v in _items.items()}}, store=False)
    except Exception:
        pass


def _fetch(url, dest: pathlib.Path, key=""):
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": f"Deban/{APP_VERSION}"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        if have and r.status != 206:
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        mode = "ab" if have else "wb"
        last = 0.0
        _progress(key, done=have, total=total)
        with open(part, mode) as f:
            done = have
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 0.5:
                    _progress(key, done=done, total=total)
                    last = time.time()
        if total and done < total:
            raise IOError(f"short read {done}/{total}")
    part.replace(dest)


def ensure(repo, force=False):
    files = FILES.get(repo)
    hashes = SHA256.get(repo)
    if not files or not hashes or set(files) != set(hashes):
        raise KeyError(repo)
    with _lock(repo):
        if is_ready(repo) and not force:
            return local_dir(repo)
        d = local_dir(repo)
        d.mkdir(parents=True, exist_ok=True)
        try:
            for f in files:
                dest = d / f
                if dest.exists() and not force and _digest(dest) == hashes[f]:
                    continue
                if dest.exists():
                    dest.unlink()
                key = f"{repo}/{f}"
                _progress(key, done=0, total=0)
                log.info("models: downloading %s", key)
                try:
                    _fetch(f"https://huggingface.co/{repo}/resolve/{REVISIONS[repo]}/{f}?download=true", dest, key=key)
                    if _digest(dest) != hashes[f]:
                        dest.unlink(missing_ok=True)
                        raise IOError(f"integrity check failed for {f}")
                finally:
                    _progress(key, finished=True)
            (d / ".complete").write_text(json.dumps({"version": APP_VERSION, "sha256": hashes}, sort_keys=True))
            _publish(error="")
            log.info("models: %s ready at %s", repo, d)
            return d
        except Exception as e:
            _publish(error=f"{repo}: {str(e)[:120]}")
            log.warning("models: %s download failed (%s)", repo, e)
            raise
