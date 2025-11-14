"""Helpers for parsing EDHREC commander pages (build ids, tags)."""

from __future__ import annotations

import re
from collections import OrderedDict
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from bs4 import BeautifulSoup

__all__ = [
    "extract_build_id_from_html",
    "extract_commander_tags_from_html",
    "extract_commander_sections_from_json",
    "extract_commander_tags_from_json",
    "extract_commander_tags_with_counts_from_html",
    "extract_commander_tags_with_counts_from_json",
    "normalize_commander_tag_name",
    "parse_commander_count",
    "split_commander_tag_name_and_count",
    "normalize_commander_tags",
]


_BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
_TAG_HREF_RE = re.compile(r"/(?:tags|themes)/[a-z0-9\-]+", re.IGNORECASE)
_TAG_LINK_RE = re.compile(r"/(?:tags|themes)/[a-z0-9\-]+(?:/[a-z0-9\-]+)?", re.IGNORECASE)
_TAG_SECTION_HEADING_RE = re.compile(r"^tags$", re.IGNORECASE)
_SECTION_KEY_MAP: Dict[str, str] = {
    "highsynergy": "High Synergy Cards",
    "highsynergycards": "High Synergy Cards",
    "synergycards": "High Synergy Cards",
    "topcards": "Top Cards",
    "popularcards": "Top Cards",
    "gamechangers": "Game Changers",
    "gamechanger": "Game Changers",
}
_MAX_TAG_LENGTH = 64
_STRUCTURAL_TAG_NAMES = {
    "themes",
    "kindred",
    "new cards",
    "high synergy",
    "high synergy cards",
    "top cards",
    "game changers",
    "card types",
    "creatures",
    "instants",
    "sorceries",
    "utility artifacts",
    "enchantments",
    "planeswalkers",
    "utility lands",
    "mana artifacts",
    "lands",
}


def extract_build_id_from_html(html: str) -> Optional[str]:
    """Return the Next.js buildId from EDHREC commander HTML (if present)."""

    if not html:
        return None
    match = _BUILD_ID_RE.search(html)
    if match:
        return match.group(1)
    return None


def _looks_like_tag_href(href: Optional[str]) -> bool:
    if not href:
        return False
    return bool(_TAG_HREF_RE.search(href))


def _clean_text(value: str) -> str:
    cleaned = unescape(value or "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _has_class_prefix(value: Optional[str], prefix: str) -> bool:
    if not value:
        return False
    classes = [part.strip() for part in value.split() if part.strip()]
    return any(cls.startswith(prefix) for cls in classes)


def parse_commander_count(value: Any) -> Optional[int]:
    """Return ``value`` parsed as an integer deck count (supports 1.5k syntax)."""

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


def split_commander_tag_name_and_count(text: str) -> Tuple[str, Optional[int]]:
    """Split a ``tag (count)`` style string into its components."""

    cleaned = text.strip()
    if not cleaned:
        return "", None
    match = re.search(r"\(([^)]+)\)\s*$", cleaned)
    if match:
        count = parse_commander_count(match.group(1))
        name = cleaned[: match.start()].strip()
        return name, count
    match = re.search(r"([0-9][0-9,\.]*\s*[kKmM]?)(?:\s+decks?|$)", cleaned)
    if match and match.end() == len(cleaned):
        count = parse_commander_count(match.group(1))
        name = cleaned[: match.start()].strip(" -:\u2013")
        return name, count
    return cleaned, None


def normalize_commander_tag_name(name: str) -> Optional[str]:
    """Return a single normalized commander tag name or ``None`` if invalid."""

    cleaned = normalize_commander_tags([name])
    if not cleaned:
        return None
    return cleaned[0]


class _CommanderTagParser(HTMLParser):
    """Simple HTML parser that captures anchor text for commander tag links."""

    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._buffer: List[str] = []
        self.tags: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, Optional[str]]]):
        if tag.lower() != "a":
            return
        attr_map = {name.lower(): value for name, value in attrs}
        href = attr_map.get("href")
        if _looks_like_tag_href(href):
            self._capture = True
            self._buffer = []
        else:
            self._capture = False

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._capture:
            text = _clean_text("".join(self._buffer))
            if text:
                self.tags.append(text)
            self._capture = False
            self._buffer = []

    def handle_data(self, data: str):
        if self._capture:
            self._buffer.append(data)

    def handle_entityref(self, name: str):
        if self._capture:
            self._buffer.append(unescape(f"&{name};"))

    def handle_charref(self, name: str):
        if not self._capture:
            return
        try:
            if name.lower().startswith("x"):
                codepoint = int(name[1:], 16)
            else:
                codepoint = int(name)
            self._buffer.append(chr(codepoint))
        except ValueError:
            pass

    def error(self, message: str):  # pragma: no cover - HTMLParser API requirement
        pass


