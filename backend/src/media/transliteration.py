"""Phonetic transliteration of Devanagari Hindi into readable Roman Hinglish.
Ported from Dispatch cognitive engine for zero-dependency, 100% deterministic transliteration.
"""

import re

DEVANAGARI_REGEX = re.compile(r"[\u0900-\u097F]")


def has_devanagari(text: str) -> bool:
    """Check if string contains any Devanagari Hindi characters."""
    if not text:
        return False
    return bool(DEVANAGARI_REGEX.search(text))


_DEVANAGARI_MAP = {
    # Signs / Modifiers
    "\u0900": "n",   # Inverted Chandrabindu
    "\u0901": "n",   # Chandrabindu
    "\u0902": "n",   # Anusvara
    "\u0903": "h",   # Visarga

    # Independent vowels
    "\u0904": "a",
    "\u0905": "a",
    "\u0906": "aa",
    "\u0907": "i",
    "\u0908": "ee",
    "\u0909": "u",
    "\u090a": "oo",
    "\u090b": "ri",
    "\u090c": "li",
    "\u090d": "e",
    "\u090e": "e",
    "\u090f": "e",
    "\u0910": "ai",
    "\u0911": "o",
    "\u0912": "o",
    "\u0913": "o",
    "\u0914": "au",

    # Consonants
    "\u0915": "k", "\u0916": "kh", "\u0917": "g", "\u0918": "gh", "\u0919": "ng",
    "\u091a": "ch", "\u091b": "chh", "\u091c": "j", "\u091d": "jh", "\u091e": "ny",
    "\u091f": "t", "\u0920": "th", "\u0921": "d", "\u0922": "dh", "\u0923": "n",
    "\u0924": "t", "\u0925": "th", "\u0926": "d", "\u0927": "dh", "\u0928": "n",
    "\u0929": "n",
    "\u092a": "p", "\u092b": "ph", "\u092c": "b", "\u092d": "bh", "\u092e": "m",
    "\u092f": "y", "\u0930": "r", "\u0931": "r",
    "\u0932": "l", "\u0933": "l", "\u0934": "l",
    "\u0935": "v", "\u0936": "sh", "\u0937": "sh", "\u0938": "s", "\u0939": "h",

    # Additional signs & vowels
    "\u093a": "oe",
    "\u093b": "ooe",
    "\u093c": "",    # Nukta
    "\u093d": "'",   # Avagraha

    # Dependent vowel signs (matras)
    "\u093e": "aa", "\u093f": "i", "\u0940": "ee", "\u0941": "u", "\u0942": "oo",
    "\u0943": "ri", "\u0944": "ri", "\u0945": "e", "\u0946": "e", "\u0947": "e",
    "\u0948": "ai", "\u0949": "o", "\u094a": "o", "\u094b": "o", "\u094c": "au",
    "\u094e": "e", "\u094f": "aw",

    # Om & Accents
    "\u0950": "Om",
    "\u0951": "", "\u0952": "", "\u0953": "", "\u0954": "",
    "\u0955": "e", "\u0956": "u", "\u0957": "oo",

    # Nukta consonants
    "\u0958": "q", "\u0959": "kh", "\u095a": "g", "\u095b": "z", "\u095c": "r",
    "\u095d": "rh", "\u095e": "f", "\u095f": "y",

    # Additional Sanskrit vowels
    "\u0960": "ri", "\u0961": "li", "\u0962": "li", "\u0963": "li",

    # Danda & Punctuation
    "\u0964": ".", "\u0965": ".",

    # Digits
    "\u0966": "0", "\u0967": "1", "\u0968": "2", "\u0969": "3", "\u096a": "4",
    "\u096b": "5", "\u096c": "6", "\u096d": "7", "\u096e": "8", "\u096f": "9",

    # Devanagari extensions
    "\u0970": ".", "\u0971": "", "\u0972": "a", "\u0973": "oe", "\u0974": "ooe",
    "\u0975": "aw", "\u0976": "ue", "\u0977": "uue", "\u0978": "d", "\u0979": "z",
    "\u097a": "y", "\u097b": "g", "\u097c": "j", "\u097d": "", "\u097e": "d",
    "\u097f": "b",
}

_NUKTA_REPLACE = {
    "\u0915\u093c": "\u0958",
    "\u0916\u093c": "\u0959",
    "\u0917\u093c": "\u095a",
    "\u091c\u093c": "\u095b",
    "\u0921\u093c": "\u095c",
    "\u0922\u093c": "\u095d",
    "\u0928\u093c": "\u0929",
    "\u092b\u093c": "\u095e",
    "\u092f\u093c": "\u095f",
    "\u0930\u093c": "\u0931",
    "\u0933\u093c": "\u0934",
}

_HALANT = "\u094d"
_CONSONANTS = set(
    "\u0915\u0916\u0917\u0918\u0919"
    "\u091a\u091b\u091c\u091d\u091e"
    "\u091f\u0920\u0921\u0922\u0923"
    "\u0924\u0925\u0926\u0927\u0928\u0929"
    "\u092a\u092b\u092c\u092d\u092e"
    "\u092f\u0930\u0931\u0932\u0933\u0934\u0935\u0936\u0937\u0938\u0939"
    "\u0958\u0959\u095a\u095b\u095c\u095d\u095e\u095f"
    "\u0978\u0979\u097a\u097b\u097c\u097e\u097f"
)
_MATRAS = set(
    "\u093e\u093f\u0940\u0941\u0942\u0943\u0944\u0945\u0946\u0947\u0948\u0949\u094a\u094b\u094c"
    "\u094e\u094f\u0955\u0956\u0957\u0962\u0963"
)
_WORD_DELIMITERS = set("\u0964\u0965.,!?;:'\"()[]{}-_/\r\n\t")


def phonetic_devanagari_to_roman(text: str) -> str:
    """Deterministic phonetic transliteration of Devanagari Hindi into readable Roman Hinglish.
    Exhaustively handles all Devanagari codepoints (U+0900..U+097F) with zero unmapped characters."""
    if not text:
        return ""

    # Normalize decomposed nukta sequences
    for comp, precomp in _NUKTA_REPLACE.items():
        text = text.replace(comp, precomp)
    # Strip any remaining standalone nuktas
    text = text.replace("\u093c", "")

    result = []
    chars = list(text)
    n = len(chars)
    for i, ch in enumerate(chars):
        if ch in _CONSONANTS:
            base = _DEVANAGARI_MAP.get(ch, "")
            next_ch = chars[i + 1] if i + 1 < n else None
            if next_ch == _HALANT:
                result.append(base)
            elif next_ch in _MATRAS:
                result.append(base)
            elif next_ch and next_ch in _CONSONANTS:
                result.append(base + "a")
            elif next_ch in ("\u0901", "\u0902"):
                result.append(base + "a")
            elif next_ch is None or next_ch.isspace() or next_ch in _WORD_DELIMITERS:
                result.append(base)
            else:
                result.append(base + "a")
        elif ch in _DEVANAGARI_MAP:
            result.append(_DEVANAGARI_MAP[ch])
        elif ch == _HALANT:
            pass
        elif 0x0900 <= ord(ch) <= 0x097F:
            # Drop unmapped Devanagari codepoints
            pass
        else:
            result.append(ch)
    return "".join(result)
