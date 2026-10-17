"""Text helpers every module may use. Pure."""

import unicodedata


def fold(text: str) -> str:
    """Lower case without accents, one character for each character, so an offset in the folded text is an
    offset in the text: a character that folds to more than one (`ß`) becomes `?`."""
    if text.isascii():
        return text.lower()
    out = []
    for ch in text:
        plain = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).lower()
        out.append(plain if len(plain) == 1 else "?")
    return "".join(out)


def pieces(text: str, size: int, overlap: int) -> list[str]:
    """`text` in pieces of at most `size` characters that together hold all of it, each one starting
    `overlap` characters before the end of the one before, cut at a space when there is one nearby. Nothing
    is left out: a reader with a size limit reads every piece."""
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            space = text.rfind(" ", start + size // 2, end)
            end = space if space > start else end
        out.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out
