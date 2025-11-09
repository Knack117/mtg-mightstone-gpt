# app.py
import os
import re
import time
import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Tuple, Iterable

import httpx
import hishel
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Mightstone (EDHREC bindings)
from mightstone.services.edhrec import EdhRecStatic, EdhRecProxiedStatic

# =============================================================================
# Config
# =============================================================================
APP_NAME = "mtg-deckbuilding-mightstone"
APP_VERSION = os.environ.get("RENDER_GIT_COMMIT", "dev")

USER_AGENT = os.environ.get(
    "HTTP_USER_AGENT",
    f"{APP_NAME}/{APP_VERSION} (+https://render.com)"
)
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "30"))

CACHE_DIR = (
    os.environ.get("MIGHTSTONE_CACHE_DIR")
    or os.environ.get("MIGHTSTONE_CACHE")
    or "/var/mightstone/cache"
)
os.makedirs(CACHE_DIR, exist_ok=True)

# Upstreams
SPELLBOOK_BASE = "https://backend.commanderspellbook.com/api/combos"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
SCRYFALL_AUTOCOMPLETE_URL = "https://api.scryfall.com/cards/autocomplete"

# Browser-ish headers for EDHREC HTML fetches
BROWSER_HEADERS = {
    "User-Agent": os.environ.get(
        "BROWSER_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

# =============================================================================
# Cached HTTP transport (Hishel + httpx)
# =============================================================================
storage = hishel.AsyncFileStorage(base_path=CACHE_DIR)
controller = hishel.Controller(
    cacheable_methods=["GET"],
    cacheable_status_codes=[200],
)
base_transport = httpx.AsyncHTTPTransport(retries=2)
cache_transport = hishel.AsyncCacheTransport(
    transport=base_transport,
    storage=storage,
    controller=controller,
)
DEFAULT_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# =============================================================================
# FastAPI app
# =============================================================================
app = FastAPI(title="Mightstone Bridge", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logger
LOG_LEVEL = os.environ.get("MIGHTSTONE_LOG_LEVEL") or ("DEBUG" if os.environ.get("MIGHTSTONE_DEBUG") else "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mightstone-bridge")

# =============================================================================
# Clients
# =============================================================================
edh = EdhRecStatic(transport=cache_transport)

# =============================================================================
# Identity mapping (letters -> EDHREC segment)
# =============================================================================
def _normalize_identity(identity: str) -> str:
    letters = [ch for ch in identity.lower() if ch in "wubrgc"]
    out = []
    for ch in letters:
        if ch not in out:
            out.append(ch)
    return "".join(out)

_ID_MONO = {"w": "white", "u": "blue", "b": "black", "r": "red", "g": "green", "c": "colorless"}
_ID_GUILDS = {
    frozenset("wu"): "azorius", frozenset("ub"): "dimir",   frozenset("br"): "rakdos",
    frozenset("rg"): "gruul",   frozenset("gw"): "selesnya",frozenset("wb"): "orzhov",
    frozenset("ur"): "izzet",   frozenset("bg"): "golgari", frozenset("rw"): "boros",
    frozenset("gu"): "simic",
}
_ID_SHARDS_WEDGES = {
    frozenset("wub"): "esper",  frozenset("ubr"): "grixis", frozenset("brg"): "jund",
    frozenset("wrg"): "naya",   frozenset("wug"): "bant",   frozenset("wur"): "jeskai",
    frozenset("ubg"): "sultai", frozenset("wbr"): "mardu",  frozenset("urg"): "temur",
    frozenset("wbg"): "abzan",
}

def _identity_to_edhrec_segment(identity: str) -> str:
    ident = _normalize_identity(identity)
    s = frozenset(ch for ch in ident if ch in "wubrg")
    if s == frozenset("wubrg"):
        return "five-color"
    if ident in _ID_MONO:
        return _ID_MONO[ident]
    if s in _ID_GUILDS:
        return _ID_GUILDS[s]
    if s in _ID_SHARDS_WEDGES:
        return _ID_SHARDS_WEDGES[s]
    return ident or "colorless"

# =============================================================================
# Helpers (Scryfall + Spellbook)
# =============================================================================
def _card_lite_from_scryfall_card(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": c.get("name"),
        "id": c.get("id"),
        "type_line": c.get("type_line"),
        "ci": c.get("color_identity"),
        "cmc": c.get("cmc"),
        "set": c.get("set"),
        "set_name": c.get("set_name"),
        "collector_number": c.get("collector_number"),
    }

def _normalize_theme_name(name: str) -> str:
    return re.sub(r"\s+", "-", name.strip().lower())

async def _scryfall_search(q: str, limit: int) -> List[Dict[str, Any]]:
    client: httpx.AsyncClient = app.state.httpx_client
    r = await client.get(SCRYFALL_SEARCH_URL, params={"q": q})
    r.raise_for_status()
    payload = r.json()
    cards = payload.get("data", [])
    return [_card_lite_from_scryfall_card(c) for c in cards[: max(1, min(limit, 100))]]

async def _scryfall_autocomplete(q: str, include_extras: bool) -> List[str]:
    params = {"q": q}
    if include_extras:
        params["include_extras"] = "true"
    client: httpx.AsyncClient = app.state.httpx_client
    r = await client.get(SCRYFALL_AUTOCOMPLETE_URL, params=params)
    r.raise_for_status()
    payload = r.json()
    return payload.get("data", [])

async def _scryfall_first(q: str) -> Optional[Dict[str, Any]]:
    client: httpx.AsyncClient = app.state.httpx_client
    r = await client.get(SCRYFALL_SEARCH_URL, params={"q": q})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None

def _spellbook_search(q: str, limit: int) -> List[Dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    lim = str(max(1, min(int(limit), 100)))
    params = {"limit": lim}
    if q:
        params["q"] = q
    for attempt in range(5):
        r = httpx.get(SPELLBOOK_BASE, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        if r.status_code != 429:
            r.raise_for_status()
            payload = r.json()
            return payload.get("data", payload)
        time.sleep(0.5 * (2 ** attempt))
    raise HTTPException(status_code=429, detail="Commander Spellbook rate limited; try again shortly.")

def _to_dict(obj) -> dict:
    try:
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            return obj.model_dump()
        if hasattr(obj, "dict") and callable(obj.dict):
            return obj.dict()
        if is_dataclass(obj):
            return asdict(obj)
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
        try:
            return json.loads(json.dumps(obj))
        except Exception:
            return {}
    except Exception as e:
        logger.exception("Serialization failed: %s", e)
        return {}

# =============================================================================
# JSON traversal helpers for Next.js payloads
# =============================================================================
def _walk(obj: Any) -> Iterable[Any]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)

def _find_first_text(obj: Any, keys: List[str]) -> Optional[str]:
    for node in _walk(obj):
        if isinstance(node, dict):
            for k in keys:
                v = node.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None

def _collect_cards_from_next(obj: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    def visit(x):
        if isinstance(x, dict):
            name = x.get("name") or x.get("cardname") or x.get("card_name")
            if isinstance(name, str) and name.strip():
                img = x.get("image") or x.get("image_normal") or x.get("image_url")
                cid = x.get("scryfall_id") or x.get("id")
                out.append({"name": name.strip(), "id": cid, "image": img})
            for v in x.values():
                visit(v)
        elif isinstance(x, list):
            for v in x:
                visit(v)
    visit(obj)
    seen = set()
    dedup: List[Dict[str, Any]] = []
    for it in out:
        nm = it.get("name")
        if nm and nm not in seen:
            seen.add(nm)
            dedup.append(it)
    return dedup

def _dedup_by_name(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        nm = (it or {}).get("name")
        if nm and nm not in seen:
            seen.add(nm)
            out.append(it)
    return out

def _collections_from_next(page_obj: dict) -> List[Dict[str, Any]]:
    """
    Discover collections of cards in flexible Next.js payloads.
    Strategy:
      1) Known buckets by common keys.
      2) Scan for arrays of dicts that look like cards (have 'name'/'card_name'),
         or objects that wrap such arrays under keys like 'cards', 'cardviews', 'results'.
      3) Synthesize headers from nearby keys or fall back to generic ones.
    """
    cols: List[Dict[str, Any]] = []

    # 1) Known buckets first
    known = [
        ("High Synergy", ["high_synergy", "highSynergy", "high_synergy_cards"]),
        ("Top Cards",    ["top", "top_cards", "signature", "signature_cards"]),
        ("Commanders",   ["commanders", "leaders"]),
        ("New Cards",    ["new_cards", "recent_cards"]),
    ]
    for header, keys in known:
        for k in keys:
            if isinstance(page_obj, dict) and k in page_obj:
                items = _collect_cards_from_next(page_obj[k])
                if items:
                    cols.append({"header": header, "items": items})
                    break

    # 2) Wide scan: any arrays of card-like dicts, or nested objects containing such arrays.
    candidate_array_keys = {
        "cards", "cardviews", "card_views", "cardlist", "card_list", "results",
        "items", "entries", "data", "rows"
    }
    probable_section_keys = {
        "panels", "sections", "panelGroups", "groups", "containers",
        "content", "body", "blocks", "widgets", "lists"
    }

    def headerize(key: Optional[str]) -> str:
        if not key:
            return "Cards"
        return str(key).replace("_", " ").replace("-", " ").title()

    # walk every dict; try direct arrays and nested objects holding arrays
    for node in _walk(page_obj):
        if not isinstance(node, dict):
            continue

        # (a) direct arrays that look like cards
        for k, v in list(node.items()):
            if isinstance(v, list) and v and isinstance(v[0], dict) and ("name" in v[0] or "card_name" in v[0]):
                items = _collect_cards_from_next(v)
                if items:
                    cols.append({"header": headerize(k), "items": items})

        # (b) objects that wrap arrays under card-ish keys
        for k in candidate_array_keys:
            wrapped = node.get(k)
            if isinstance(wrapped, list) and wrapped and isinstance(wrapped[0], dict):
                if "name" in wrapped[0] or "card_name" in wrapped[0] or "card" in wrapped[0]:
                    items = _collect_cards_from_next(wrapped)
                    if items:
                        cols.append({"header": headerize(k), "items": items})

        # (c) containers of sections
        for sk in probable_section_keys:
            maybe_sections = node.get(sk)
            if isinstance(maybe_sections, list):
                for sec in maybe_sections:
                    if isinstance(sec, dict):
                        found_header = sec.get("title") or sec.get("header") or sec.get("name") or sk
                        best_items: List[Dict[str, Any]] = []
                        for inner_k, inner_v in list(sec.items()):
                            if isinstance(inner_v, list) and inner_v and isinstance(inner_v[0], dict) and (
                                "name" in inner_v[0] or "card_name" in inner_v[0] or "card" in inner_v[0]
                            ):
                                best_items = _collect_cards_from_next(inner_v)
                                if best_items:
                                    break
                            if isinstance(inner_v, dict):
                                for ik2, iv2 in list(inner_v.items()):
                                    if isinstance(iv2, list) and iv2 and isinstance(iv2[0], dict) and (
                                        "name" in iv2[0] or "card_name" in iv2[0] or "card" in iv2[0]
                                    ):
                                        best_items = _collect_cards_from_next(iv2)
                                        if best_items:
                                            break
                        if best_items:
                            cols.append({"header": headerize(found_header), "items": best_items})

    # Merge duplicate headers and dedup items by name
    merged: Dict[str, List[Dict[str, Any]]] = {}
    for c in cols:
        merged.setdefault(c["header"], []).extend(c["items"])
    cols = [{"header": h, "items": _dedup_by_name(v)} for h, v in merged.items()]

    # Sort by size (largest first) for nicer display
    cols.sort(key=lambda c: len(c["items"]), reverse=True)
    return cols

# =============================================================================
# EDHREC Theme Extraction (Next.js build JSON + inline + mirror + HTML meta)
# =============================================================================
async def _fetch_next_inline(client: httpx.AsyncClient, url: str, headers: dict) -> Tuple[Optional[str], Optional[dict], Optional[str]]:
    """
    Return (buildId, inlinePageObj, titleFromInline) from inline __NEXT_DATA__ if present.
    """
    r = await client.get(url, headers=headers, follow_redirects=True)
    if r.status_code != 200 or not r.text:
        return None, None, None
    soup = BeautifulSoup(r.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__", type="application/json")
    title_tag = soup.select_one("title")
    title_inline = title_tag.get_text(strip=True) if title_tag else None
    if not script or not script.text:
        return None, None, title_inline
    try:
        data = json.loads(script.text)
        build_id = data.get("buildId")
        inline_obj = (
            data.get("props", {}).get("pageProps")
            or data.get("pageProps")
            or data.get("data")
            or data
        )
        return build_id, inline_obj, title_inline
    except Exception:
        return None, None, title_inline

async def _fetch_next_page_json(client: httpx.AsyncClient, build_id: str, theme_slug: str, segment: str, headers: dict) -> Optional[dict]:
    """
    Fetch the concrete Next.js JSON:
    /_next/data/<buildId>/tags/<theme>/<segment>.json
    Return the most useful dict (pageProps/props.pageProps/data/root).
    """
    next_url = f"https://edhrec.com/_next/data/{build_id}/tags/{theme_slug}/{segment}.json"
    r = await client.get(next_url, headers=headers, follow_redirects=True)
    if r.status_code != 200:
        return None
    try:
        data = r.json()
        return (
            data.get("pageProps")
            or data.get("props", {}).get("pageProps")
            or data.get("data")
            or data
        )
    except Exception:
        return None

async def _fetch_json_mirror(client: httpx.AsyncClient, theme_slug: str, segment: str, headers: dict) -> Optional[dict]:
    """
    Attempt the json.edhrec.com mirror (may return 403). Safe to ignore on failure.
    """
    json_url = f"https://json.edhrec.com/tags/{theme_slug}/{segment}.json"
    r = await client.get(json_url, headers=headers, follow_redirects=True)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None

async def _parse_edhrec_theme(theme_slug: str, identity: str) -> dict:
    segment = _identity_to_edhrec_segment(identity)
    page_url = f"https://edhrec.com/tags/{theme_slug}/{segment}"
    client: httpx.AsyncClient = app.state.httpx_client

    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = f"https://edhrec.com/tags/{segment}"

    header = ""
    description = ""
    collections: List[Dict[str, Any]] = []

    # 1) Inline __NEXT_DATA__ (get buildId + inline object)
    build_id, inline_obj, title_inline = await _fetch_next_inline(client, page_url, headers=headers)
    if title_inline and not header:
        header = title_inline
    if inline_obj:
        description = _find_first_text(inline_obj, ["description", "pageDescription"]) or description
        inline_cols = _collections_from_next(inline_obj)
        if inline_cols and not collections:
            collections = inline_cols

    # 2) If we have buildId, pull the dedicated page JSON and extract from it
    next_obj = None
    if build_id:
        next_obj = await _fetch_next_page_json(client, build_id, theme_slug, segment, headers=headers)
        if next_obj:
            if not header:
                header = _find_first_text(next_obj, ["title", "header", "pageTitle"]) or header
            if not description:
                description = _find_first_text(next_obj, ["description", "pageDescription"]) or description
            next_cols = _collections_from_next(next_obj)
            # Prefer whichever source yields more content
            if (next_cols and len(next_cols) > len(collections)) or not collections:
                collections = next_cols

    # 3) If still empty, try the json.edhrec.com mirror
    if not collections:
        mirror = await _fetch_json_mirror(client, theme_slug, segment, headers=headers)
        if mirror:
            if not header:
                header = _find_first_text(mirror, ["title", "header", "pageTitle"]) or header
            if not description:
                description = _find_first_text(mirror, ["description", "pageDescription"]) or description
            collections = _collections_from_next(mirror)

    # 4) Minimal HTML meta fallback (title/description)
    if not description or not header:
        r = await client.get(page_url, headers=headers, follow_redirects=True)
        soup = BeautifulSoup(r.text or "", "html.parser")
        if not header:
            t = soup.select_one("title")
            if t and t.get_text(strip=True):
                header = t.get_text(strip=True)
        if not description:
            md = soup.select_one('meta[name="description"]')
            if md and md.get("content"):
                description = md["content"].strip()

    # Dedup items
    for col in collections:
        col["items"] = _dedup_by_name(col.get("items", []) or [])

    logger.debug("[EDHREC THEME] url=%s header=%s desc_len=%d sections=%d",
                 page_url, header, len(description or ""), len(collections))
    return {"header": header or "Unknown", "description": description or "", "container": {"collections": collections}}

def _normalize_page_theme_payload(data: dict) -> dict:
    header = (data or {}).get("header") or "Unknown"
    description = (data or {}).get("description") or ""
    container = (data or {}).get("container") or {}
    collections = container.get("collections") or container.get("sections") or []

    norm_collections: List[Dict[str, Any]] = []
    if isinstance(collections, list):
        for sec in collections:
            sec_header = ""
            items_src = []
            if isinstance(sec, dict):
                sec_header = sec.get("header") or sec.get("title") or ""
                items_src = sec.get("items") or sec.get("cardviews") or sec.get("cards") or []
            items = []
            if isinstance(items_src, list):
                for it in items_src:
                    if isinstance(it, dict):
                        items.append({
                            "name": it.get("name") or it.get("card_name") or "",
                            "id": it.get("id") or it.get("scryfall_id"),
                            "image": it.get("image") or it.get("image_normal"),
                        })
                    else:
                        items.append({"name": str(it), "id": None, "image": None})
            norm_collections.append({"header": sec_header, "items": _dedup_by_name(items)})

    return {"header": header, "description": description, "container": {"collections": norm_collections}}

# =============================================================================
# Routes
# =============================================================================
@app.get("/health")
async def health():
    return {"ok": True, "version": APP_VERSION, "cache_dir": CACHE_DIR, "ua": USER_AGENT,
            "services": {"scryfall": True, "edhrec": True, "spellbook": True}}

# ---- Scryfall ---------------------------------------------------------------
@app.get("/cards/search")
async def cards_search(q: str = Query(..., description="Scryfall search string"), limit: int = 25):
    try:
        return await _scryfall_search(q, limit)
    except httpx.HTTPStatusError as e:
        text = ""
        try: text = e.response.text[:200]
        except Exception: pass
        raise HTTPException(status_code=e.response.status_code, detail=f"scryfall error: {text}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"search error: {e}")

@app.get("/scryfall/autocomplete")
async def scryfall_autocomplete(
    q: str = Query(..., min_length=1, description="Partial card name"),
    include_extras: bool = Query(False, description="Include funny/extra cards"),
):
    try:
        names = await _scryfall_autocomplete(q, include_extras=include_extras)
        return {"data": names}
    except httpx.HTTPStatusError as e:
        text = ""
        try: text = e.response.text[:200]
        except Exception: pass
        raise HTTPException(status_code=e.response.status_code, detail=f"Scryfall error: {text}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Scryfall error: {e!s}")

@app.get("/legal_printings")
async def legal_printings(name: str = Query(..., description="Exact card name")):
    try:
        base = await _scryfall_first(f'!"{name}"')
        if not base:
            raise HTTPException(status_code=404, detail=f'Card not found: "{name}"')
        client: httpx.AsyncClient = app.state.httpx_client
        r = await client.get(SCRYFALL_SEARCH_URL, params={"q": f'!"{base["name"]}" include:extras unique:prints'})
        r.raise_for_status()
        prints_payload = r.json().get("data", [])
        return {"name": base["name"], "prints": [
            {"id": p.get("id"), "set": p.get("set"), "set_name": p.get("set_name"), "collector_number": p.get("collector_number")}
            for p in prints_payload
        ]}
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        text = ""
        try: text = e.response.text[:200]
        except Exception: pass
        raise HTTPException(status_code=e.response.status_code, detail=f"scryfall error: {text}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"printings error: {e}")

# ---- Commander summary (Scryfall + EDHREC) ----------------------------------
@app.get("/commander/summary")
async def commander_summary(name: str = Query(..., description="Commander name (exact or close)")):
    try:
        commander_card = await _scryfall_first(f'!"{name}" legal:commander game:paper')
        if not commander_card:
            raise HTTPException(status_code=404, detail=f'Commander not found: "{name}"')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"scryfall error: {e}")

    edhrec_summary: Dict[str, Any] = {"high_synergy": [], "top_cards": [], "average_deck_sample": []}
    try:
        page = await edh.commander_async(commander_card["name"])
        def _extract_named_list_from_edhrec(obj: Any, candidate_attrs: List[str]) -> List[str]:
            for attr in candidate_attrs:
                val = getattr(obj, attr, None)
                if not val: continue
                names: List[str] = []
                try:
                    for item in val:
                        n = getattr(item, "name", item)
                        if isinstance(n, str): names.append(n)
                except TypeError:
                    continue
                if names:
                    seen, out = set(), []
                    for n in names:
                        if n not in seen:
                            seen.add(n); out.append(n)
                    return out
            return []
        edhrec_summary["high_synergy"] = _extract_named_list_from_edhrec(page, ["high_synergy","high_synergy_cards","synergies"])[:40]
        edhrec_summary["top_cards"]    = _extract_named_list_from_edhrec(page, ["top_cards","signature","signature_cards","commander_cards"])[:60]
        avg = await edh.average_deck_async(commander_card["name"])
        sample: List[str] = []
        for attr in ["cards","main","deck","list"]:
            if hasattr(avg, attr):
                try:
                    for item in getattr(avg, attr):
                        n = getattr(item, "name", item)
                        if isinstance(n, str):
                            sample.append(n)
                        if len(sample) >= 20: break
                except TypeError:
                    pass
                break
        edhrec_summary["average_deck_sample"] = list(dict.fromkeys(sample))
    except Exception:
        pass

    return {
        "commander": {
            "name": commander_card.get("name"),
            "id": commander_card.get("id"),
            "oracle_text": commander_card.get("oracle_text"),
            "type_line": commander_card.get("type_line"),
            "color_identity": commander_card.get("color_identity"),
        },
        "edhrec": edhrec_summary,
    }

# ---- Commander Spellbook ----------------------------------------------------
@app.get("/combos")
async def combos(
    commander: Optional[str] = Query(None, description='Commander filter, e.g. "Miirym, Sentinel Wyrm"'),
    includes: Optional[List[str]] = Query(None, description='One or more card names the combo must include'),
    limit: int = 25,
):
    clauses: List[str] = []
    if commander:
        clauses.append(f'commander:"{commander}"')
    if includes:
        for n in includes:
            if n and n.strip():
                clauses.append(f'includes:"{n.strip()}"')
    q = " ".join(clauses)
    try:
        data = _spellbook_search(q, limit)
        out = []
        for c in data:
            names: List[str] = []
            for sec in ("uses", "requires"):
                for item in c.get(sec, []) or []:
                    nm = item.get("card")
                    if isinstance(nm, str) and nm not in names:
                        names.append(nm)
            if not names and isinstance(c.get("cards"), list):
                for nm in c["cards"]:
                    if isinstance(nm, str) and nm not in names:
                        names.append(nm)
            out.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "cards": names,
                "results": c.get("results"),
                "permalink": c.get("permalink") or c.get("url"),
            })
        return out
    except httpx.HTTPStatusError as e:
        txt = ""
        try: txt = e.response.text[:200]
        except Exception: pass
        raise HTTPException(status_code=e.response.status_code, detail=f"spellbook error: {txt}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"spellbook error: {e}")

# ---- EDHREC combos index ----------------------------------------------------
@app.get("/edhrec/combos")
async def edhrec_combos(identity: Optional[str] = Query(None, description="Optional color identity (e.g. 'w', 'ur', 'wubrg').")):
    try:
        id_arg = None
        if identity:
            norm = _normalize_identity(identity)
            if not norm:
                raise HTTPException(status_code=400, detail="Invalid identity; use W/U/B/R/G letters.")
            id_arg = norm
        page = await edh.combos_async(identity=id_arg)
        return page.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"EDHREC error: {e!s}")

# ---- EDHREC theme (tags) ----------------------------------------------------
@app.get("/edhrec/theme")
async def edhrec_theme(
    name: str = Query(..., description="Theme/Tag slug or name, e.g. 'prowess'"),
    identity: str = Query(..., description="Color identity letters, e.g. 'wur' for Jeskai"),
):
    theme_name = _normalize_theme_name(name)
    norm_id = _normalize_identity(identity)
    if not norm_id:
        raise HTTPException(status_code=400, detail="Invalid identity; use W/U/B/R/G letters (e.g., 'wur').")

    # Try Mightstone clients first (static, then proxied)
    try:
        page = await edh.theme_async(name=theme_name, identity=norm_id)
        raw = _to_dict(page)
        shaped = _normalize_page_theme_payload(raw)
        if shaped["header"] != "Unknown" or shaped["container"]["collections"]:
            return shaped
    except Exception as e:
        logger.info("Static theme fetch failed: %s", e)
    try:
        edh_proxy = EdhRecProxiedStatic(transport=cache_transport)
        page = await edh_proxy.theme_async(name=theme_name, identity=norm_id)
        raw = _to_dict(page)
        shaped = _normalize_page_theme_payload(raw)
        if shaped["header"] != "Unknown" or shaped["container"]["collections"]:
            return shaped
    except Exception as e2:
        logger.info("Proxied theme fetch failed: %s", e2)

    # Next.js pipeline fallback
    try:
        shaped = await _parse_edhrec_theme(theme_name, norm_id)
        return shaped
    except Exception as e3:
        logger.exception("Theme parse failed: %s", e3)
        return {"header": "Unknown", "description": "", "container": {"collections": []}}

# ---- Debug helpers ----------------------------------------------------------
@app.get("/edhrec/theme_debug")
async def edhrec_theme_debug(name: str, identity: str, preview: int = 1000):
    theme = _normalize_theme_name(name)
    segment = _identity_to_edhrec_segment(identity)
    url = f"https://edhrec.com/tags/{theme}/{segment}"
    client: httpx.AsyncClient = app.state.httpx_client
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = f"https://edhrec.com/tags/{segment}"
    r = await client.get(url, headers=headers, follow_redirects=True)
    text = r.text or ""
    return {"status": r.status_code, "url": str(r.url), "preview": text[: max(0, min(preview, 4000))]}

@app.get("/edhrec/theme_nextdebug")
async def edhrec_theme_nextdebug(name: str, identity: str):
    """
    Debug endpoint: shows buildId, whether inline/next JSON was found, the top-level keys,
    and a quick preview of discovered sections with item counts.
    """
    theme = _normalize_theme_name(name)
    segment = _identity_to_edhrec_segment(identity)
    url = f"https://edhrec.com/tags/{theme}/{segment}"
    client: httpx.AsyncClient = app.state.httpx_client

    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = f"https://edhrec.com/tags/{segment}"

    build_id, inline_obj, title_inline = await _fetch_next_inline(client, url, headers=headers)
    info: Dict[str, Any] = {
        "buildId": build_id,
        "hasInlineProps": bool(inline_obj),
        "titleInline": title_inline,
    }

    next_obj = None
    if build_id:
        next_obj = await _fetch_next_page_json(client, build_id, theme, segment, headers=headers)
        info["hasNextJson"] = bool(next_obj)
        if isinstance(next_obj, dict):
            info["nextKeys"] = list(next_obj.keys())[:50]
        else:
            info["nextKeys"] = []

    # Show which sections we’d export right now
    probe_source = inline_obj or next_obj or {}
    cols = _collections_from_next(probe_source) if isinstance(probe_source, dict) else []
    info["foundSections"] = [{"header": c["header"], "count": len(c.get("items", []))} for c in cols[:10]]

    return info

# =============================================================================
# Lifecycle
# =============================================================================
@app.on_event("startup")
async def on_startup():
    # http2=False avoids needing the 'h2' extra in httpx
    app.state.httpx_client = httpx.AsyncClient(
        transport=cache_transport,
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(10.0, connect=10.0),
        http2=False,
    )

@app.on_event("shutdown")
async def on_shutdown():
    client = getattr(app.state, "httpx_client", None)
    if client is not None:
        await client.aclose()
