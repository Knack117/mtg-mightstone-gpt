"""Color identity utilities for Mightstone service."""

from typing import Tuple

WUBRG_ORDER = "wubrg"

SLUG_MAP = {
    "w": "mono-white",
    "u": "mono-blue",
    "b": "mono-black",
    "r": "mono-red",
    "g": "mono-green",
    "wu": "azorius",
    "ub": "dimir",
    "br": "rakdos",
    "rg": "gruul",
    "wg": "selesnya",
    "wb": "orzhov",
    "ur": "izzet",
    "bg": "golgari",
    "wr": "boros",
    "ug": "simic",
    "wub": "esper",
    "ubr": "grixis",
    "brg": "jund",
    "wrg": "naya",
    "wug": "bant",
    "wbg": "abzan",
    "wur": "jeskai",
    "ubg": "sultai",
    "wbr": "mardu",
    "urg": "temur",
    "wubr": "yore-tiller",
    "ubrg": "glint-eye",
    "wbrg": "dune-brood",
    "wurg": "ink-treader",
    "wubg": "witch-maw",
    "wubrg": "five-color",
}

LABEL_TO_CODE = {v.replace("-", " ").title(): k for k, v in SLUG_MAP.items()}
SLUG_TO_CODE = {v: k for k, v in SLUG_MAP.items()}


def _sort_code_letters(raw: str) -> str:
    letters = [c for c in raw if c in WUBRG_ORDER]
    seen = set()
    ordered = []
    for c in WUBRG_ORDER:
        if c in letters and c not in seen:
            ordered.append(c)
            seen.add(c)
    return "".join(ordered)


def canonicalize_identity(value: str) -> Tuple[str, str, str]:
    """Canonicalize an EDH color identity.

    Args:
        value: Color identity expressed as letters ("wur"), label ("Jeskai"), or slug ("jeskai").

    Returns:
        Tuple of (code, label, slug).

    Raises:
        ValueError: If the value cannot be interpreted as a known color identity.
    """

    if not value:
        raise ValueError("Missing color identity")

    s = value.strip().lower()

    if s in SLUG_TO_CODE:
        code = SLUG_TO_CODE[s]
    else:
        label_guess = s.replace("-", " ").title()
        if label_guess in LABEL_TO_CODE:
            code = LABEL_TO_CODE[label_guess]
        else:
            code = _sort_code_letters(s)

    slug = SLUG_MAP.get(code)
    if not slug:
        raise ValueError(f"Unrecognized color identity: {value}")

    label = slug.replace("-", " ").title()
    return code, label, slug
