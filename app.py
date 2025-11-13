# app.py
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from utils.commander_identity import normalize_commander_name
from utils.edhrec_commander import (
    extract_build_id_from_html,
    extract_commander_tags_from_html,
    extract_commander_tags_from_json,
    normalize_commander_tags,
)
from services.edhrec import EdhrecError, fetch_average_deck
from utils.identity import canonicalize_identity

# -----------------------------------------------------------------------------
# Config & Logging
# -----------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", "8080"))
USER_AGENT = os.environ.get(
    "MIGHTSTONE_UA",
    "Mightstone-GPT/1.0 (+https://mtg-mightstone-gpt.onrender.com)"
)
SCRYFALL_BASE = "https://api.scryfall.com"
EDHREC_BASE = "https://edhrec.com"

logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("mightstone")

# -----------------------------------------------------------------------------
# Privacy Policy
# -----------------------------------------------------------------------------
PRIVACY_CONTACT_EMAIL = os.getenv("PRIVACY_CONTACT_EMAIL", "pommnetwork@gmail.com")
PRIVACY_LAST_UPDATED = date.today().isoformat()

PRIVACY_HTML = f"""<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8">
  <title>Mightstone Privacy Policy</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex">
  <style>
    body {{max-width: 860px; margin: 2rem auto; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; line-height: 1.6; padding: 0 1rem;}}
    h1, h2 {{line-height: 1.2;}}
    code, pre {{background:#f6f8fa; padding: .2rem .3rem; border-radius: 4px;}}
    .muted {{color:#666}}
  </style>
</head><body>
  <h1>Mightstone Privacy Policy</h1>
  <p class="muted">Last updated: {PRIVACY_LAST_UPDATED}</p>

  <h2>1. Introduction</h2>
  <p>Mightstone (“we”, “us”, “our”) provides features via a custom GPT and HTTP endpoints hosted at this service.
  This page explains how we collect, use, and share information when you use Mightstone.</p>

  <h2>2. Information We Collect</h2>
  <ul>
    <li>Inputs you send to our endpoints/actions (e.g., search queries, commander names).</li>
    <li>Basic operational metadata (timestamps, request/response status, performance metrics).</li>
  </ul>
  <p>We do not intentionally collect highly sensitive personal data. Do not submit such data.</p>

  <h2>3. How We Use Information</h2>
  <ul>
    <li>To fulfill your requests and operate the service.</li>
    <li>To maintain security, monitor abuse, and improve reliability.</li>
  </ul>

  <h2>4. Sharing & Disclosure</h2>
  <p>We do not sell personal data. We may share limited data with service providers (e.g., hosting) under confidentiality and security obligations or when required by law.</p>

  <h2>5. Data Retention</h2>
  <p>We retain data only as long as necessary for the purposes described above or to comply with legal obligations, after which we delete or anonymize it.</p>

  <h2>6. Your Rights</h2>
  <p>Depending on your jurisdiction, you may have rights to access, correct, or delete your personal information, and to object or restrict certain processing. Contact us to exercise these rights.</p>

  <h2>7. Security</h2>
  <p>We use reasonable technical and organizational measures to protect information, but no system is 100% secure.</p>

  <h2>8. Children’s Privacy</h2>
  <p>Our service is not directed to children under 13 and we do not knowingly collect information from them.</p>

  <h2>9. Changes</h2>
  <p>We may update this policy. We will post updates here and adjust the “Last updated” date.</p>

  <h2>10. Contact</h2>
  <p>Email: <a href="mailto:{PRIVACY_CONTACT_EMAIL}">{PRIVACY_CONTACT_EMAIL}</a></p>

</body></html>"""

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class ThemeItem(BaseModel):
    name: str
    id: Optional[str] = None     # Scryfall UUID
    image: Optional[str] = None  # Optional Scryfall image URL

class ThemeCollection(BaseModel):
    header: str
    items: List[ThemeItem] = Field(default_factory=list)

class ThemeContainer(BaseModel):
    collections: List[ThemeCollection] = Field(default_factory=list)

class PageTheme(BaseModel):
    header: str
    description: str
    tags: List[str] = Field(default_factory=list)
    container: ThemeContainer
    source_url: Optional[str] = None
    error: Optional[str] = None

