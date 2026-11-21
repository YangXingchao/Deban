import yaml

from .. import config

DEFAULT = {
    "name": "",
    "role": "",
    "languages": [],
    "expertise": [],
    "responsibilities": [],
    "style": {},
    "keywords_ext": {},
}


def _path():
    return config.DATA_DIR / "profile.yaml"


def load():
    try:
        data = yaml.safe_load(_path().read_text()) or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = dict(DEFAULT)
    out.update({k: v for k, v in data.items() if k in DEFAULT})
    return out


LIMITS = {"name": 30, "role": 60}


def save(data: dict):
    cur = load()
    clean = {}
    for k, v in data.items():
        if k not in DEFAULT:
            continue
        if isinstance(v, str) and k in LIMITS:
            v = v.strip()[:LIMITS[k]]
        elif isinstance(v, list) and k in ("expertise", "responsibilities"):
            v = [str(x).strip()[:30] for x in v if str(x).strip()]
        clean[k] = v
    cur.update(clean)
    _path().write_text(yaml.safe_dump(cur, allow_unicode=True, sort_keys=False))
    return cur


def as_prompt():
    p = load()
    if not any(p.values()):
        return ""
    lines = [f"Name: {p['name']}" if p["name"] else "",
             f"Role: {p['role']}" if p["role"] else "",
             f"Languages: {', '.join(p['languages'])}" if p["languages"] else "",
             f"Expertise: {', '.join(p['expertise'])}" if p["expertise"] else "",
             f"Responsibilities: {', '.join(p['responsibilities'])}" if p["responsibilities"] else ""]
    return "\n".join(l for l in lines if l)


STYLE_HINTS = {"concise": "Concise (1-2 sentences)", "polite": "Polite and formal", "direct": "Direct and clear"}


def bump_style(style: str):
    if style not in STYLE_HINTS:
        return
    p = load()
    st = dict(p.get("style") or {})
    st[style] = int(st.get(style, 0)) + 1
    save({"style": st})


def style_hint():
    st = load().get("style") or {}
    picks = [k for k in ("concise", "polite", "direct") if int(st.get(k, 0)) >= 3]
    return "、".join(STYLE_HINTS[k] for k in picks)


def keywords():
    p = load()
    base = [k for k in (p["expertise"] + p["responsibilities"]) if k]
    ext = p.get("keywords_ext") or {}
    out = list(base)
    for k in base:
        for v in ext.get(k, []) or []:
            if v and v not in out:
                out.append(v)
    return out


EXPAND_SYS = (
    "For each user expertise or responsibility, list 10 to 15 terms and subtopics "
    "that may occur in Japanese, Chinese, or English meetings. Include relevant technical terms "
    "and phrases of at least two characters. Return only JSON mapping each original term to an array of terms."
)


def expand_keywords(llm):
    if not llm or not getattr(llm, "available", False):
        return 0
    p = load()
    terms = [k for k in (p["expertise"] + p["responsibilities"]) if k]
    ext = dict(p.get("keywords_ext") or {})
    missing = [t for t in terms if t not in ext]
    if not missing:
        return 0
    data = llm.chat_json(EXPAND_SYS, "\n".join(missing), max_tokens=900, role="classifier") or {}
    for t in missing:
        vals = data.get(t) or []
        import re as _re
        _cjk = _re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
        def _ok(v):
            v = str(v).strip()
            return bool(v) and v != t and len(v) >= (2 if _cjk.search(v) else 3)
        ext[t] = [str(v).strip() for v in vals if _ok(v)][:15]
    ext = {k: v for k, v in ext.items() if k in terms}
    save({"keywords_ext": ext})
    return len(missing)
