from .. import config


def _path():
    return config.DATA_DIR / "glossary.txt"


def load():
    try:
        return [l.strip() for l in _path().read_text().splitlines() if l.strip()]
    except Exception:
        return []


def save(terms):
    clean = []
    for t in terms:
        t = t.strip()[:40]
        if t and t not in clean:
            clean.append(t)
    _path().write_text("\n".join(clean))
    return clean
