import json
import os
import pathlib
import threading



def _resolve_data_dir():
    override = os.environ.get("MC_DATA_DIR")
    if override:
        return pathlib.Path(override).expanduser()
    base = pathlib.Path(os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share")
    target = base / "deban"
    legacy = base / "deban-oss"
    if legacy.is_dir() and not legacy.is_symlink() and not target.exists():
        import socket
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", int(os.environ.get("MC_PORT", "8765")))) == 0:
                raise RuntimeError("Stop the running Deban instance before migrating the data directory.")
        legacy.rename(target)
        legacy.symlink_to(target, target_is_directory=True)
    return target


DATA_DIR = _resolve_data_dir()


DEFAULTS = {
    "desktop_onboarded": False,
    "port": 8765,
    "src_lang": "ja",
    "out_lang": "zh",
    "max_spk": 0,
    "record_audio": True,
    "translate_default": True,
    "mic_enabled": True,
    "spk_threshold": 0.45,
    "self_name": "",
    "ui_lang": "",
    "my_names": [],
    "alerts_enabled": True,
    "alert_respond": True, "alert_consider": True, "alert_contribute": True, "alert_ask": True,
    "respond_threshold": 0.75,
    "consider_threshold": 0.60,
    "contribute_threshold": 0.80,
    "ask_threshold": 0.6,
    "contribute_max_per_meeting": 4,
    "ask_max_per_meeting": 1,
    "scan_interval_sec": 30,
    "topic_pause_sec": 20,
    "topic_max_chars": 400,
    "auto_end_min": 10,
}

_lock = threading.Lock()
_cfg = None


def _config_path():
    return DATA_DIR / "config.json"


def ensure_dirs():
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for d in ("audio", "speakers", "contexts", "exports", "models"):
        (DATA_DIR / d).mkdir(parents=True, exist_ok=True)


def load():
    global _cfg
    with _lock:
        if _cfg is None:
            ensure_dirs()
            cfg = dict(DEFAULTS)
            try:
                cfg.update({k: v for k, v in json.loads(_config_path().read_text()).items() if k in DEFAULTS})
            except Exception:
                sl = _system_lang()
                if sl:
                    cfg["src_lang"] = "zh" if sl.startswith("zh") else sl
                    cfg["out_lang"] = sl
            for k in DEFAULTS:
                env = os.environ.get("MC_" + k.upper())
                if env is None:
                    continue
                if isinstance(DEFAULTS[k], bool):
                    cfg[k] = env == "1"
                elif isinstance(DEFAULTS[k], list):
                    cfg[k] = [x.strip() for x in env.split(",") if x.strip()]
                else:
                    cfg[k] = type(DEFAULTS[k])(env)
            if cfg.get("src_lang") == "auto":
                sl = _system_lang()
                cfg["src_lang"] = "zh" if (sl or "").startswith("zh") else sl if sl in {"ja", "en"} else "ja"
            _cfg = cfg
        return _cfg


def _system_lang():
    import locale
    language = (locale.getlocale()[0] or "").lower()
    if language.startswith("zh"):
        return "zh-Hant" if any(t in language for t in ("tw", "hk", "mo", "hant")) else "zh"
    if language.startswith(("ja", "en")):
        return language[:2]
    return None


def get(key):
    return load()[key]


_ENUMS = {"src_lang": {"ja", "en", "zh"}, "out_lang": {"zh", "zh-Hant", "ja", "en"},
          "ui_lang": {"", "ja", "en", "zh", "zh-Hant"}}


def _validate(k, v):
    d = DEFAULTS[k]
    try:
        if isinstance(d, bool):
            return bool(v)
        if isinstance(d, int):
            return int(v)
        if isinstance(d, float):
            v = float(v)
            return min(1.0, max(0.0, v)) if k.endswith("_threshold") else v
        if isinstance(d, list):
            return [str(x).strip()[:30] for x in v if str(x).strip()][:50] if isinstance(v, list) else None
        v = str(v).strip()
        if k in _ENUMS and v not in _ENUMS[k]:
            return None
        return v[:100]
    except Exception:
        return None


def update(patch: dict):
    cfg = load()
    with _lock:
        try:
            disk = {k: v for k, v in json.loads(_config_path().read_text()).items() if k in DEFAULTS}
        except Exception:
            disk = {}
        for k, v in patch.items():
            if k not in DEFAULTS or v is None:
                continue
            v = _validate(k, v)
            if v is None:
                continue
            cfg[k] = v
            disk[k] = v
        _config_path().write_text(json.dumps(disk, ensure_ascii=True, indent=2), encoding="utf-8")
    return cfg


_ANON_LABEL = {"ja": "話者{}", "zh": "发言人{}", "zh-Hant": "發言人{}", "en": "Speaker {}"}


def anon_label(n):
    return _ANON_LABEL.get(get("ui_lang") or _system_lang() or "en", _ANON_LABEL["en"]).format(n)