class HealthResponse(BaseModel):
    status: str

# -----------------------------------------------------------------------------
# Commander Page Snapshot Helpers
# -----------------------------------------------------------------------------


@dataclass
class CommanderPageSnapshot:
    """In-memory representation of commander page metadata."""

    url: str
    html: str
    tags: List[str]
    json_payload: Optional[Dict[str, Any]] = None


# -----------------------------------------------------------------------------
# App & Clients
# -----------------------------------------------------------------------------
app = FastAPI(
    title="Mightstone GPT Webservice",
    version="1.0.0",
    description="Scryfall + EDHREC helper API for CommanderGPT",
)

# CORS (adjust to your frontends as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

http_timeout = httpx.Timeout(20.0, connect=10.0)
default_headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9"}

@app.on_event("startup")
async def on_startup():
    app.state.client = httpx.AsyncClient(timeout=http_timeout, headers=default_headers, http2=False)
    app.state.scryfall = httpx.AsyncClient(timeout=http_timeout, headers={"User-Agent": USER_AGENT}, http2=False)
    log.info("HTTP clients created.")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await app.state.client.aclose()
    except Exception:
        pass
    try:
        await app.state.scryfall.aclose()
    except Exception:
        pass
    log.info("HTTP clients closed.")

# -----------------------------------------------------------------------------
# Helpers: EDHREC (Next.js) tag/theme scraping via JSON
# -----------------------------------------------------------------------------
_build_id_rx = re.compile(r'"buildId"\s*:\s*"([^"]+)"')


def _camel_or_snake_to_title(value: str) -> str:
    value = value or ""
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    header_aliases = {
        "signaturecards": "Signature Cards",
        "popularcards": "Top Cards",
        "topcards": "Top Cards",
        "highsynergycards": "High Synergy Cards",
        "synergycards": "High Synergy Cards",
        "newcards": "New Cards",
        "newcommanders": "New Commanders",
        "topcommanders": "Top Commanders",
        "toppartners": "Top Partners",
        "combocards": "Combo Cards",
        "combos": "Combos",
        "cardviews": "Cardviews",
        "cards": "Cards",
    }
    if normalized in header_aliases:
        return header_aliases[normalized]

    spaced = re.sub(r"[_\-]+", " ", value)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", spaced)
    spaced = re.sub(r"(?i)(cards)$", " Cards", spaced)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    return spaced.title() if spaced else "Cards"

