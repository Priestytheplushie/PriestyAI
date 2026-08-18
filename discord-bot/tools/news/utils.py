import re
import unicodedata


def clean_unicode_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("’", "'").replace("“", '"').replace("”", '"').replace("—", "-")

    cleaned_chars = []
    for char in text:
        category = unicodedata.category(char)
        if category[0] in ("L", "N", "P") or category == "Zs":
            cleaned_chars.append(char)

    sanitized = "".join(cleaned_chars)
    return " ".join(sanitized.split()).strip()


def clean_display_name(name: str) -> str:
    if not name:
        return "User"

    name = re.sub(r"^([\[\(\{][a-zA-Z0-9\s_]{2,5}[\]\)\}])\s*", "", name)
    name = re.sub(r"^[A-Z0-9\s]{2,5}\s*[\-\|•~xツ]\s*", "", name)
    name = name.replace("|", " ")
    name = clean_unicode_text(name)

    return name if name else "User"
