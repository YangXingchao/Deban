import re
import time

from .. import config
from ..context import profile, glossary

QUESTION_RE = re.compile(
    r"[?？]$|ですか|ますか|でしょうか|かな[?？。]?$|(う|る|た|の|ない|です|ます)か[?？。]?$|(です|ます)?よね[?？。]?$|でしょ[?？]$|どう(思|です|でしょう)|いかが|"
    r"教えて|確認(できます|お願い)|よろしいですか|大丈夫ですか|"
    r"\b(what|how|why|when|could you|can you|do you|are you|right\?)\b|"
    r"(怎么看|觉得呢|可以吗|行吗|对吧|如何)", re.IGNORECASE)

HANDOFF_RE = re.compile(r"お願いします$|どうぞ|お任せ|いかがでしょうか$")

INVITE_RE = re.compile(
    r"質問(は|が)?(あります|ございます)|何かあります|ご意見|コメント(は|が)?あ|"
    r"(懸念|意見|気になる点|不明点|確認したいこと)(は|が)?(あります|ございます)|他に.{0,8}(あります|ございます)か|"
    r"(其他|还有).{0,4}(问题|疑问)|有什么问题|any (questions|comments)|questions\?", re.IGNORECASE)


HONORIFICS = r"(さん|サン|くん|君|様|先生|氏|老师|老板|同学|哥|姐|总|女士|博士|教授)"


def kana_fold(s):
    return "".join(chr(ord(c) + 0x60) if "\u3041" <= c <= "\u3096" else c for c in s or "")


def name_regex():
    parts = []
    for n in config.get("my_names") or []:
        n = kana_fold(n.strip())
        if not n:
            continue
        if re.fullmatch(r"[A-Za-z .'\-]+", n):
            parts.append(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])")
        elif len(n) <= 2:
            parts.append(re.escape(n) + r"\s*" + HONORIFICS)
        else:
            parts.append(re.escape(n))
    return re.compile("|".join(parts) if parts else r"(?!x)x", re.IGNORECASE)


HONORIFIC_TOKEN_RE = re.compile(r"([\u30a1-\u30fa\u3041-\u3096\u4e00-\u9fffA-Za-z]{1,6})\s*(さん|サン|くん|君|様|先生|氏|老师|老板|同学|博士|教授)")
PROBLEM_RE = re.compile(
    r"うまくいか|うまく行か|原因が(よく)?分か|わからなく|分からなく|不安定|崩れ|発散|おかしい|バグ|問題[がもはを]|課題[がもはを]|問題点|困って|どうすれば|(見当|検討)がつか|"
    r"不知道(为什么|原因)|搞不清|出问题|不稳定|崩了|报错|卡住|"
    r"not sure why|no idea|doesn't work|isn't working|unstable|diverg|broken|stuck", re.IGNORECASE)


_ROMAJI = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o", "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so", "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no", "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo", "ヤ": "ya", "ユ": "yu", "ヨ": "yo", "ラ": "ra", "リ": "ri",
    "ル": "ru", "レ": "re", "ロ": "ro", "ワ": "wa", "ヲ": "o", "ン": "n", "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo", "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo", "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "ヴ": "vu", "ー": "-", "ッ": "",
}
_SMALL = {"ャ": "ya", "ュ": "yu", "ョ": "yo", "ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o"}


def romaji(kata):
    out = []
    for c in kata:
        if c in _SMALL and out:
            prev = out[-1]
            out[-1] = (prev[:-1] if prev and prev[-1] in "iu" and len(prev) > 1 else prev) + _SMALL[c]
        else:
            out.append(_ROMAJI.get(c, c))
    return "".join(out)


def fuzzy_name_hit(text, known_names=()):
    import difflib
    names = [kana_fold(n.strip()) for n in (config.get("my_names") or []) if n.strip() and not re.fullmatch(r"[A-Za-z .'\-]+", n)]
    if not names:
        return None
    known = {kana_fold(k) for k in known_names or ()}
    for m in HONORIFIC_TOKEN_RE.finditer(kana_fold(text or "")):
        tok = m.group(1)
        if any(tok == n or tok.endswith(n) or n.endswith(tok) and len(tok) >= 2 for n in names):
            continue
        if any(tok == k or k.endswith(tok) for k in known):
            continue
        kata = r"[\u30a1-\u30fa\u30fc]+"
        for n in names:
            if not (re.fullmatch(kata, n) and len(n) >= 2):
                continue
            rn = romaji(n)
            for L in sorted({len(tok), len(n), len(n) + 1}, reverse=True):
                if L < 2 or L > len(tok):
                    continue
                sub = tok[-L:]
                if not re.fullmatch(kata, sub) or any(sub == k or k.endswith(sub) for k in known):
                    continue
                if difflib.SequenceMatcher(None, romaji(sub), rn).ratio() >= (0.5 if L == len(tok) else 0.6):
                    return sub
    return None


class RuleEngine:
    def __init__(self):
        self.last_intervention_at = 0.0
        self.last_self_spoke_at = 0.0

    @staticmethod
    def name_in(text):
        return bool(name_regex().search(kana_fold(text or "")))

    def features(self, text, spk_id, lang, known_names=()):
        kw = [k for k in (profile.keywords() + glossary.load()) if k and k.lower() in text.lower()]
        return {
            "name_hit": bool(name_regex().search(kana_fold(text))),
            "question": bool(QUESTION_RE.search(text.strip())),
            "handoff": bool(HANDOFF_RE.search(text.strip())),
            "keywords": kw,
            "followup": (time.time() - self.last_self_spoke_at) < 45,
            "invite": bool(INVITE_RE.search(text.strip())),
            "name_fuzzy": fuzzy_name_hit(text, known_names),
            "problem": bool(PROBLEM_RE.search(text)),
        }

    def candidate(self, feats):
        if feats["name_hit"]:
            return True, "name"
        if feats.get("name_fuzzy"):
            return True, "name_fuzzy"
        if feats["question"] and feats["keywords"]:
            return True, "question+keyword"
        if feats["question"] and feats["followup"]:
            return True, "question+followup"
        if feats["handoff"] and feats["followup"]:
            return True, "handoff"
        if feats["invite"]:
            return True, "ask_invite"
        return False, ""

    def note_self_spoke(self):
        self.last_self_spoke_at = time.time()
