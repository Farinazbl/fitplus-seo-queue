"""Persian-aware text normalisation shared by dedup and policy checks."""
import re

_LETTERS = str.maketrans({
    "ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "أ": "ا", "إ": "ا", "ؤ": "و",
})
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DIACRITICS = re.compile(r"[ً-ٰٟـ]")
_URL_MENTION_TAG = re.compile(r"https?://\S+|www\.\S+|@\w+|#\w+", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def fold(text: str) -> str:
    """Unify Arabic/Persian letter variants and digits; keep everything else."""
    t = text.translate(_LETTERS).translate(_DIGITS)
    t = _DIACRITICS.sub("", t)
    t = t.replace("‌", " ")
    return _SPACES.sub(" ", t).strip().lower()


def normalize(text: str) -> str:
    """Aggressive form used for duplicate detection: no links, tags or punctuation."""
    t = _URL_MENTION_TAG.sub(" ", text)
    t = fold(t)
    t = _PUNCT.sub(" ", t)
    return _SPACES.sub(" ", t).strip()
