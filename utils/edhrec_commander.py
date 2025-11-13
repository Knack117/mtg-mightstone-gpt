"""Helpers for parsing EDHREC commander pages (build ids, tags)."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable, List, Optional, Sequence, Set

__all__ = [
    "extract_build_id_from_html",
    "extract_commander_tags_from_html",
    "extract_commander_tags_from_json",
    "normalize_commander_tags",
]


_BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
_TAG_HREF_RE = re.compile(r"/(?:tags|themes)/[a-z0-9\-]+", re.IGNORECASE)
_MAX_TAG_LENGTH = 64


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


def extract_commander_tags_from_html(html: str) -> List[str]:
    """Return commander theme tags discovered in EDHREC HTML."""

    if not html:
        return []
    parser = _CommanderTagParser()
    parser.feed(html)
    parser.close()
    return normalize_commander_tags(parser.tags)


def _coerce_tag_candidate(value: Any) -> List[str]:
    tags: List[str] = []
    if isinstance(value, str):
        candidate = _clean_text(value)
        if candidate:
            tags.append(candidate)
        return tags

    if isinstance(value, dict):
        for key in ("name", "label", "title", "theme", "displayName"):
            raw = value.get(key)
            if isinstance(raw, str):
                candidate = _clean_text(raw)
                if candidate:
                    tags.append(candidate)
        for nested_key in ("tags", "themes", "items", "list", "entries"):
            tags.extend(_coerce_tag_candidate(value.get(nested_key)))
        return tags

    if isinstance(value, (list, tuple, set)):
        for item in value:
            tags.extend(_coerce_tag_candidate(item))
        return tags

    return tags


def extract_commander_tags_from_json(payload: Any) -> List[str]:
    """Return commander theme tags discovered in EDHREC Next.js payloads."""

    if payload is None:
        return []

    tags: List[str] = []
    visited: Set[int] = set()

    def walk(node: Any):
        node_id = id(node)
        if node_id in visited:
            return
        visited.add(node_id)

        if isinstance(node, dict):
            for key, value in node.items():
                key_lower = key.lower() if isinstance(key, str) else ""
                if "tag" in key_lower or "theme" in key_lower:
                    tags.extend(_coerce_tag_candidate(value))
                walk(value)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                walk(item)

    walk(payload)
    return normalize_commander_tags(tags)


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
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result
