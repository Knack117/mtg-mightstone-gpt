"""Utilities for fetching and parsing EDHREC "Average Deck" pages."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import re

import requests
from bs4 import BeautifulSoup

from .commander_identity import commander_slug_candidates, commander_to_slug

USER_AGENT = "MightstoneBot/1.0 (+https://mtg-mightstone-gpt.onrender.com)"
TIMEOUT = 20

BRACKET_MAP = {
    "all": "",
    "exhibition": "exhibition",
    "core": "core",
    "upgraded": "upgraded",
    "optimized": "optimized",
    "cedh": "cedh",
    # numeric aliases
    "1": "exhibition",
    "2": "core",
    "3": "upgraded",
    "4": "optimized",
    "5": "cedh",
}


def _normalize_bracket(bracket: str) -> Tuple[str, str]:
    key = (bracket or "all").strip().lower()
    key = BRACKET_MAP.get(key, key)
    if key and key not in BRACKET_MAP.values():
        key = ""
    label = "All" if not key else key.title()
    return key, label


def build_average_url(name: str, bracket: str = "all") -> Tuple[str, str, str]:
    """Return (slug, url, label) for an EDHREC average deck lookup."""

    slug = commander_to_slug(name)
    key, label = _normalize_bracket(bracket)
    url = f"https://edhrec.com/average-decks/{slug}" + (f"/{key}" if key else "")
    return slug, url, label


_LINE = re.compile(r"^\s*(?P<count>\d{1,2})\s+(?P<name>[^/\r\n]+?)\s*$")


def parse_decklines_from_text(text: str) -> List[Dict[str, object]]:
    """Parse copyable deck text into {count, name} objects."""

    items: List[Dict[str, object]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("commander ("):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        count = int(match.group("count"))
        name = match.group("name").strip()
        name = re.sub(r"\s+\[[A-Z0-9]{2,5}\]$", "", name).strip()
        items.append({"count": count, "name": name})
    return items


def extract_decklist(soup: BeautifulSoup) -> List[Dict[str, object]]:
    """Extract a decklist from the EDHREC HTML soup."""

    candidates: List[List[Dict[str, object]]] = []
    for tag_name in ["pre", "code", "textarea"]:
        for element in soup.find_all(tag_name):
            text = element.get_text("\n", strip=True)
            if not text or len(text) <= 100:
                continue
            items = parse_decklines_from_text(text)
            if len(items) >= 60:
                return items
            candidates.append(items)

    text = soup.get_text("\n", strip=True)
    items = parse_decklines_from_text(text)
    if len(items) >= 60:
        return items

    return max(candidates, key=len) if candidates else []


def fetch_average_deck(name: str, bracket: str = "all") -> Dict[str, object]:
    """Fetch an average deck for the supplied commander."""

    key, label = _normalize_bracket(bracket)
    slugs = commander_slug_candidates(name) or [commander_to_slug(name)]
    last_error: Optional[Exception] = None

    for slug in slugs:
        url = f"https://edhrec.com/average-decks/{slug}" + (f"/{key}" if key else "")
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = exc
            continue

        if response.status_code == 404:
            last_error = requests.HTTPError(f"Average deck not found for slug '{slug}' (404)")
            continue

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            last_error = exc
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        items = extract_decklist(soup)
        if items:
            return {
                "header": f"{name} — Average Deck ({label})",
                "description": "",
                "source_url": url,
                "container": {
                    "collections": [
                        {"header": "Decklist", "items": items},
                    ]
                },
            }

        last_error = ValueError(f"Average deck page for slug '{slug}' did not include a decklist")

    if last_error:
        raise last_error

    raise RuntimeError(f"Failed to fetch average deck for {name}")
