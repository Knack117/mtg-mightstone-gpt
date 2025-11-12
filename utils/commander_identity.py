import re
import unicodedata
from typing import Tuple

WUBRG_ORDER = "wubrg"


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def slugify_commander(name: str) -> str:
    # Take the front face/name for MDFCs or “A // B” partner-style inputs
    name = re.split(r"\s*//\s*|\s*\|\s*", name.strip())[0]
    # Normalize punctuation/diacritics, drop set-specific stuff
    s = _ascii(name.lower())
    s = re.sub(r"[’'`]", "", s)           # apostrophes
    s = re.sub(r"[^a-z0-9]+", "-", s)     # non-alnum → dash
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s  # e.g., "chulane-teller-of-tales"


def normalize_commander_name(name: str) -> Tuple[str, str, str]:
    """Returns (display_name, slug, edhrec_url)"""
    display = name.strip()
    slug = slugify_commander(display)
    url = f"https://edhrec.com/commanders/{slug}"
    return display, slug, url


def sort_wubrg(letters: str) -> str:
    letters = "".join(sorted(set([c for c in letters.lower() if c in WUBRG_ORDER]),
                             key=WUBRG_ORDER.index))
    return letters
