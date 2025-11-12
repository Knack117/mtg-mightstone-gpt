"""Robust EDHREC average deck fetching and parsing helpers."""

from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import wraps
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup

__all__ = [
    "EdhrecError",
    "EdhrecNotFoundError",
    "EdhrecParsingError",
    "EdhrecTimeoutError",
    "average_deck_url",
    "deep_find_cards",
    "fetch_average_deck",
    "slugify_commander",
]

USER_AGENT = "Mightstone-GPT/1.0 (+https://mtg-mightstone-gpt.onrender.com)"
REQUEST_TIMEOUT = 12
RETRY_ATTEMPTS = 2
CACHE_TTL_SECONDS = 15 * 60

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}

_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}


class EdhrecError(RuntimeError):
    """Base exception for EDHREC related failures."""

    def __init__(self, message: str, url: str, details: Optional[str] = None) -> None:
        super().__init__(message)
        self.url = url
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        payload = {"message": str(self), "url": self.url}
        if self.details:
            payload["details"] = self.details
        return payload


class EdhrecTimeoutError(EdhrecError):
    """Raised when EDHREC requests exceed the timeout budget."""


class EdhrecNotFoundError(EdhrecError):
    """Raised when a commander/bracket combination is missing on EDHREC."""


class EdhrecParsingError(EdhrecError):
    """Raised when EDHREC payloads cannot be parsed."""


def slugify_commander(name: str) -> str:
    """Return the EDHREC slug for a commander name.

    Rules:
    * Lowercase and trim surrounding whitespace.
    * Replace " // " and "//" with a hyphen.
    * Replace all non-alphanumeric characters with hyphens.
    * Collapse runs of hyphens and strip leading/trailing hyphens.
    """

    slug = (name or "").strip().lower()
    if not slug:
        return ""

    slug = slug.replace(" // ", "-").replace("//", "-")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def average_deck_url(name: str, bracket: str = "upgraded") -> str:
    """Return the EDHREC average deck URL for a commander and bracket."""

    slug = slugify_commander(name)
    normalized_bracket = (bracket or "upgraded").strip().lower()
    return f"https://edhrec.com/average-decks/{slug}/{normalized_bracket}"


def _cache_key(slug: str, bracket: str) -> Tuple[str, str]:
    return slug, (bracket or "upgraded").strip().lower()


def _with_cache(func):
    @wraps(func)
    def wrapper(slug: str, bracket: str, *args: Any, **kwargs: Any):
        key = _cache_key(slug, bracket)
        now = time.time()
        cached = _CACHE.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            # Return a shallow copy to avoid callers mutating the cache payload.
            return json.loads(json.dumps(cached[1]))

        result = func(slug, bracket, *args, **kwargs)
        _CACHE[key] = (now, json.loads(json.dumps(result)))
        return result

    return wrapper


