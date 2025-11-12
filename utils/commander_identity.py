import re
import unicodedata
from typing import Tuple

WUBRG_ORDER = "wubrg"


def to_ascii(s: str) -> str:
    """Best-effort ASCII folding (drops diacritics and unsupported chars)."""

    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def commander_to_slug(name: str) -> str:
    """
    Canonicalize a commander name into an EDHREC slug.

    Examples:
      "Avatar Aang" -> "avatar-aang"
      "K’rrik, Son of Yawgmoth" -> "krrik-son-of-yawgmoth"
      "Eruth, Tormented Prophet // Back" -> "eruth-tormented-prophet"
    """

    base = re.split(r"\s*//\s*|\s*\|\s*", name.strip())[0]
    s = to_ascii(base.lower())
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def slugify_commander(name: str) -> str:
    """Backward-compatible alias for commander slugification."""

    return commander_to_slug(name)


def normalize_commander_name(name: str) -> Tuple[str, str, str]:
    """Returns (display_name, slug, edhrec_url)"""
    display = name.strip()
    slug = commander_to_slug(display)
    url = f"https://edhrec.com/commanders/{slug}"
    return display, slug, url


def sort_wubrg(letters: str) -> str:
    letters = "".join(sorted(set([c for c in letters.lower() if c in WUBRG_ORDER]),
                             key=WUBRG_ORDER.index))
    return letters
