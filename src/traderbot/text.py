"""Text that reaches a terminal or a prompt: `shown` escapes what could act or hide.

Its own module so the CLI (`cli.shown`) and the prompt fence share one rule without a cycle.
"""

# Default ignorable code points that isprintable() passes (letters and marks that render as
# nothing): the combining grapheme joiner, Hangul fillers, Khmer inherent vowels, Mongolian free
# variation selectors and variation selectors 1 to 256. The format ones (Cf) are escaped already.
INVISIBLE = (
    (0x034F, 0x034F),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFFA0, 0xFFA0),
    (0xE0100, 0xE01EF),
)


def _invisible(ch: str) -> bool:
    point = ord(ch)
    return any(low <= point <= high for low, high in INVISIBLE)


def shown(text: str) -> str:
    """Model or user text for the terminal: line breaks, control, format and invisible characters
    print as escapes, so text can neither drive the terminal nor forge a line of output."""
    return "".join(
        ch if ch.isprintable() and not _invisible(ch) else ch.encode("unicode_escape").decode()
        for ch in text
    )
