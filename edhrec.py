"""Helpers for discovering EDHREC commander and average-deck URLs."""

from __future__ import annotations

import re
import time
from typing import Dict, Optional, Set, Tuple
from urllib.parse import quote_plus

import requests

from utils.commander_identity import commander_slug_candidates

UA = "MightstoneBot/1.0 (+https://github.com/Knack117/mtg-mightstone-gpt)"


_ALLOWED_AVERAGE_DECK_PATHS: Tuple[str, ...] = (
    "",
    "exhibition",
    "exhibition/budget",
    "exhibition/expensive",
    "core",
    "upgraded",
    "optimized",
    "cedh",
    "cedh/budget",
    "cedh/expensive",
)

_AVERAGE_DECK_BRACKET_ALIASES = {
    "": "",
    "all": "",
    "average": "",
    "default": "",
    "exhibition": "exhibition",
    "precon": "exhibition",
    "core": "core",
    "upgraded": "upgraded",
    "optimized": "optimized",
    "cedh": "cedh",
    "1": "exhibition",
    "2": "core",
    "3": "upgraded",
    "4": "optimized",
    "5": "cedh",
    "exhibition/budget": "exhibition/budget",
    "exhibition-budget": "exhibition/budget",
    "exhibition/expensive": "exhibition/expensive",
    "exhibition-expensive": "exhibition/expensive",
    "cedh/budget": "cedh/budget",
    "cedh-budget": "cedh/budget",
    "cedh/expensive": "cedh/expensive",
    "cedh-expensive": "cedh/expensive",
}
def _get(session: requests.Session, url: str, retries: int = 2) -> requests.Response:
    """Perform a GET with lightweight retry handling."""

    last: Optional[requests.Response] = None
    for attempt in range(retries + 1):
        response = session.get(url, headers={"User-Agent": UA}, timeout=15)
        if response.status_code in (429, 503) and attempt < retries:
            time.sleep(0.8 * (attempt + 1))
            last = response
            continue
        response.raise_for_status()
        return response
    assert last is not None
    last.raise_for_status()
    return last  # pragma: no cover - raise_for_status will always raise


def _fetch_html(session: requests.Session, url: str) -> str:
    return _get(session, url).text


def _find_commander_page(session: requests.Session, name: str) -> Optional[str]:
    """Return the EDHREC commander page for *name* if one exists."""

    for slug in commander_slug_candidates(name or ""):
        if not slug:
            continue
        url = f"https://edhrec.com/commanders/{slug}"
        response = session.get(url, headers={"User-Agent": UA}, timeout=15)
        if response.status_code == 200:
            return url

    query = quote_plus(name or "")
    search_url = f"https://edhrec.com/search?q={query}"
    html = _fetch_html(session, search_url)
    match = re.search(r'href="(/commanders/[a-z0-9\-]+)"', html)
    return f"https://edhrec.com{match.group(1)}" if match else None


_AVERAGE_DECK_PATH_RE = re.compile(
    r"^/average-decks/([a-z0-9\-]+)(?:/([a-z0-9\-]+)(?:/([a-z0-9\-]+))?)?$"
)


def _pick_avg_link(html: str, bracket: str) -> Optional[Dict[str, Set[str] | Optional[str]]]:
    links = re.findall(r'href="(/average-decks/[a-z0-9\-]+(?:/[a-z0-9\-]+){0,2})"', html)
    links = list(dict.fromkeys(links))
    if not links:
        return None

    normalized_links: list[Tuple[str, str]] = []
    buckets: Set[str] = set()

    for path in links:
        match = _AVERAGE_DECK_PATH_RE.match(path)
        if not match:
            continue
        bracket_parts = [part for part in match.groups()[1:] if part]
        raw_bracket = "/".join(bracket_parts)
        normalized = _coerce_average_deck_bracket(raw_bracket)
        if normalized is None:
            continue
        normalized_links.append((path, normalized))
        buckets.add(display_average_deck_bracket(normalized))

    if not normalized_links:
        return None

    for path, normalized in normalized_links:
        if normalized == bracket:
            return {
                "url": f"https://edhrec.com{path}",
                "available": buckets,
            }

    return {"url": None, "available": buckets}


