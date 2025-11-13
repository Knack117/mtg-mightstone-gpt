"""Robust EDHREC average deck fetching and parsing helpers."""

from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from edhrec import (
    find_average_deck_url,
    display_average_deck_bracket,
    normalize_average_deck_bracket,
)
from utils.commander_identity import commander_to_slug
from utils.edhrec_commander import (
    extract_build_id_from_html,
    extract_commander_sections_from_json,
    extract_commander_tags_from_html,
    extract_commander_tags_from_json,
    normalize_commander_tags,
)

__all__ = [
    "EdhrecError",
    "EdhrecNotFoundError",
    "EdhrecParsingError",
    "EdhrecTimeoutError",
    "average_deck_url",
    "deep_find_cards",
    "fetch_average_deck",
    "slugify_commander",
    "fetch_commander_summary",
    "fetch_commander_tag_theme",
    "fetch_tag_theme",
    "fetch_tag_index",
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


@dataclass
class CommanderMetadata:
    tags: List[str]
    sections: Dict[str, List[str]]


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
    """Return the EDHREC slug for a commander name."""

    return commander_to_slug(name or "")


def average_deck_url(name: str, bracket: str = "upgraded") -> str:
    """Return the EDHREC average deck URL for a commander and bracket."""

    slug = slugify_commander(name)
    normalized_bracket = normalize_average_deck_bracket(bracket)
    if normalized_bracket:
        return f"https://edhrec.com/average-decks/{slug}/{normalized_bracket}"
    return f"https://edhrec.com/average-decks/{slug}"


def _cache_key(slug: str, bracket: str) -> Tuple[str, str]:
    return slug, (bracket or "")


def _request_average_deck(url: str, session: Optional[requests.Session] = None) -> str:
    last_exc: Optional[EdhrecError] = None

    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            if session is not None:
                response = session.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
            else:
                response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        except requests.Timeout:
            last_exc = EdhrecTimeoutError(
                f"Timeout fetching EDHREC page after {REQUEST_TIMEOUT}s", url
            )
        except requests.RequestException as exc:
            last_exc = EdhrecError(f"Network error talking to EDHREC: {exc}", url)
        else:
            if response.status_code == 404:
                raise EdhrecNotFoundError("Average deck not found for this commander/bracket", url)
            if response.status_code >= 500 and attempt < RETRY_ATTEMPTS:
                time.sleep(0.3 * (attempt + 1))
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                last_exc = EdhrecError(f"Unexpected response: {exc}", url)
            else:
                return response.text

        time.sleep(0.2 * (attempt + 1))

    assert last_exc is not None
    raise last_exc


_AVERAGE_DECK_PATH_RE = re.compile(
    r"^/average-decks/([a-z0-9\-]+)(?:/([a-z0-9\-]+)(?:/([a-z0-9\-]+))?)?$"
)


def _normalize_average_deck_url(url: str) -> Tuple[str, str, str]:
    if not url or not str(url).strip():
        raise ValueError("source_url is required")

    parsed = urlparse(str(url).strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("source_url must be http or https")

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc != "edhrec.com":
        raise ValueError("source_url must point to edhrec.com")

    path = parsed.path.rstrip("/")
    match = _AVERAGE_DECK_PATH_RE.match(path)
    if not match:
        raise ValueError("source_url must be an EDHREC average-decks URL")

    slug = match.group(1)
    bracket_parts = [part for part in match.groups()[1:] if part]
    raw_bracket = "/".join(bracket_parts)
    normalized_bracket = normalize_average_deck_bracket(raw_bracket)
    normalized_url = f"https://edhrec.com/average-decks/{slug}"
    if normalized_bracket:
        normalized_url += f"/{normalized_bracket}"
    return normalized_url, slug, normalized_bracket


def _fetch_average_deck_payload(
    slug: str,
    bracket: str,
    *,
    session: Optional[requests.Session] = None,
    source_url: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_bracket = normalize_average_deck_bracket(bracket)
    key = _cache_key(slug, normalized_bracket)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return json.loads(json.dumps(cached[1]))

    if source_url:
        url = source_url
    else:
        url = f"https://edhrec.com/average-decks/{slug}"
        if normalized_bracket:
            url += f"/{normalized_bracket}"
    html = _request_average_deck(url, session=session)
    payload = _find_next_data(html, url)
    cards = _find_cards_in_payload(payload, url)

    normalized_cards = [
        {
            "name": card.name,
            "qty": int(card.qty),
            "is_commander": bool(card.is_commander),
        }
        for card in cards
        if card.qty > 0 and card.name
    ]

    result = {
        "source_url": url,
        "bracket": normalized_bracket,
        "cards": normalized_cards,
    }
    _CACHE[key] = (now, json.loads(json.dumps(result)))
    return json.loads(json.dumps(result))


def _fetch_commander_metadata(slug: str, session: requests.Session) -> CommanderMetadata:
    if not slug:
        return CommanderMetadata(tags=[], sections={
            "High Synergy Cards": [],
            "Top Cards": [],
            "Game Changers": [],
        })

    commander_url = f"https://edhrec.com/commanders/{slug}"
    try:
        response = session.get(commander_url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return CommanderMetadata(tags=[], sections={
            "High Synergy Cards": [],
            "Top Cards": [],
            "Game Changers": [],
        })

    if response.status_code != 200:
        return CommanderMetadata(tags=[], sections={
            "High Synergy Cards": [],
            "Top Cards": [],
            "Game Changers": [],
        })

    html = response.text
    html_tags = extract_commander_tags_from_html(html)
    build_id = extract_build_id_from_html(html)
    json_tags: List[str] = []
    sections: Dict[str, List[str]] = {
        "High Synergy Cards": [],
        "Top Cards": [],
        "Game Changers": [],
    }

    if build_id:
        json_url = f"https://edhrec.com/_next/data/{build_id}/commanders/{slug}.json"
        try:
            json_response = session.get(json_url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            json_response = None
        else:
            if json_response.status_code == 200:
                try:
                    payload = json_response.json()
                except ValueError:
                    payload = None
                else:
                    json_tags = extract_commander_tags_from_json(payload)
                    sections = extract_commander_sections_from_json(payload)

    if json_tags:
        tags = normalize_commander_tags(json_tags)
    else:
        tags = normalize_commander_tags(html_tags)
    return CommanderMetadata(tags=tags, sections=sections)


def _find_next_data(html: str, url: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise EdhrecParsingError("Missing __NEXT_DATA__ payload", url, "script id=__NEXT_DATA__")

    try:
        return json.loads(script.string)
    except json.JSONDecodeError as exc:
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


def _extract_commander_card(
    name: Optional[str], cards: List[_NormalizedCard]
) -> Tuple[Optional[Dict[str, Any]], List[_NormalizedCard]]:
    normalized_name = (name or "").strip()
    commander_names = [part.strip() for part in re.split(r"//", normalized_name)] if normalized_name else []
    commander_names = [n for n in commander_names if n]
    normalized_lookup = {n.lower(): n for n in commander_names}
    full_name_lower = normalized_name.lower() if normalized_name else None

    commander_entries: List[_NormalizedCard] = []
    remaining: List[_NormalizedCard] = []

    for card in cards:
        card_name_lower = card.name.lower()
        is_match = card.is_commander
        if full_name_lower and card_name_lower == full_name_lower:
            is_match = True
        if normalized_lookup and card_name_lower in normalized_lookup:
            is_match = True
        if is_match:
            commander_entries.append(card)
        else:
            remaining.append(card)

    if not commander_entries:
        return None, cards

    total_qty = sum(card.qty for card in commander_entries)
    component_names = [card.name for card in commander_entries]
    if normalized_name:
        commander_name = normalized_name
    else:
        commander_name = " // ".join(component_names)
    commander_card: Dict[str, Any] = {"name": commander_name, "qty": total_qty}
    if len(component_names) > 1 or commander_name.lower() != commander_entries[0].name.lower():
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


def fetch_average_deck(
    name: Optional[str] = None,
    bracket: Optional[str] = "upgraded",
    *,
    source_url: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """Fetch and parse EDHREC average deck data."""

    normalized_name = (name or "").strip() or None
    normalized_bracket = None
    available_brackets: Optional[Set[str]] = None

    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True

    commander_metadata = CommanderMetadata(
        tags=[],
        sections={
            "High Synergy Cards": [],
            "Top Cards": [],
            "Game Changers": [],
        },
    )

    try:
        if source_url:
            normalized_url, slug, normalized_bracket = _normalize_average_deck_url(source_url)
        else:
            if not normalized_name:
                raise ValueError("Commander name is required")
            if not bracket or not bracket.strip():
                raise ValueError("Bracket must be provided when source_url is omitted")

            normalized_bracket = normalize_average_deck_bracket(bracket)

            discovery = find_average_deck_url(
                session,
                normalized_name,
                display_average_deck_bracket(normalized_bracket),
            )
            raw_url = discovery.get("source_url")
            normalized_url, slug, normalized_bracket = _normalize_average_deck_url(str(raw_url))
            available_data = discovery.get("available_brackets")
            if isinstance(available_data, (set, list, tuple)):
                available_brackets = {str(item) for item in available_data}
        payload = _fetch_average_deck_payload(
            slug,
            normalized_bracket or "",
            session=session,
            source_url=normalized_url,
        )
        try:
            commander_metadata = _fetch_commander_metadata(slug, session)
        except Exception:
            commander_metadata = CommanderMetadata(
                tags=[],
                sections={
                    "High Synergy Cards": [],
                    "Top Cards": [],
                    "Game Changers": [],
                },
            )
    finally:
        if own_session:
            session.close()

    cards_payload = payload.get("cards", [])
    cards: List[_NormalizedCard] = []
    for entry in cards_payload:
        if not isinstance(entry, dict):
            continue
        name_value = entry.get("name")
        qty_value = entry.get("qty")
        if not isinstance(name_value, str):
            continue
        qty_int = _coerce_int(qty_value)
        if qty_int is None:
            continue
        cards.append(
            _NormalizedCard(
                name=name_value,
                qty=max(1, qty_int),
                is_commander=bool(entry.get("is_commander")),
            )
        )

    commander_card, remaining_cards = _extract_commander_card(normalized_name, cards)
    final_cards = [
        {"name": card.name, "qty": card.qty}
        for card in remaining_cards
        if card.qty > 0 and card.name
    ]

    if not final_cards:
        raise EdhrecParsingError("Parsed deck contained no cards", payload.get("source_url", ""), None)

    result: Dict[str, Any] = {
        "commander": normalized_name or (commander_card["name"] if commander_card else None),
        "bracket": display_average_deck_bracket(payload.get("bracket", normalized_bracket or "")),
        "source_url": payload.get("source_url"),
        "cards": final_cards,
        "commander_card": commander_card,
        "commander_tags": commander_metadata.tags,
        "commander_high_synergy_cards": commander_metadata.sections.get("High Synergy Cards", []),
        "commander_top_cards": commander_metadata.sections.get("Top Cards", []),
        "commander_game_changers": commander_metadata.sections.get("Game Changers", []),
    }

    if result["commander"] is None:
        result.pop("commander")

    if available_brackets is not None:
        result["available_brackets"] = sorted(str(item) for item in available_brackets)

    return result


# -----------------------------------------------------------------------------
# Commander Summary Helpers
#
# These helper functions fetch and parse commander pages on EDHREC, including
# budget variants, extract card categories, compute inclusion percentages, and
# collect tag counts from both the HTML and embedded Next.js payloads.
# -----------------------------------------------------------------------------

_SUMMARY_SECTION_ORDER: Tuple[str, ...] = (
    "New Cards",
    "High Synergy Cards",
    "Top Cards",
    "Game Changers",
    "Creatures",
    "Instants",
    "Sorceries",
    "Utility Artifacts",
    "Enchantments",
    "Battles",
    "Planeswalkers",
    "Utility Lands",
    "Mana Artifacts",
    "Lands",
)

_TAG_LINK_RE = re.compile(r"/(?:tags|themes)/[a-z0-9\-]+(?:/[a-z0-9\-]+)?", re.IGNORECASE)


def _coerce_budget_segment(budget: Optional[str]) -> Optional[str]:
    if budget is None:
        return None
    normalized = budget.strip().lower()
    if not normalized:
        return None
    aliases = {
        "budget": "budget",
        "cheap": "budget",
        "low": "budget",
        "expensive": "expensive",
        "premium": "expensive",
        "high": "expensive",
    }
    resolved = aliases.get(normalized, normalized)
    if resolved not in {"budget", "expensive"}:
        raise ValueError("Budget must be 'budget' or 'expensive'")
    return resolved


def _parse_count(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    text = str(value).strip().lower()
    if not text:
        return None
    text = text.replace(",", "")
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1000, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def _split_tag_name_and_count(text: str) -> Tuple[str, Optional[int]]:
    cleaned = text.strip()
    if not cleaned:
        return "", None
    match = re.search(r"\(([^)]+)\)\s*$", cleaned)
    if match:
        count = _parse_count(match.group(1))
        name = cleaned[: match.start()].strip()
        return name, count
    match = re.search(r"([0-9][0-9,\.]*\s*[kKmM]?)(?:\s+decks?|$)", cleaned)
    if match and match.end() == len(cleaned):
        count = _parse_count(match.group(1))
        name = cleaned[: match.start()].strip(" -:\u2013")
        return name, count
    return cleaned, None


def _extract_tags_with_counts_from_html(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    merged: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def record(name: str, count: Optional[int]) -> None:
        normalized = name.strip()
        if not normalized:
            return
        key = normalized.lower()
        if key in merged:
            if merged[key]["deck_count"] is None and isinstance(count, int):
                merged[key]["deck_count"] = count
            return
        merged[key] = {"name": normalized, "deck_count": count if isinstance(count, int) else None}

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if not _TAG_LINK_RE.search(href or ""):
            continue
        count: Optional[int] = None
        for attr in ("data-tag-count", "data-count", "data-deck-count"):
            if attr in anchor.attrs:
                count = _parse_count(anchor.attrs.get(attr))
                if count is not None:
                    break
        if count is None:
            for child in anchor.find_all(["span", "div"]):
                child_text = child.get_text(" ", strip=True)
                _, child_count = _split_tag_name_and_count(child_text)
                if child_count is not None:
                    count = child_count
                    break
        text = anchor.get_text(" ", strip=True)
        name, parsed_count = _split_tag_name_and_count(text)
        if count is None:
            count = parsed_count
        record(name, count)

    return list(merged.values())


def _extract_tags_with_counts_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    commander = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("commander")
    )
    if not isinstance(commander, dict):
        return []

    merged: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def record(name: str, count: Optional[int]) -> None:
        normalized = (name or "").strip()
        if not normalized:
            return
        key = normalized.lower()
        if key in merged:
            if merged[key]["deck_count"] is None and isinstance(count, int):
                merged[key]["deck_count"] = count
            return
        merged[key] = {"name": normalized, "deck_count": count if isinstance(count, int) else None}

    visited: Set[int] = set()

    def walk(node: Any, *, is_tag_context: bool = False) -> None:
        node_id = id(node)
        if node_id in visited:
            return
        visited.add(node_id)

        if isinstance(node, dict):
            slug_value = None
            for key in ("slug", "href", "url", "path"):
                raw = node.get(key)
                if isinstance(raw, str) and _TAG_LINK_RE.search(raw):
                    slug_value = raw
                    break
            tag_field = node.get("tag") or node.get("theme")
            name_field: Optional[str] = None
            for key in ("name", "label", "title", "displayName"):
                raw = node.get(key)
                if isinstance(raw, str) and raw.strip():
                    name_field = raw.strip()
                    break
            if name_field is None and isinstance(tag_field, dict):
                for key in ("name", "label", "title"):
                    raw = tag_field.get(key)
                    if isinstance(raw, str) and raw.strip():
                        name_field = raw.strip()
                        break
            elif name_field is None and isinstance(tag_field, str):
                name_field = tag_field.strip()

            count_value: Optional[int] = None
            for key in ("deckCount", "deck_count", "numDecks", "num_decks", "count", "decks"):
                count_value = _parse_count(node.get(key))
                if count_value is not None:
                    break

            is_tag = is_tag_context or slug_value is not None or tag_field is not None

            if name_field and count_value is not None and is_tag:
                record(name_field, count_value)

            for child_key, child_value in node.items():
                if isinstance(child_value, (dict, list, tuple, set)):
                    child_tag_context = is_tag or child_key.lower() in {
                        "tags",
                        "themes",
                        "tagcloud",
                        "tag_cloud",
                        "taggroups",
                        "groups",
                    }
                    walk(child_value, is_tag_context=child_tag_context)

        elif isinstance(node, (list, tuple, set)):
            for entry in node:
                if isinstance(entry, (dict, list, tuple, set)):
                    walk(entry, is_tag_context=is_tag_context)

    walk(commander)
    return list(merged.values())


def _merge_tag_sources(*sources: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for source in sources:
        if not source:
            continue
        for entry in source:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip().lower()
            count = entry.get("deck_count")
            count_value = int(count) if isinstance(count, (int, float)) else None
            if key in merged:
                if merged[key]["deck_count"] is None and count_value is not None:
                    merged[key]["deck_count"] = count_value
            else:
                merged[key] = {"name": name.strip(), "deck_count": count_value}
    return list(merged.values())


def _sort_tags_by_deck_count(tags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def sort_key(entry: Dict[str, Any]) -> Tuple[float, str]:
        count = entry.get("deck_count")
        if isinstance(count, int):
            return (-float(count), entry["name"].lower())
        return (float("inf"), entry["name"].lower())

    return sorted(tags, key=sort_key)


def _parse_cardlists_from_json(payload: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    categories: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    if not isinstance(payload, dict):
        return {}
    page_props = payload.get("props", {}).get("pageProps", {})
    container = page_props.get("data", {}).get("container", {})
    json_dict = container.get("json_dict") or container.get("jsonDict") or {}
    cardlists = json_dict.get("cardlists")
    if not isinstance(cardlists, list):
        return {}

    for section in cardlists:
        if not isinstance(section, dict):
            continue
        header = section.get("header") or section.get("name") or section.get("title")
        if not isinstance(header, str) or not header.strip():
            continue
        header_text = header.strip()
        cards_out: List[Dict[str, Any]] = []
        for key in ("cardviews", "cards", "items"):
            card_entries = section.get(key)
            if not isinstance(card_entries, list):
                continue
            for entry in card_entries:
                if not isinstance(entry, dict):
                    continue
                name = None
                for name_key in ("name", "cardName", "label", "title"):
                    raw = entry.get(name_key)
                    if isinstance(raw, str) and raw.strip():
                        name = raw.strip()
                        break
                if name is None and isinstance(entry.get("names"), list):
                    parts = [str(part).strip() for part in entry["names"] if isinstance(part, str) and part.strip()]
                    if parts:
                        name = " // ".join(parts)
                if not name:
                    continue
                synergy = entry.get("synergy")
                synergy_pct = (
                    round(float(synergy) * 100, 2)
                    if isinstance(synergy, (int, float))
                    else None
                )
                num_decks = entry.get("num_decks") or entry.get("numDecks") or entry.get("inclusion")
                potential_decks = entry.get("potential_decks") or entry.get("potentialDecks")
                inclusion_pct: Optional[float] = None
                if (
                    isinstance(num_decks, (int, float))
                    and isinstance(potential_decks, (int, float))
                    and potential_decks
                ):
                    try:
                        inclusion_pct = round(float(num_decks) / float(potential_decks) * 100, 2)
                    except ZeroDivisionError:
                        inclusion_pct = None
                cards_out.append(
                    {
                        "name": name,
                        "inclusion_percent": inclusion_pct,
                        "synergy_percent": synergy_pct,
                    }
                )
        if cards_out:
            categories[header_text] = cards_out

    return dict(categories)


def _normalize_summary_categories(categories: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    ordered: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for header in _SUMMARY_SECTION_ORDER:
        ordered[header] = list(categories.get(header, []))
    for header, cards in categories.items():
        if header not in ordered:
            ordered[header] = cards
    return dict(ordered)


def _extract_next_payload(html: str, url: str) -> Optional[Dict[str, Any]]:
    try:
        return _find_next_data(html, url)
    except EdhrecParsingError:
        return None
    except Exception:
        return None


def _extract_page_metadata(html: str) -> Tuple[Optional[str], Optional[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    title = title_tag.get_text(strip=True) if title_tag else None
    description = meta_desc.get("content", None) if meta_desc else None
    if isinstance(description, str):
        description = description.strip()
    return title, description


def fetch_commander_summary(
    name: str,
    *,
    budget: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    if not name or not name.strip():
        raise ValueError("Commander name is required")

    slug = commander_to_slug(name.strip())
    budget_segment = _coerce_budget_segment(budget)
    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True

    try:
        url = f"https://edhrec.com/commanders/{slug}"
        if budget_segment:
            url = f"{url}/{budget_segment}"

        response = session.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        html = response.text

        payload = _extract_next_payload(html, url)
        categories = _normalize_summary_categories(_parse_cardlists_from_json(payload))

        tags_from_payload = _extract_tags_with_counts_from_payload(payload or {}) if payload else []
        tags_from_html = _extract_tags_with_counts_from_html(html)

        json_tag_names = extract_commander_tags_from_json(payload) if payload else []
        html_tag_names = extract_commander_tags_from_html(html)

        combined_tags = _merge_tag_sources(
            tags_from_payload,
            tags_from_html,
            ({"name": tag, "deck_count": None} for tag in json_tag_names),
            ({"name": tag, "deck_count": None} for tag in html_tag_names),
        )

        top_tags = _sort_tags_by_deck_count(combined_tags)[:10]

        return {
            "commander": name.strip(),
            "slug": slug,
            "source_url": url,
            "budget": budget_segment,
            "categories": categories,
            "tags": combined_tags,
            "top_tags": top_tags,
        }
    finally:
        if own_session:
            session.close()


def _slugify_tag(value: str) -> str:
    text = (value or "").strip().lower()
    text = text.replace("+", " plus ")
    text = re.sub(r"[’'`]+", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        raise ValueError("Tag name is required")
    return text


def _normalize_identity_slug(identity: Optional[str]) -> Optional[str]:
    if identity is None:
        return None
    text = identity.strip().lower()
    if not text:
        return None
    text = text.replace("+", "-")
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or None


def fetch_commander_tag_theme(
    name: str,
    tag: str,
    *,
    budget: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    if not name or not name.strip():
        raise ValueError("Commander name is required")
    if not tag or not tag.strip():
        raise ValueError("Tag name is required")

    slug = commander_to_slug(name.strip())
    tag_slug = _slugify_tag(tag)
    budget_segment = _coerce_budget_segment(budget)

    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True

    try:
        url = f"https://edhrec.com/commanders/{slug}"
        if budget_segment:
            url = f"{url}/{budget_segment}"
        url = f"{url}/{tag_slug}"

        response = session.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            raise EdhrecNotFoundError("Commander tag theme not found", url)
        response.raise_for_status()
        html = response.text

        payload = _extract_next_payload(html, url)
        categories = _normalize_summary_categories(_parse_cardlists_from_json(payload))
        title, description = _extract_page_metadata(html)

        if not title:
            display_tag = tag_slug.replace("-", " ").title()
            title = f"{name.strip()} – {display_tag} | EDHREC"

        return {
            "commander": name.strip(),
            "slug": slug,
            "tag": tag_slug,
            "budget": budget_segment,
            "source_url": url,
            "header": title,
            "description": description or "",
            "categories": categories,
        }
    finally:
        if own_session:
            session.close()


def fetch_tag_theme(
    tag: str,
    *,
    identity: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    if not tag or not tag.strip():
        raise ValueError("Tag name is required")

    tag_slug = _slugify_tag(tag)
    identity_slug = _normalize_identity_slug(identity)

    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True

    try:
        url = f"https://edhrec.com/tags/{tag_slug}"
        if identity_slug:
            url = f"{url}/{identity_slug}"

        response = session.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            raise EdhrecNotFoundError("Tag theme not found", url)
        response.raise_for_status()
        html = response.text

        payload = _extract_next_payload(html, url)
        categories = _normalize_summary_categories(_parse_cardlists_from_json(payload))
        title, description = _extract_page_metadata(html)

        if not title:
            display_tag = tag_slug.replace("-", " ").title()
            if identity_slug:
                display_identity = identity_slug.replace("-", " ").title()
                title = f"{display_tag} – {display_identity} | EDHREC"
            else:
                title = f"{display_tag} | EDHREC"

        return {
            "tag": tag_slug,
            "identity": identity_slug,
            "source_url": url,
            "header": title,
            "description": description or "",
            "categories": categories,
        }
    finally:
        if own_session:
            session.close()


def fetch_tag_index(
    *,
    identity: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    identity_slug = _normalize_identity_slug(identity)

    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True

    try:
        url = "https://edhrec.com/tags"
        if identity_slug:
            url = f"{url}/{identity_slug}"

        response = session.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        html = response.text

        soup = BeautifulSoup(html, "html.parser")
        tags: "OrderedDict[Tuple[str, Optional[str]], Dict[str, Any]]" = OrderedDict()

        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "") or ""
            match = re.match(r"^/tags/([a-z0-9\-]+)(?:/([a-z0-9\-]+))?", href)
            if not match:
                continue
            tag_slug = match.group(1)
            anchor_identity = match.group(2) or identity_slug
            text = anchor.get_text(" ", strip=True)
            name, count = _split_tag_name_and_count(text)
            for attr in ("data-tag-count", "data-count", "data-deck-count"):
                if attr in anchor.attrs:
                    attr_count = _parse_count(anchor.attrs.get(attr))
                    if attr_count is not None:
                        count = attr_count
                        break
            key = (tag_slug.lower(), anchor_identity.lower() if anchor_identity else None)
            tag_url = f"https://edhrec.com/tags/{tag_slug}"
            if anchor_identity:
                tag_url = f"{tag_url}/{anchor_identity}"
            entry = tags.get(key)
            if entry is None:
                tags[key] = {
                    "name": name or tag_slug.replace("-", " ").title(),
                    "slug": tag_slug,
                    "identity": anchor_identity,
                    "url": tag_url,
                    "deck_count": count if isinstance(count, int) else None,
                }
            else:
                if name and (not entry.get("name") or entry["name"].lower() == entry["slug"].replace("-", " ").lower()):
                    entry["name"] = name
                if entry.get("deck_count") is None and isinstance(count, int):
                    entry["deck_count"] = count

        return {
            "identity": identity_slug,
            "source_url": url,
            "tags": list(tags.values()),
        }
    finally:
        if own_session:
            session.close()
