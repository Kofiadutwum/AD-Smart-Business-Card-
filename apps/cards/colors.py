"""Keep any colour a subscriber picks readable (Section 12, NFR-06).

Professional and Business owners can choose any accent colour. These helpers
derive the text colours the card actually uses so every pairing reaches
WCAG AA (4.5:1) in both light and dark appearance.
"""

import re

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
LIGHT_BG = "#ffffff"
DARK_BG = "#15161a"


def valid_hex(value):
    return bool(value and HEX_RE.match(value))


def _rgb(hex_value):
    hex_value = hex_value.lstrip("#")
    return tuple(int(hex_value[i : i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def luminance(hex_value):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(hex_value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mix(hex_value, target, amount):
    a, b = _rgb(hex_value), _rgb(target)
    return _hex(tuple(x + (y - x) * amount for x, y in zip(a, b)))


def readable_against(colour, background, minimum=4.5):
    """Darken or lighten ``colour`` just enough to read on ``background``."""
    if contrast(colour, background) >= minimum:
        return colour
    target = "#000000" if luminance(background) > 0.5 else "#ffffff"
    for step in range(1, 21):
        candidate = _mix(colour, target, step * 0.05)
        if contrast(candidate, background) >= minimum:
            return candidate
    return target


def on_colour(background):
    """White or near-black text, whichever reads better on ``background``."""
    return "#ffffff" if contrast("#ffffff", background) >= contrast("#15171d", background) else "#15171d"


def palette(accent, card_colour=""):
    accent = accent if valid_hex(accent) else "#0048a9"
    band = card_colour if valid_hex(card_colour) else accent
    return {
        "accent": accent,
        "on_accent": on_colour(accent),
        "accent_text_light": readable_against(accent, LIGHT_BG),
        "accent_text_dark": readable_against(accent, DARK_BG),
        "band": band,
        "on_band": on_colour(band),
    }
