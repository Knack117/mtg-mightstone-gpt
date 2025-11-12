"""Helpers for discovering EDHREC commander and average-deck URLs."""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Dict, Optional, Set
from urllib.parse import quote_plus

import requests

UA = "MightstoneBot/1.0 (+https://github.com/Knack117/mtg-mightstone-gpt)"


def _strip_accents(text: str) -> str:
    """Return *text* lowercased with accents removed."""

    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _slugify(name: str) -> str:
    """Create an EDHREC-style slug for *name*."""

    slug = (name or "").strip()
    slug = slug.replace(" // ", " - ").replace("//", " - ")
    slug = _strip_accents(slug.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return re.sub(r"-+", "-", slug)


def _partner_orders(name: str) -> list[str]:
    """Return all partner orderings for *name*."""

    normalized = (name or "").replace(" - ", " // ")
    if "//" not in normalized:
        return [name]
    first, second = [piece.strip() for piece in normalized.split("//", 1)]
    return [f"{first} // {second}", f"{second} // {first}"]


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

    for variant in _partner_orders(name or ""):
        slug = _slugify(variant)
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


def _pick_avg_link(html: str, bracket: str) -> Optional[Dict[str, Set[str] | Optional[str]]]:
    links = re.findall(r'href="(/average-decks/[a-z0-9\-]+/[a-z0-9\-]+)"', html)
    links = list(dict.fromkeys(links))
    if not links:
        return None

    buckets = {path.rsplit("/", 1)[-1] for path in links}
    lower_bracket = (bracket or "").strip().lower()
    for path in links:
        if path.endswith(f"/{lower_bracket}"):
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

    normalized_bracket = (bracket or "").strip().lower()
    if not normalized_bracket:
        raise ValueError({"code": "BRACKET_REQUIRED", "message": "Bracket is required"})

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

    for variant in _partner_orders(name or ""):
        slug = _slugify(variant)
        if not slug:
            continue
        url = f"https://edhrec.com/average-decks/{slug}/{normalized_bracket}"
        response = session.get(url, headers={"User-Agent": UA}, timeout=15)
        if response.status_code == 200:
            return {
                "source_url": url,
                "available_brackets": {normalized_bracket},
            }

    query = quote_plus(name or "")
    search_url = f"https://edhrec.com/search?q={query}"
    html = _fetch_html(session, search_url)
    match = re.search(r'href="(/average-decks/[a-z0-9\-]+/[a-z0-9\-]+)"', html)
    if match:
        url = f"https://edhrec.com{match.group(1)}"
        return {
            "source_url": url,
            "available_brackets": {url.rsplit("/", 1)[-1]},
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


__all__ = [
    "find_average_deck_url",
]