async def _fetch_text(url: str) -> str:
    log.info('HTTP GET %s', url)
    try:
        response = await app.state.client.get(url, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        if status_code == 404:
            raise HTTPException(status_code=404, detail=f"Resource not found ({url})") from exc
        raise HTTPException(status_code=502, detail=f"Upstream fetch failed ({status_code} {url})") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed ({url})") from exc
    return response.text


async def _fetch_json(url: str) -> Any:
    log.info('HTTP GET %s', url)
    try:
        response = await app.state.client.get(url, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        if status_code == 404:
            raise HTTPException(status_code=404, detail=f"Resource not found ({url})") from exc
        raise HTTPException(status_code=502, detail=f"Upstream JSON fetch failed ({status_code} {url})") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream JSON request failed ({url})") from exc

    try:
        return response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Invalid JSON from {url}") from exc

def _snakecase(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _extract_title_description_from_head(html: str) -> Tuple[str, str]:
    title = ""
    desc = ""
    # crude extraction to avoid BS4 dependency at runtime
    m_title = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if m_title:
        title = _snakecase(re.sub(r"<.*?>", "", m_title.group(1)))
    m_desc = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html, flags=re.IGNORECASE | re.DOTALL
    )
    if m_desc:
        desc = _snakecase(m_desc.group(1))
    return title or "Unknown", desc or ""

def _walk_for_named_arrays(obj: Any) -> Dict[str, List[str]]:
    """
    Heuristic: EDHREC Next.js JSON often has arrays of objects with a 'name' field
    under keys like 'cardviews' or 'cards'. We scan the JSON tree and aggregate.
    Returns {'Cardviews': [...names], 'Cards': [...names], ...}
    """
    buckets: Dict[str, List[str]] = {}
    def walk(node: Any, current_key: Optional[str] = None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            # If this looks like a list of cardish dicts with 'name'
            names: List[str] = []
            for el in node:
                if isinstance(el, dict) and "name" in el and isinstance(el["name"], str):
                    names.append(_snakecase(el["name"]))
            if names and current_key:
                # Normalize known headers
                header = "Cardviews" if "cardview" in current_key.lower() else (
                    "Cards" if current_key.lower() == "cards" else current_key.title()
                )
                buckets.setdefault(header, [])
                buckets[header].extend(names)
            # keep walking lists (in case nested)
            for el in node:
                walk(el, current_key)
        # primitives are ignored
    walk(obj)
    # de-dup while preserving order
    for k, vals in list(buckets.items()):
        seen = set()
        uniq = []
        for n in vals:
            if n not in seen:
                uniq.append(n)
                seen.add(n)
        buckets[k] = uniq
    return buckets


def _commander_item_from_entry(entry: Any) -> Optional[ThemeItem]:
    if not isinstance(entry, dict):
        return None

    name: Optional[str] = None
    scryfall_id: Optional[str] = None
    image_url: Optional[str] = None

    card = entry.get("card")
    if isinstance(card, dict):
        name = card.get("name") or card.get("label")
        scryfall_id = card.get("scryfall_id") or card.get("scryfallId") or card.get("id")
        image_field = card.get("image") or card.get("image_url") or card.get("imageUri")
        if isinstance(image_field, str):
            image_url = image_field
        elif isinstance(image_field, dict):
            image_url = image_field.get("normal") or image_field.get("large") or image_field.get("art")
        elif isinstance(card.get("image_uris"), dict):
            image_uris = card.get("image_uris")
            image_url = image_uris.get("normal") or image_uris.get("large")

    if not name:
        name = entry.get("name") or entry.get("label")
    if not name:
        return None

    item = ThemeItem(name=name)
    scryfall_id = scryfall_id or entry.get("scryfall_id") or entry.get("scryfallId")
    if isinstance(scryfall_id, str) and scryfall_id:
        item.id = scryfall_id

    if not image_url:
        image_field = entry.get("image") or entry.get("image_url") or entry.get("imageUri")
        if isinstance(image_field, str):
            image_url = image_field
        elif isinstance(image_field, dict):
            image_url = image_field.get("normal") or image_field.get("large")
        elif isinstance(entry.get("image_uris"), dict):
            image_uris = entry.get("image_uris")
            image_url = image_uris.get("normal") or image_uris.get("large")

    if isinstance(image_url, str) and image_url:
        item.image = image_url

    return item


def _extract_commander_buckets(data: Any) -> Dict[str, List[ThemeItem]]:
    buckets: Dict[str, List[ThemeItem]] = {}
    visited_lists: Set[int] = set()

    def walk(node: Any, path: List[str]):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + [key])
            return

        if isinstance(node, list):
            node_id = id(node)
            if node_id in visited_lists:
                return
            visited_lists.add(node_id)

            items: List[ThemeItem] = []
            for element in node:
                item = _commander_item_from_entry(element)
                if item:
                    items.append(item)

            if items:
                key = path[-1] if path else "cards"
                header = _camel_or_snake_to_title(key)
                existing = buckets.setdefault(header, [])
                existing_names = {it.name for it in existing}
                for item in items:
                    if item.name not in existing_names:
                        existing.append(item)
                        existing_names.add(item.name)

            for element in node:
                walk(element, path)

    if isinstance(data, dict):
        page_props = data.get("pageProps")
        if isinstance(page_props, dict) and "data" in page_props:
            walk(page_props.get("data"), [])
        else:
            walk(data, [])
    else:
        walk(data, [])

    return buckets


def _order_commander_headers(keys: List[str]) -> List[str]:
    preferred = [
        "Signature Cards",
        "High Synergy Cards",
        "Top Cards",
        "New Cards",
        "Top Partners",
        "Top Commanders",
        "New Commanders",
        "Combo Cards",
        "Combos",
    ]
    ordered: List[str] = []
    seen: Set[str] = set()
    for name in preferred:
        if name in keys and name not in seen:
            ordered.append(name)
            seen.add(name)
    for key in keys:
        if key not in seen:
            ordered.append(key)
            seen.add(key)
    return ordered


def _payload_has_collections(payload: Optional[Dict[str, Any]]) -> bool:
    if not payload:
        return False
    container = payload.get("container") if isinstance(payload, dict) else None
    if isinstance(container, ThemeContainer):
        collections = container.collections
    elif isinstance(container, dict):
        collections = container.get("collections")
    else:
        return False

    if not isinstance(collections, list):
        return False

    for collection in collections:
        if isinstance(collection, ThemeCollection):
            if collection.items:
                return True
        elif isinstance(collection, dict):
            items = collection.get("items")
            if isinstance(items, list) and items:
                return True
    return False


async def _fetch_commander_page_snapshot(slug: str) -> Optional[CommanderPageSnapshot]:
    commander_url = f"{EDHREC_BASE}/commanders/{slug}"
    try:
        html = await _fetch_text(commander_url)
    except HTTPException:
        log.warning("Commander HTML fetch failed for slug %s", slug, exc_info=True)
        return None

    html_tags = extract_commander_tags_from_html(html)
    build_id = extract_build_id_from_html(html)
    json_payload: Optional[Dict[str, Any]] = None
    json_tags: List[str] = []

    if build_id:
        json_url = f"{EDHREC_BASE}/_next/data/{build_id}/commanders/{slug}.json"
        try:
            json_payload = await _fetch_json(json_url)
            json_tags = extract_commander_tags_from_json(json_payload) if json_payload else []
        except HTTPException:
            log.warning("Commander JSON fetch failed for slug %s", slug, exc_info=True)
            json_payload = None
    else:
        log.warning("No buildId discovered for commander slug %s", slug)

    tags = normalize_commander_tags(html_tags + json_tags)

    return CommanderPageSnapshot(
        url=commander_url,
        html=html,
        tags=tags,
        json_payload=json_payload,
    )


async def try_fetch_commander_synergy(
    slug: str, *, snapshot: Optional[CommanderPageSnapshot] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[CommanderPageSnapshot]]:
    if snapshot is None:
        snapshot = await _fetch_commander_page_snapshot(slug)
    if snapshot is None:
        return None, None

    header, description = _extract_title_description_from_head(snapshot.html)

    buckets = _extract_commander_buckets(snapshot.json_payload or {})
    ordered_headers = _order_commander_headers(list(buckets.keys()))
    collections: List[ThemeCollection] = []
    for header_name in ordered_headers:
        items = buckets.get(header_name, [])
        if items:
            collections.append(ThemeCollection(header=header_name, items=items))

    page = PageTheme(
        header=header or f"{slug.replace('-', ' ').title()} | EDHREC",
        description=description or "",
        tags=snapshot.tags,
        container=ThemeContainer(collections=collections),
        source_url=snapshot.url,
    )
    return page.dict(), snapshot


async def commander_summary_handler(name: str) -> Dict[str, Any]:
    display, slug, edhrec_url = normalize_commander_name(name)

    data, snapshot = await try_fetch_commander_synergy(slug=slug)
    tags: List[str] = snapshot.tags if snapshot else []
    source_url = snapshot.url if snapshot else edhrec_url

    if not _payload_has_collections(data):
        if not tags:
            snapshot = snapshot or await _fetch_commander_page_snapshot(slug)
            if snapshot:
                tags = snapshot.tags
                source_url = snapshot.url
        fallback_page = PageTheme(
            header=f"{display} | EDHREC",
            description="",
            tags=tags,
            container=ThemeContainer(collections=[]),
            source_url=source_url,
            error=f"Synergy unavailable for {display}",
        )
        return fallback_page.dict()

    if isinstance(data, dict):
        data.setdefault("header", f"{display} | EDHREC")
        data.setdefault("description", "")
        container = data.get("container")
        if isinstance(container, ThemeContainer):
            data["container"] = container.dict()
        elif not isinstance(container, dict):
            data["container"] = {"collections": []}
        if not tags:
            tags_value = data.get("tags")
            if isinstance(tags_value, list):
                tags = normalize_commander_tags(tags_value)
            elif isinstance(tags_value, str):
                tags = normalize_commander_tags([tags_value])
            else:
                snapshot = snapshot or await _fetch_commander_page_snapshot(slug)
                if snapshot:
                    tags = snapshot.tags
                    source_url = snapshot.url
        data["tags"] = tags
        data.setdefault("source_url", source_url)
        return data

    # Final guard: coerce to PageTheme structure
    if not tags:
        snapshot = snapshot or await _fetch_commander_page_snapshot(slug)
        if snapshot:
            tags = snapshot.tags
            source_url = snapshot.url
    page = PageTheme(
        header=f"{display} | EDHREC",
        description="",
        tags=tags,
        container=ThemeContainer(collections=[]),
        source_url=source_url,
    )
    return page.dict()

async def _fetch_theme_resources(name: str, identity: str) -> Dict[str, Any]:
    tag_slug = (name or "").strip().lower()
    try:
        _code, label, color_slug = canonicalize_identity(identity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tag_html_url = f"{EDHREC_BASE}/tags/{tag_slug}/{color_slug}"
    html = await _fetch_text(tag_html_url)
    header, description = _extract_title_description_from_head(html)

    build_id = extract_build_id_from_html(html)
    if not build_id:
        json_url = f"{EDHREC_BASE}/_next/data/{'C2WISSDrnMBiFoK_iJlSk'}/tags/{tag_slug}/{color_slug}.json"
    else:
        json_url = f"{EDHREC_BASE}/_next/data/{build_id}/tags/{tag_slug}/{color_slug}.json"

    data = await _fetch_json(json_url)

    return {
        "tag_slug": tag_slug,
        "color_slug": color_slug,
        "label": label,
        "tag_html_url": tag_html_url,
        "json_url": json_url,
        "header": header,
        "description": description,
        "data": data,
    }


async def fetch_theme_tag(name: str, identity: str) -> PageTheme:
    """
    Pulls the EDHREC Tag (e.g., /tags/prowess/jeskai) Next.js JSON and builds a PageTheme.
    """
    resources = await _fetch_theme_resources(name, identity)

    # Heuristic extraction
    buckets = _walk_for_named_arrays(resources["data"])
    collections: List[ThemeCollection] = []
    # Prefer Cardviews + Cards if present
    ordered_keys = []
    if "Cardviews" in buckets:
        ordered_keys.append("Cardviews")
    if "Cards" in buckets:
        ordered_keys.append("Cards")
    # include any other buckets we found
    ordered_keys += [k for k in buckets.keys() if k not in ordered_keys]

    for k in ordered_keys:
        items = [ThemeItem(name=n) for n in buckets[k]]
        collections.append(ThemeCollection(header=k, items=items))

    header = resources["header"]
    if not header:
        header = f"{resources['label']} {resources['tag_slug'].title()} | EDHREC"

    return PageTheme(
        header=header,
        description=resources["description"],
        container=ThemeContainer(collections=collections),
        source_url=resources["tag_html_url"],
    )

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return HTMLResponse(content=PRIVACY_HTML, media_type="text/html; charset=utf-8")


# Maintain the legacy underscore route for backward compatibility but prefer the hyphenated path.
@app.get("/edhrec/average-deck")
@app.get("/edhrec/average_deck", include_in_schema=False)
def edhrec_average_deck(
    name: Optional[str] = Query(None, description="Commander name (printed name)"),
    bracket: Optional[str] = Query(
        None,
        description="Average deck bracket (e.g., all, exhibition, exhibition/budget, upgraded)",
    ),
    source_url: Optional[str] = Query(
        None,
        description="Direct EDHREC average-decks URL (skips discovery)",
    ),
):
    normalized_name = name.strip() if isinstance(name, str) else None
    normalized_bracket = bracket.strip().lower() if isinstance(bracket, str) else None

    if not source_url:
        if not normalized_name:
            raise HTTPException(
                status_code=400,
                detail={"code": "NAME_REQUIRED", "message": "Commander name is required"},
            )
        if not normalized_bracket:
            raise HTTPException(
                status_code=400,
                detail={"code": "BRACKET_REQUIRED", "message": "Bracket is required"},
            )

    session = requests.Session()
    try:
        payload = fetch_average_deck(
            name=normalized_name,
            bracket=normalized_bracket,
            source_url=source_url,
            session=session,
        )
    except ValueError as exc:
        detail = exc.args[0] if exc.args else str(exc)
        if isinstance(detail, dict):
            raise HTTPException(status_code=400, detail=detail) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EdhrecError as exc:
        return {"error": exc.to_dict()}
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - safeguard
        raise HTTPException(status_code=502, detail=f"Failed to fetch average deck: {exc}") from exc
    finally:
        session.close()

    response: Dict[str, Any] = {
        "cards": payload.get("cards", []),
        "commander_card": payload.get("commander_card"),
        "meta": {
            "source_url": payload.get("source_url"),
            "resolved_bracket": payload.get("bracket"),
            "request": {
                "name": name,
                "bracket": bracket,
                "source_url": source_url,
            },
            "commander_tags": payload.get("commander_tags", []),
            "commander_high_synergy_cards": payload.get(
                "commander_high_synergy_cards", []
            ),
            "commander_top_cards": payload.get("commander_top_cards", []),
            "commander_game_changers": payload.get(
                "commander_game_changers", []
            ),
        },
        "error": None,
    }

    if payload.get("commander"):
        response["meta"]["commander"] = payload["commander"]
    if "available_brackets" in payload:
        response["meta"]["available_brackets"] = payload["available_brackets"]

    return response


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.get("/commander/summary", response_model=PageTheme)
async def commander_summary(
    name: str = Query(..., description="Commander name (raw string, partners, MDFCs supported)"),
):
    try:
        payload = await commander_summary_handler(name)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Commander summary fetch failed.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return PageTheme.parse_obj(payload)


@app.get("/edhrec/theme", response_model=PageTheme)
async def edhrec_theme(
    name: str = Query(..., description="EDHREC tag/theme name, e.g. 'prowess'"),
    identity: str = Query(..., description="Color identity (e.g., 'wur' -> Jeskai)"),
):
    """
    Returns a best-effort PageTheme for the EDHREC *tag* page (e.g., /tags/prowess/jeskai).
    """
    try:
        return await fetch_theme_tag(name, identity)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Theme fetch failed.")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/edhrec/theme_nextdebug")
async def edhrec_theme_nextdebug(
    name: str = Query(..., description="EDHREC tag/theme name, e.g. 'prowess'"),
    identity: str = Query(..., description="Color identity (e.g., 'wur' -> Jeskai)"),
):
    """Return the raw EDHREC payload for debugging theme extraction."""

    try:
        resources = await _fetch_theme_resources(name, identity)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:  # pragma: no cover - defensive
        status_code = exc.response.status_code if exc.response is not None else 502
        if status_code == 404:
            raise HTTPException(status_code=404, detail="Not Found") from exc
        raise HTTPException(status_code=502, detail="Upstream EDHREC error") from exc
    except httpx.RequestError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=502, detail="Upstream EDHREC request failed") from exc
    except Exception as exc:
        log.exception("Theme debug fetch failed.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    debug_payload = {
        "tag": resources["tag_slug"],
        "identity": resources["color_slug"],
        "label": resources["label"],
        "header": resources["header"],
        "description": resources["description"],
        "tag_url": resources["tag_html_url"],
        "json_url": resources["json_url"],
        "data": resources["data"],
    }

    return JSONResponse(content=debug_payload)

@app.get("/cards/search")
async def cards_search(
    q: str = Query(..., description="Scryfall query string. Use exact names with !\"Name\" for precision."),
    limit: int = Query(10, ge=1, le=175)
):
    """
    Light wrapper around Scryfall /cards/search. Useful for client hydration or debugging.
    """
    url = f"{SCRYFALL_BASE}/cards/search"
    params = {"q": q, "order": "name", "unique": "cards", "include_extras": "true", "include_multilingual": "true"}
    log.info("Scryfall search: %s", q)
    r = await app.state.scryfall.get(url, params=params)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    data = r.json()
    # Truncate to 'limit'
    if "data" in data and isinstance(data["data"], list):
        data["data"] = data["data"][:limit]
    return data

# -----------------------------------------------------------------------------
# Entrypoint (when run directly)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
