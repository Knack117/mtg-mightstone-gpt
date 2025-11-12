import re
import unicodedata
from typing import List, Tuple

WUBRG_ORDER = "wubrg"


def to_ascii(s: str) -> str:
    """Best-effort ASCII folding (drops diacritics and unsupported chars)."""

    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _slugify_piece(value: str) -> str:
    piece = to_ascii(value.lower())
    piece = re.sub(r"[’'`]", "", piece)
    piece = re.sub(r"[^a-z0-9]+", "-", piece)
    piece = re.sub(r"-{2,}", "-", piece).strip("-")
    return piece


def _split_commander_variants(name: str) -> List[str]:
    raw = name.strip()
    if not raw:
        return []

    if "//" not in raw:
        base = re.split(r"\s*\|\s*", raw)[0]
        return [base.strip()]

    parts = [segment.strip() for segment in re.split(r"\s*//\s*", raw) if segment.strip()]
    normalized: List[str] = []
    banned = {"back", "backside"}
    for segment in parts:
        primary = re.split(r"\s*\|\s*", segment)[0].strip()
        if primary and primary.lower() not in banned:
            normalized.append(primary)

    return normalized or [re.split(r"\s*\|\s*", raw)[0].strip()]


def commander_slug_candidates(name: str) -> List[str]:
    """Return slug candidates for a commander name (partners + fallbacks)."""

    candidates: List[str] = []
    pieces = _split_commander_variants(name)
    if not pieces:
        return candidates

    combined = "-".join(filter(None, (_slugify_piece(piece) for piece in pieces)))
    if combined:
        candidates.append(combined)

    first_piece = _slugify_piece(pieces[0])
    if first_piece and first_piece not in candidates:
        candidates.append(first_piece)

    return candidates


def commander_to_slug(name: str) -> str:
    """
    Canonicalize a commander name into an EDHREC slug.

    Examples:
      "Avatar Aang" -> "avatar-aang"
      "K’rrik, Son of Yawgmoth" -> "krrik-son-of-yawgmoth"
      "Eruth, Tormented Prophet // Back" -> "eruth-tormented-prophet"
      "Donatello, the Brains // Michelangelo, the Heart" ->
        "donatello-the-brains-michelangelo-the-heart"
    """

    candidates = commander_slug_candidates(name)
    return candidates[0] if candidates else ""


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