def find_average_deck_url(
    session: requests.Session, name: str, bracket: str
) -> Dict[str, Set[str] | str]:
    """Discover the EDHREC average-decks URL for *(name, bracket)*."""

    if not name or not (name.strip()):
        raise ValueError({"code": "NAME_REQUIRED", "message": "Commander name is required"})

    if bracket is None or not bracket.strip():
        raise ValueError({"code": "BRACKET_REQUIRED", "message": "Bracket is required"})

    normalized_bracket = normalize_average_deck_bracket(bracket)

    commander_url = _find_commander_page(session, name)
    if commander_url:
        html = _fetch_html(session, commander_url)
        picked = _pick_avg_link(html, normalized_bracket)
        if picked and picked["url"]:
            return {
                "source_url": picked["url"],
                "available_brackets": picked["available"],
            }
        if picked and not picked["url"]:
            raise ValueError(
                {
                    "code": "BRACKET_UNAVAILABLE",
                    "message": f"Bracket '{bracket}' not found for '{name}'",
                    "available_brackets": sorted(picked["available"]),
                    "commander_url": commander_url,
                }
            )

    for slug in commander_slug_candidates(name or ""):
        if not slug:
            continue
        if normalized_bracket:
            url = f"https://edhrec.com/average-decks/{slug}/{normalized_bracket}"
        else:
            url = f"https://edhrec.com/average-decks/{slug}"
        response = session.get(url, headers={"User-Agent": UA}, timeout=15)
        if response.status_code == 200:
            return {
                "source_url": url,
                "available_brackets": {display_average_deck_bracket(normalized_bracket)},
            }

    query = quote_plus(name or "")
    search_url = f"https://edhrec.com/search?q={query}"
    html = _fetch_html(session, search_url)
    match = re.search(r'href="(/average-decks/[a-z0-9\-]+(?:/[a-z0-9\-]+){1,2})"', html)
    if match:
        path = match.group(1)
        match_path = _AVERAGE_DECK_PATH_RE.match(path)
        if match_path:
            bracket_parts = [part for part in match_path.groups()[1:] if part]
            normalized = _coerce_average_deck_bracket("/".join(bracket_parts))
        else:
            normalized = None
        url = f"https://edhrec.com{path}"
        return {
            "source_url": url,
            "available_brackets": {
                display_average_deck_bracket(normalized) if normalized is not None else bracket
            },
        }

    raise ValueError(
        {
            "code": "NOT_FOUND",
            "message": f"Could not resolve average-decks URL for '{name}'",
            "hints": [
                "Check spelling/front-face name",
                "Pair may be too new or not indexed",
            ],
        }
    )


def display_average_deck_bracket(path: str) -> str:
    return "all" if not path else path


def allowed_average_deck_brackets() -> Tuple[str, ...]:
    """Return the supported average-deck bracket identifiers."""

    return tuple(display_average_deck_bracket(path) for path in _ALLOWED_AVERAGE_DECK_PATHS)


def normalize_average_deck_bracket(bracket: Optional[str]) -> str:
    """Return the normalized EDHREC average-deck bracket path."""

    text = (bracket or "").strip().lower()
    text = text.replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    text = text.strip("/")

    if not text:
        return ""

    normalized = _AVERAGE_DECK_BRACKET_ALIASES.get(text, text)
    if normalized in _ALLOWED_AVERAGE_DECK_PATHS:
        return normalized

    raise ValueError(
        {
            "code": "BRACKET_UNSUPPORTED",
            "message": f"Bracket '{bracket}' is not supported",
            "allowed_brackets": allowed_average_deck_brackets(),
        }
    )


def _coerce_average_deck_bracket(bracket: Optional[str]) -> Optional[str]:
    try:
        return normalize_average_deck_bracket(bracket)
    except ValueError:
        return None


__all__ = [
    "find_average_deck_url",
    "allowed_average_deck_brackets",
    "display_average_deck_bracket",
    "normalize_average_deck_bracket",
]

