"""OCR language normalization helpers.

These helpers translate friendly user input such as ``chinese`` or ``中文``
into Tesseract language codes such as ``chi_sim``.
"""

from __future__ import annotations

import re
from typing import List


_ALIAS_GROUPS = {
    "eng": ("eng", "en", "english", "英文", "英语"),
    "deu": ("deu", "de", "german", "deutsch", "德语", "德文"),
    "fra": ("fra", "fr", "french", "francais", "français", "法语", "法文"),
    "spa": ("spa", "es", "spanish", "espanol", "español", "西班牙语"),
    "ita": ("ita", "it", "italian", "意大利语"),
    "por": ("por", "pt", "portuguese", "葡萄牙语"),
    "nld": ("nld", "nl", "dutch", "荷兰语"),
    "pol": ("pol", "pl", "polish", "波兰语"),
    "rus": ("rus", "ru", "russian", "俄语"),
    "chi_sim": (
        "chi sim",
        "chi_sim",
        "chi-sim",
        "zh",
        "zh_cn",
        "zh-cn",
        "cn",
        "chinese",
        "simplifiedchinese",
        "chinesesimplified",
        "simplified chinese",
        "chinese simplified",
        "中文",
        "汉语",
        "汉字",
        "简体",
        "简体中文",
        "简中",
        "普通话",
    ),
    "chi_tra": (
        "chi tra",
        "chi_tra",
        "chi-tra",
        "zh_tw",
        "zh-tw",
        "zh_hant",
        "zh-hant",
        "traditionalchinese",
        "chinesetraditional",
        "traditional chinese",
        "chinese traditional",
        "繁体",
        "繁體",
        "繁体中文",
        "繁體中文",
        "繁中",
    ),
    "jpn": ("jpn", "ja", "jp", "japanese", "日语", "日文", "日本語"),
    "kor": ("kor", "ko", "kr", "korean", "韩语", "韓語", "한국어"),
    "ara": ("ara", "ar", "arabic", "阿拉伯语"),
    "hin": ("hin", "hi", "hindi", "印地语"),
    "tur": ("tur", "tr", "turkish", "土耳其语"),
    "vie": ("vie", "vi", "vietnamese", "越南语"),
}


def _normalize_alias_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


_ALIASES_BY_KEY = {
    _normalize_alias_key(alias): code
    for code, aliases in _ALIAS_GROUPS.items()
    for alias in aliases
}

_SPLIT_RE = re.compile(r"\s*(?:\+|,|;|/|\||、|，|\band\b|\bplus\b|和|与)\s*", re.IGNORECASE)


def _normalize_part(part: str) -> str:
    raw = part.strip().strip("'\"")
    if not raw:
        return ""

    alias_key = _normalize_alias_key(raw)
    if alias_key in _ALIASES_BY_KEY:
        return _ALIASES_BY_KEY[alias_key]

    return raw.lower()


def normalize_ocr_lang(value: str | None, default: str = "eng") -> str:
    """Normalize friendly OCR language input to Tesseract codes.

    Examples:
        ``"chinese"`` -> ``"chi_sim"``
        ``"中文+english"`` -> ``"chi_sim+eng"``
        ``"zh-cn, en"`` -> ``"chi_sim+eng"``
    """
    if value is None:
        return default

    raw = value.strip()
    if not raw:
        return default

    parts = _SPLIT_RE.split(raw)
    normalized: List[str] = []

    for part in parts:
        code = _normalize_part(part)
        if not code:
            continue
        if code not in normalized:
            normalized.append(code)

    return "+".join(normalized) if normalized else default


__all__ = ["normalize_ocr_lang"]