def _request_average_deck(url: str) -> str:
    last_exc: Optional[EdhrecError] = None

    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        except requests.Timeout as exc:  # pragma: no cover - network failure path
            last_exc = EdhrecTimeoutError(
                f"Timeout fetching EDHREC page after {REQUEST_TIMEOUT}s", url
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure path
            last_exc = EdhrecError(f"Network error talking to EDHREC: {exc}", url)
        else:
            if response.status_code == 404:
                raise EdhrecNotFoundError("Average deck not found for this commander/bracket", url)
            if response.status_code >= 500 and attempt < RETRY_ATTEMPTS:
                time.sleep(0.3 * (attempt + 1))
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:  # pragma: no cover - unexpected status
                last_exc = EdhrecError(f"Unexpected response: {exc}", url)
            else:
                return response.text

        time.sleep(0.2 * (attempt + 1))

    assert last_exc is not None
    raise last_exc


def _find_next_data(html: str, url: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise EdhrecParsingError("Missing __NEXT_DATA__ payload", url, "script id=__NEXT_DATA__")

    try:
        return json.loads(script.string)
    except json.JSONDecodeError as exc:  # pragma: no cover - malformed payload
        raise EdhrecParsingError("Invalid JSON in __NEXT_DATA__", url, str(exc)) from exc


def _looks_card_container_key(key: str) -> bool:
    normalized = key.lower()
    return any(token in normalized for token in ("deck", "cards", "average", "mainboard", "board"))


def deep_find_cards(obj: Any) -> Optional[List[Any]]:
    """Search *obj* for card-like collections and flatten them.

    Returns the first encountered list of card-ish objects or strings. When multiple
    card lists are discovered (e.g., categories for lands/spells), their contents are
    concatenated in discovery order to maintain a stable ordering.
    """

    seen_lists: List[List[Any]] = []
    seen_ids: set[int] = set()

    def is_card_like(item: Any) -> bool:
        if isinstance(item, str):
            return bool(item.strip())
        if isinstance(item, dict):
            if isinstance(item.get("card"), dict) and isinstance(item["card"].get("name"), str):
                return True
            for name_key in ("name", "cardName", "label", "cardname"):
                if isinstance(item.get(name_key), str):
                    return True
            if isinstance(item.get("names"), list) and all(isinstance(v, str) for v in item["names"]):
                return True
        return False

    def walk(node: Any) -> None:
        if isinstance(node, list):
            node_id = id(node)
            if node_id in seen_ids:
                return
            if node and all(is_card_like(entry) for entry in node):
                seen_ids.add(node_id)
                seen_lists.append(node)
                return
            seen_ids.add(node_id)
            for entry in node:
                walk(entry)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(obj)

    if not seen_lists:
        return None

    flattened: List[Any] = []
    for entries in seen_lists:
        flattened.extend(entries)
    return flattened


@dataclass
class _NormalizedCard:
    name: str
    qty: int
    is_commander: bool = False


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            return int(value)
    return None


def _normalize_card_entry(entry: Any) -> Optional[_NormalizedCard]:
    if isinstance(entry, str):
        name = entry.strip()
        if not name:
            return None
        return _NormalizedCard(name=name, qty=1)

    if not isinstance(entry, dict):
        return None

    source = entry
    if isinstance(entry.get("card"), dict):
        source = {**entry, **entry["card"]}

    name: Optional[str] = None
    for key in ("name", "cardName", "card_name", "label", "title"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break

    if not name and isinstance(source.get("names"), list):
        names = [v.strip() for v in source["names"] if isinstance(v, str) and v.strip()]
        if names:
            name = " // ".join(names)

    if not name:
        return None

    qty: Optional[int] = None
    for key in ("qty", "quantity", "count", "copies", "amount", "q"):
        qty = _coerce_int(source.get(key))
        if qty is not None:
            break
    if qty is None:
        qty = 1

    is_commander = False
    for flag in ("isCommander", "is_commander", "commander"):
        value = source.get(flag)
        if isinstance(value, bool):
            is_commander = is_commander or value
    categories = source.get("categories")
    if isinstance(categories, Sequence) and not isinstance(categories, (str, bytes)):
        if any(str(cat).strip().lower() == "commander" for cat in categories if cat is not None):
            is_commander = True
    role = source.get("role") or source.get("slot")
    if isinstance(role, str) and role.strip().lower() == "commander":
        is_commander = True

    return _NormalizedCard(name=name, qty=max(1, qty), is_commander=is_commander)


def _normalize_cards(entries: Iterable[Any]) -> List[_NormalizedCard]:
    normalized: List[_NormalizedCard] = []
    for entry in entries:
        card = _normalize_card_entry(entry)
        if card is not None:
            normalized.append(card)
    return normalized


def _dedupe_cards(cards: Iterable[_NormalizedCard]) -> List[_NormalizedCard]:
    combined: "OrderedDict[str, _NormalizedCard]" = OrderedDict()
    for card in cards:
        key = card.name
        if key in combined:
            existing = combined[key]
            combined[key] = _NormalizedCard(
                name=existing.name,
                qty=existing.qty + card.qty,
                is_commander=existing.is_commander or card.is_commander,
            )
        else:
            combined[key] = card
    return list(combined.values())


def _extract_commander_card(name: str, cards: List[_NormalizedCard]) -> Tuple[Optional[Dict[str, Any]], List[_NormalizedCard]]:
    commander_names = [name.strip() for name in re.split(r"//", name)]
    commander_names = [n for n in commander_names if n]
    normalized_lookup = {n.lower(): n for n in commander_names}
    full_name_lower = name.strip().lower()

    commander_entries: List[_NormalizedCard] = []
    remaining: List[_NormalizedCard] = []

    for card in cards:
        card_name_lower = card.name.lower()
        if card.is_commander or card_name_lower == full_name_lower or card_name_lower in normalized_lookup:
            commander_entries.append(card)
        else:
            remaining.append(card)

    if not commander_entries:
        return None, cards

    total_qty = sum(card.qty for card in commander_entries)
    component_names = [card.name for card in commander_entries]
    commander_card: Dict[str, Any] = {"name": name.strip() or commander_entries[0].name, "qty": total_qty}
    if len(component_names) > 1 or commander_card["name"].lower() != commander_entries[0].name.lower():
        commander_card["components"] = component_names

    return commander_card, remaining


def _find_cards_in_payload(data: Dict[str, Any], url: str) -> List[_NormalizedCard]:
    props = data.get("props") or {}
    page_props = props.get("pageProps") or {}

    candidate_sources: List[Any] = []
    for key, value in page_props.items():
        if _looks_card_container_key(str(key)):
            candidate_sources.append(value)
    if "pageData" in page_props:
        candidate_sources.append(page_props["pageData"])

    for source in candidate_sources:
        cards = deep_find_cards(source)
        if cards:
            normalized = _normalize_cards(cards)
            if normalized:
                return _dedupe_cards(normalized)

    cards = deep_find_cards(page_props) or deep_find_cards(data)
    if cards:
        normalized = _normalize_cards(cards)
        if normalized:
            return _dedupe_cards(normalized)

    keys = ", ".join(sorted(page_props.keys())) or "(no keys)"
    raise EdhrecParsingError("Could not parse EDHREC average deck", url, f"pageProps keys: {keys}")


@_with_cache
def _fetch_average_deck(slug: str, bracket: str, name: str) -> Dict[str, Any]:
    url = f"https://edhrec.com/average-decks/{slug}/{(bracket or 'upgraded').strip().lower()}"
    html = _request_average_deck(url)
    payload = _find_next_data(html, url)
    cards = _find_cards_in_payload(payload, url)

    commander_card, remaining_cards = _extract_commander_card(name, cards)
    final_cards = [
        {"name": card.name, "qty": card.qty}
        for card in remaining_cards
        if card.qty > 0 and card.name
    ]

    return {
        "commander": name,
        "bracket": (bracket or "upgraded").strip().lower(),
        "source_url": url,
        "cards": final_cards,
        "commander_card": commander_card,
    }


def fetch_average_deck(name: str, bracket: str = "upgraded") -> Dict[str, Any]:
    """Fetch and parse EDHREC average deck data for *name*.

    Results are cached for a short period to avoid unnecessary network load.
    """

    if not name or not name.strip():
        raise ValueError("Commander name is required")

    slug = slugify_commander(name)
    if not slug:
        raise ValueError("Commander name is required")

    normalized_bracket = (bracket or "upgraded").strip().lower()
    if normalized_bracket not in {"upgraded", "precon"}:
        raise ValueError("Bracket must be 'upgraded' or 'precon'")

    data = _fetch_average_deck(slug, normalized_bracket, name.strip())

    cards = data.get("cards", [])
    if not isinstance(cards, list) or not cards:
        raise EdhrecParsingError("Parsed deck contained no cards", data["source_url"], None)

    return data