def _extract_tags_from_section(section) -> List[str]:  # type: ignore[no-untyped-def]
    tags: List[str] = []
    if section is None or not hasattr(section, "find_all"):
        return tags
    anchors = section.find_all("a", href=True)
    for anchor in anchors:
        href = anchor.get("href")
        if not _looks_like_tag_href(href):
            continue
        text = _clean_text(anchor.get_text(" "))
        if text:
            tags.append(text)
    return tags


def extract_commander_tags_from_html(html: str) -> List[str]:
    """Return commander theme tags discovered in EDHREC HTML."""

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # New layout: Tags rendered within the navigation panel tag cloud
    nav_panel = soup.find(
        "div",
        class_=lambda value: _has_class_prefix(value, "NavigationPanel_tags__"),
    )
    if nav_panel:
        nav_tags: List[str] = []
        anchors = nav_panel.find_all(
            "a",
            class_=lambda value: _has_class_prefix(value, "LinkHelper_container__"),
        )
        for anchor in anchors:
            if not _looks_like_tag_href(anchor.get("href")):
                continue
            label = anchor.find(
                "span",
                class_=lambda value: _has_class_prefix(value, "NavigationPanel_label__"),
            )
            text_source = label or anchor
            text = _clean_text(text_source.get_text(" ") if text_source else "")
            if text:
                nav_tags.append(text)
        if nav_tags:
            return normalize_commander_tags(nav_tags)

    # Prefer anchors contained within the explicit "Tags" section if available.
    section_tags: List[str] = []
    heading = soup.find(lambda tag: tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"} and _TAG_SECTION_HEADING_RE.match(tag.get_text(strip=True) or ""))
    if heading:
        # Include anchors inside the heading's parent (chips often live alongside the heading)
        section_tags.extend(_extract_tags_from_section(heading.parent))
        # Also walk through following siblings until another heading is encountered.
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                break
            section_tags.extend(_extract_tags_from_section(sibling))

    if section_tags:
        return normalize_commander_tags(section_tags)

    # Fallback: capture any anchor that links to a tag/theme URL.
    parser = _CommanderTagParser()
    parser.feed(html)
    parser.close()
    return normalize_commander_tags(parser.tags)


def extract_commander_tags_with_counts_from_html(html: str) -> List[Dict[str, Any]]:
    """Return commander tags (with deck counts when available) from HTML."""

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    merged: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def record(name: str, count: Optional[int]) -> None:
        normalized = normalize_commander_tag_name(name)
        if not normalized:
            return
        key = normalized.lower()
        if key in merged:
            if merged[key]["deck_count"] is None and isinstance(count, int):
                merged[key]["deck_count"] = count
            return
        merged[key] = {"tag": normalized, "deck_count": count if isinstance(count, int) else None}

    def _class_list(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            return [part for part in value.split() if part]
        if isinstance(value, (list, tuple, set)):
            return [str(part) for part in value if isinstance(part, str) and part]
        return []

    nav_panel = soup.find(
        "div",
        class_=lambda value: any(cls.startswith("NavigationPanel_tags__") for cls in _class_list(value)),
    )
    if nav_panel:
        anchors = nav_panel.find_all("a", href=True)
        for anchor in anchors:
            href = anchor.get("href", "")
            if not _TAG_LINK_RE.search(href or ""):
                continue
            label_node = anchor.find(
                "span",
                class_=lambda value: any(
                    cls.startswith("NavigationPanel_label__") for cls in _class_list(value)
                ),
            )
            raw_name = label_node.get_text(" ", strip=True) if label_node else anchor.get_text(" ", strip=True)
            name, inline_count = split_commander_tag_name_and_count(raw_name)
            count_node = anchor.find(
                "span",
                class_=lambda value: (
                    any(cls.startswith("badge") for cls in _class_list(value))
                    or any(cls.startswith("NavigationPanel_count__") for cls in _class_list(value))
                ),
            )
            count = parse_commander_count(count_node.get_text(" ", strip=True)) if count_node else None
            if count is None:
                count = inline_count
            record(name, count)

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if not _TAG_LINK_RE.search(href or ""):
            continue
        count: Optional[int] = None
        for attr in ("data-tag-count", "data-count", "data-deck-count"):
            if attr in anchor.attrs:
                count = parse_commander_count(anchor.attrs.get(attr))
                if count is not None:
                    break
        if count is None:
            for child in anchor.find_all(["span", "div"]):
                child_text = child.get_text(" ", strip=True)
                _, child_count = split_commander_tag_name_and_count(child_text)
                if child_count is not None:
                    count = child_count
                    break
        text = anchor.get_text(" ", strip=True)
        name, parsed_count = split_commander_tag_name_and_count(text)
        if count is None:
            count = parsed_count
        record(name, count)

    return list(merged.values())


def _collect_tag_entries(source: Any, *, treat_as_tag: bool) -> List[str]:
    """Collect potential tag names from ``source``.

    ``treat_as_tag`` indicates whether objects within ``source`` should be
    interpreted as tag entries (e.g., dictionaries describing individual tags).
    When ``False``, the walker only descends into known container keys (``tags``,
    ``items`` ...). This keeps structural labels such as "Themes" or "Kindred"
    from being captured as tags.
    """

    tags: List[str] = []

    if source is None:
        return tags

    if isinstance(source, str):
        if treat_as_tag:
            cleaned = _clean_text(source)
            if cleaned:
                tags.append(cleaned)
        return tags

    if isinstance(source, (list, tuple, set)):
        for item in source:
            tags.extend(_collect_tag_entries(item, treat_as_tag=treat_as_tag))
        return tags

    if isinstance(source, dict):
        nested_candidates: List[Any] = []
        if treat_as_tag:
            for key in ("name", "label", "title", "displayName", "theme"):
                raw = source.get(key)
                if isinstance(raw, str):
                    cleaned = _clean_text(raw)
                    if cleaned:
                        tags.append(cleaned)
                        break
            else:
                for nested_key in ("tag", "theme"):
                    nested_value = source.get(nested_key)
                    if nested_value is not None:
                        nested_candidates.append(nested_value)

        for key, value in source.items():
            key_lower = key.lower() if isinstance(key, str) else ""
            if key_lower in {"tags", "themes", "items", "list", "entries", "values", "chips", "tag", "tagitem"}:
                nested_candidates.append(value)
            elif key_lower in {
                "sections",
                "groups",
                "tabgroups",
                "tabs",
                "taggroups",
                "collections",
                "edges",
                "nodes",
                "node",
            }:
                tags.extend(_collect_tag_entries(value, treat_as_tag=False))

        for candidate in nested_candidates:
            tags.extend(_collect_tag_entries(candidate, treat_as_tag=True))

        return tags

    return tags


def _extract_commander_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    props = payload.get("props")
    if isinstance(props, dict):
        page_props = props.get("pageProps")
        if isinstance(page_props, dict):
            commander = page_props.get("commander")
            if isinstance(commander, dict):
                return commander
    return None


def extract_commander_tags_from_json(payload: Any) -> List[str]:
    """Return commander theme tags discovered in EDHREC Next.js payloads."""

    commander = _extract_commander_payload(payload)
    if not commander:
        return []

    tags: List[str] = []

    tags.extend(_collect_tag_entries(commander.get("themes"), treat_as_tag=True))

    metadata = commander.get("metadata")
    if isinstance(metadata, dict):
        tag_cloud = metadata.get("tagCloud") or metadata.get("tag_cloud")
        if tag_cloud:
            tags.extend(_collect_tag_entries(tag_cloud, treat_as_tag=False))

    return normalize_commander_tags(tags)


def extract_commander_tags_with_counts_from_json(payload: Any) -> List[Dict[str, Any]]:
    """Return commander tags (with deck counts when available) from JSON payloads."""

    commander = _extract_commander_payload(payload)
    if not commander:
        return []

    merged: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def record(name: str, count: Optional[int]) -> None:
        normalized = normalize_commander_tag_name(name)
        if not normalized:
            return
        key = normalized.lower()
        entry = merged.get(key)
        count_value = count if isinstance(count, int) else None
        if entry:
            if entry["deck_count"] is None and count_value is not None:
                entry["deck_count"] = count_value
            return
        merged[key] = {"tag": normalized, "deck_count": count_value}

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
                count_value = parse_commander_count(node.get(key))
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


def normalize_commander_tags(values: Iterable[str]) -> List[str]:
    """Clean and deduplicate commander tags while preserving order."""

    seen: Set[str] = set()
    result: List[str] = []
    for raw in values:
        cleaned = _clean_text(raw)
        if not cleaned:
            continue
        if len(cleaned) > _MAX_TAG_LENGTH:
            continue
        if not re.search(r"[A-Za-z]", cleaned):
            continue
        if cleaned.lower() in _STRUCTURAL_TAG_NAMES:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _gather_section_card_names(source: Any) -> List[str]:
    names: List[str] = []
    visited: Set[int] = set()

    def collect(node: Any):
        node_id = id(node)
        if node_id in visited:
            return
        visited.add(node_id)

        if isinstance(node, dict):
            name_value: Optional[str] = None
            for key in ("name", "cardName", "label", "title"):
                raw = node.get(key)
                if isinstance(raw, str) and raw.strip():
                    name_value = _clean_text(raw)
                    break
            if not name_value and isinstance(node.get("names"), list):
                parts = [_clean_text(part) for part in node["names"] if isinstance(part, str)]
                parts = [part for part in parts if part]
                if parts:
                    name_value = " // ".join(parts)
            if name_value:
                names.append(name_value)

            for child_key, child_value in node.items():
                if child_key in {"name", "cardName", "label", "title", "names"}:
                    continue
                if isinstance(child_value, (dict, list, tuple, set)):
                    collect(child_value)
        elif isinstance(node, (list, tuple, set)):
            str_entries = [
                _clean_text(entry)
                for entry in node
                if isinstance(entry, str) and _clean_text(entry)
            ]
            if str_entries and len(str_entries) == len(node):
                names.extend(str_entries)
            else:
                for entry in node:
                    collect(entry)

    collect(source)

    deduped: List[str] = []
    seen: Set[str] = set()
    for name in names:
        cleaned = _clean_text(name)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def extract_commander_sections_from_json(payload: Any) -> Dict[str, List[str]]:
    """Return commander sections (High Synergy, Top Cards, Game Changers) from JSON."""

    sections: Dict[str, List[str]] = {
        "High Synergy Cards": [],
        "Top Cards": [],
        "Game Changers": [],
    }

    if payload is None:
        return sections

    visited: Set[int] = set()

    def walk(node: Any):
        node_id = id(node)
        if node_id in visited:
            return
        visited.add(node_id)

        if isinstance(node, dict):
            for key, value in node.items():
                normalized = re.sub(r"[^a-z]", "", key.lower()) if isinstance(key, str) else ""
                if normalized in _SECTION_KEY_MAP:
                    header = _SECTION_KEY_MAP[normalized]
                    names = _gather_section_card_names(value)
                    if names:
                        existing = sections.get(header, [])
                        seen = {name.lower(): name for name in existing}
                        for name in names:
                            lowered = name.lower()
                            if lowered not in seen:
                                existing.append(name)
                                seen[lowered] = name
                        sections[header] = existing
                walk(value)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                walk(item)

    walk(payload)
    return sections
