# app.py
import os
import re
import time
import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

import httpx
import hishel
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Mightstone (EDHREC); Scryfall is called via REST
from mightstone.services.edhrec import EdhRecStatic, EdhRecProxiedStatic

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
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

# Browser-like headers for EDHREC HTML fetching (to avoid simple bot challenges)
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
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# -----------------------------------------------------------------------------
# Cached HTTP transport (Hishel + httpx)
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
app = FastAPI(title="Mightstone Bridge", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten if desired
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logger
LOG_LEVEL = os.environ.get("MIGHTSTONE_LOG_LEVEL") or ("DEBUG" if os.environ.get("MIGHTSTONE_DEBUG") else "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mightstone-bridge")
# Optional: very verbose deps
# logging.getLogger("httpx").setLevel(logging.DEBUG)
# logging.getLogger("hishel").setLevel(logging.DEBUG)

# -----------------------------------------------------------------------------
# Clients
# -----------------------------------------------------------------------------
# EDHREC through Mightstone (uses our cached transport)
edh = EdhRecStatic(transport=cache_transport)

# -----------------------------------------------------------------------------
# Identity mapping (letters -> EDHREC faction segment)
# -----------------------------------------------------------------------------
def _normalize_identity(identity: str) -> str:
    """Keep letters only (wubrgc), lowercase, unique."""
    letters = [ch for ch in identity.lower() if ch in "wubrgc"]
    # unique in original order
    out = []
    for ch in letters:
        if ch not in out:
            out.append(ch)
    return "".join(out)

_ID_MONO = {
    "w": "white",
    "u": "blue",
    "b": "black",
    "r": "red",
    "g": "green",
    "c": "colorless",
}

# Two-color guilds
_ID_GUILDS = {
    frozenset("wu"): "azorius",
    frozenset("ub"): "dimir",
    frozenset("br"): "rakdos",
    frozenset("rg"): "gruul",
    frozenset("gw"): "selesnya",
    frozenset("wb"): "orzhov",
    frozenset("ur"): "izzet",
    frozenset("bg"): "golgari",
    frozenset("rw"): "boros",
    frozenset("gu"): "simic",
}

# Three-color shards/wedges
_ID_SHARDS_WEDGES = {
    frozenset("wub"): "esper",
    frozenset("ubr"): "grixis",
    frozenset("brg"): "jund",
    frozenset("wrg"): "naya",
    frozenset("wug"): "bant",
    frozenset("wur"): "jeskai",
    frozenset("ubg"): "sultai",
    frozenset("wbr"): "mardu",
    frozenset("urg"): "temur",
    frozenset("wbg"): "abzan",
}

def _identity_to_edhrec_segment(identity: str) -> str:
    """
    Map color identity letters to EDHREC's path segment used under /tags/<theme>/<segment>.
    Examples: 'w'->'white', 'ub'->'dimir', 'wur'->'jeskai', 'wubrg'->'five-color'.
    Fallback to the raw letters if unknown.
    """
    ident = _normalize_identity(identity)
    s = frozenset(ch for ch in ident if ch in "wubrg")

    # Five color
    if s == frozenset("wubrg"):
        return "five-color"
    # Mono + colorless
    if ident in _ID_MONO:
        return _ID_MONO[ident]
    # Guilds
    if s in _ID_GUILDS:
        return _ID_GUILDS[s]
    # Shards/Wedges
    if s in _ID_SHARDS_WEDGES:
        return _ID_SHARDS_WEDGES[s]
    # Unknown combo -> raw letters
    return ident or "colorless"

# -----------------------------------------------------------------------------
# Helpers (Scryfall + EDHREC + Spellbook)
# -----------------------------------------------------------------------------
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
    """EDHREC uses lowercase hyphenated slugs for tags/themes."""
    return re.sub(r"\s+", "-", name.strip().lower())

def _extract_named_list_from_edhrec(obj: Any, candidate_attrs: List[str]) -> List[str]:
    for attr in candidate_attrs:
        val = getattr(obj, attr, None)
        if not val:
            continue
        names: List[str] = []
        try:
            for item in val:
                n = getattr(item, "name", item)
                if isinstance(n, str):
                    names.append(n)
        except TypeError:
            continue
        if names:
            seen, out = set(), []
            for n in names:
                if n not in seen:
                    seen.add(n); out.append(n)
            return out
    return []

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

    for attempt in range(5):  # exponential backoff on 429
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
            norm_collections.append({"header": sec_header, "items": items})

    return {"header": header, "description": description, "container": {"collections": norm_collections}}

# -----------------------------------------------------------------------------
# Hardened HTML parser (uses /tags/<theme>/<segment>)
# -----------------------------------------------------------------------------
async def _parse_edhrec_theme_html(theme_slug: str, identity: str) -> dict:
    """
    Fetches https://edhrec.com/tags/<theme>/<segment>
    where <segment> is guild/shard/wedge/mono name (e.g., 'jeskai', 'dimir', 'white', 'five-color').
    """
    segment = _identity_to_edhrec_segment(identity)
    url = f"https://edhrec.com/tags/{theme_slug}/{segment}"
    client: httpx.AsyncClient = app.state.httpx_client

    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = f"https://edhrec.com/tags/{segment}"

    r = await client.get(url, headers=headers, follow_redirects=True)
    status = r.status_code
    text_start = (r.text or "")[:600]
    logger.debug("[EDHREC FETCH] %s -> %s\n%s", url, status, text_start.replace("\n", " ")[:600])

    # Challenge/blocked detection
    if status in (403, 503) or any(
        s in text_start.lower()
        for s in (
            "cf-chl-bypass", "cloudflare", "attention required",
            "please enable javascript", "checking your browser"
        )
    ):
        logger.warning("Likely blocked/challenged by EDHREC/Cloudflare (status=%s).", status)
        return {"header": "Unknown", "description": "", "container": {"collections": []}}

    if status == 404:
        return {"header": "Unknown", "description": "", "container": {"collections": []}}

    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # ---- Header
    header = ""
    h1 = soup.select_one("h1")
    if h1 and h1.get_text(strip=True):
        header = h1.get_text(strip=True)
    if not header:
        og = soup.select_one('meta[property="og:title"]')
        if og and og.get("content"):
            header = og["content"].strip()
    if not header:
        title = soup.select_one("title")
        if title and title.get_text(strip=True):
            header = title.get_text(strip=True)
    header = header or f"{segment.title()} {theme_slug.replace('-', ' ').title()}"

    # ---- Description
    description = ""
    for sel in (".theme__description", ".theme-description", ".theme__intro", ".content__description", ".page-subtitle"):
        node = soup.select_one(sel)
        if node:
            txt = node.get_text(" ", strip=True)
            if txt:
                description = txt
                break

    # ---- Sections/Collections
    section_nodes = soup.select(".theme__panel, .card-container, .card__container, section, .section, div[data-view]")

    def extract_card_nodes(container):
        return container.select("a.card__link, span.card__name, .card__name a, .nw-card .name a, .card .name a")

    def find_img_near(node, scope):
        cand = node.find("img") or node.find_previous("img") or node.find_next("img") or scope.select_one("img")
        if not cand:
            return None
        return cand.get("data-src") or cand.get("data-original") or cand.get("src")

    collections: List[Dict[str, Any]] = []
    seen_headers = set()

    for sec in section_nodes:
        sec_header = ""
        for hdr_sel in ("h2", "h3", ".section__title", ".section-title", ".view__title", ".cards__title"):
            hnode = sec.select_one(hdr_sel)
            if hnode and hnode.get_text(strip=True):
                sec_header = hnode.get_text(strip=True)
                break
        if not sec_header:
            sec_header = sec.get("data-title", "") or sec.get("aria-label", "")

        items: List[Dict[str, Any]] = []
        for cn in extract_card_nodes(sec):
            name = cn.get_text(strip=True)
            if not name:
                continue
            img_url = find_img_near(cn, sec)
            items.append({"name": name, "id": None, "image": img_url})

        if items:
            tag = (sec_header or "Cards").strip().lower()
            if tag not in seen_headers:
                seen_headers.add(tag)
                collections.append({"header": sec_header or "Cards", "items": items})

    if not collections:
        global_cards = soup.select("a.card__link, span.card__name, .card__name a, .card .name a")
        items = []
        for cn in global_cards:
            nm = cn.get_text(strip=True)
            if nm:
                items.append({"name": nm, "id": None, "image": None})
        if items:
            collections.append({"header": "Cards", "items": items})

    # Dedup within sections
    for col in collections:
        seen = set()
        dedup: List[Dict[str, Any]] = []
        for it in col["items"]:
            nm = it.get("name", "")
            if nm and nm not in seen:
                seen.add(nm)
                dedup.append(it)
        col["items"] = dedup

    logger.debug("[EDHREC HTML PARSER] header=%s desc_len=%d sections=%d", header, len(description), len(collections))
    return {"header": header, "description": description, "container": {"collections": collections}}

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "cache_dir": CACHE_DIR,
        "ua": USER_AGENT,
        "services": {"scryfall": True, "edhrec": True, "spellbook": True},
    }

# ---- Scryfall (REST) --------------------------------------------------------
@app.get("/cards/search")
async def cards_search(q: str = Query(..., description="Scryfall search string"), limit: int = 25):
    try:
        return await _scryfall_search(q, limit)
    except httpx.HTTPStatusError as e:
        text = ""
        try:
            text = e.response.text[:200]
        except Exception:
            pass
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
        try:
            text = e.response.text[:200]
        except Exception:
            pass
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
        r = await client.get(
            SCRYFALL_SEARCH_URL,
            params={"q": f'!"{base["name"]}" include:extras unique:prints'},
        )
        r.raise_for_status()
        prints_payload = r.json().get("data", [])

        return {
            "name": base["name"],
            "prints": [
                {
                    "id": p.get("id"),
                    "set": p.get("set"),
                    "set_name": p.get("set_name"),
                    "collector_number": p.get("collector_number"),
                }
                for p in prints_payload
            ],
        }
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        text = ""
        try:
            text = e.response.text[:200]
        except Exception:
            pass
        raise HTTPException(status_code=e.response.status_code, detail=f"scryfall error: {text}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"printings error: {e}")

# ---- Commander summary (Scryfall REST + EDHREC) -----------------------------
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
        edhrec_summary["high_synergy"] = _extract_named_list_from_edhrec(
            page, ["high_synergy", "high_synergy_cards", "synergies"]
        )[:40]
        edhrec_summary["top_cards"] = _extract_named_list_from_edhrec(
            page, ["top_cards", "signature", "signature_cards", "commander_cards"]
        )[:60]

        avg = await edh.average_deck_async(commander_card["name"])
        sample: List[str] = []
        for attr in ["cards", "main", "deck", "list"]:
            if hasattr(avg, attr):
                try:
                    for item in getattr(avg, attr):
                        n = getattr(item, "name", item)
                        if isinstance(n, str):
                            sample.append(n)
                        if len(sample) >= 20:
                            break
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

# ---- Commander Spellbook (REST) ---------------------------------------------
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
        try:
            txt = e.response.text[:200]
        except Exception:
            pass
        raise HTTPException(status_code=e.response.status_code, detail=f"spellbook error: {txt}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"spellbook error: {e}")

# ---- EDHREC combos index (Mightstone) ---------------------------------------
@app.get("/edhrec/combos")
async def edhrec_combos(
    identity: Optional[str] = Query(None, description="Optional color identity (e.g. 'w', 'ur', 'wubrg')."),
):
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

# ---- EDHREC theme page (normalized + logged + HTML fallback) ----------------
@app.get("/edhrec/theme")
async def edhrec_theme(
    name: str = Query(..., description="Theme/Tag slug or name, e.g. 'prowess'"),
    identity: str = Query(..., description="Color identity letters, e.g. 'wur' for Jeskai"),
):
    """
    Try Static client, then Proxied, then direct HTML parse (now using /tags/<theme>/<segment>).
    Always return {header, description, container:{collections:[]}}.
    """
    theme_name = _normalize_theme_name(name)
    norm_id = _normalize_identity(identity)
    if not norm_id:
        raise HTTPException(status_code=400, detail="Invalid identity; use W/U/B/R/G letters (e.g., 'wur').")

    # 1) Static Mightstone
    try:
        page = await edh.theme_async(name=theme_name, identity=norm_id)
        raw = _to_dict(page)
        logger.debug("[EDHREC STATIC] theme=%s id=%s keys=%s", theme_name, norm_id, list(raw.keys())[:10])
        shaped = _normalize_page_theme_payload(raw)
        if shaped["header"] != "Unknown" or shaped["container"]["collections"]:
            return shaped
        logger.warning("Static client returned empty-ish payload; falling through.")
    except Exception as e:
        logger.info("Static theme fetch failed: %s", e)

    # 2) Proxied Mightstone
    try:
        edh_proxy = EdhRecProxiedStatic(transport=cache_transport)
        page = await edh_proxy.theme_async(name=theme_name, identity=norm_id)
        raw = _to_dict(page)
        logger.debug("[EDHREC PROXIED] theme=%s id=%s keys=%s", theme_name, norm_id, list(raw.keys())[:10])
        shaped = _normalize_page_theme_payload(raw)
        if shaped["header"] != "Unknown" or shaped["container"]["collections"]:
            return shaped
        logger.warning("Proxied client returned empty-ish payload; falling through.")
    except Exception as e2:
        logger.info("Proxied theme fetch failed: %s", e2)

    # 3) Direct HTML parse fallback (now against /tags/<theme>/<segment>)
    try:
        shaped = await _parse_edhrec_theme_html(theme_name, norm_id)
        logger.debug("[EDHREC HTML] theme=%s id=%s header=%s collections=%d",
                     theme_name, norm_id, shaped.get("header"), len(shaped.get("container", {}).get("collections", [])))
        return shaped
    except Exception as e3:
        logger.exception("HTML fallback failed: %s", e3)
        return {"header": "Unknown", "description": "", "container": {"collections": []}}

# ---- EDHREC theme RAW (debug) -----------------------------------------------
@app.get("/edhrec/theme_raw")
async def edhrec_theme_raw(name: str, identity: str):
    """
    Debug endpoint to inspect the upstream Mightstone payload (static -> proxied).
    Not intended for production consumption by GPT actions.
    """
    theme_name = _normalize_theme_name(name)
    norm_id = _normalize_identity(identity)
    try:
        page = await edh.theme_async(name=theme_name, identity=norm_id)
        raw = _to_dict(page)
        return {"source": "static", "raw": raw}
    except Exception:
        edh_proxy = EdhRecProxiedStatic(transport=cache_transport)
        page = await edh_proxy.theme_async(name=theme_name, identity=norm_id)
        return {"source": "proxied", "raw": _to_dict(page)}

# ---- EDHREC theme HTML preview (fetch debug via /tags) ----------------------
@app.get("/edhrec/theme_debug")
async def edhrec_theme_debug(name: str, identity: str, preview: int = 1000):
    """
    Returns the first N chars of the fetched EDHREC HTML (after browser-like headers),
    against https://edhrec.com/tags/<theme>/<segment>.
    """
    theme = _normalize_theme_name(name)
    segment = _identity_to_edhrec_segment(identity)
    url = f"https://edhrec.com/tags/{theme}/{segment}"
    client: httpx.AsyncClient = app.state.httpx_client
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = f"https://edhrec.com/tags/{segment}"
    r = await client.get(url, headers=headers, follow_redirects=True)
    text = r.text or ""
    return {
        "status": r.status_code,
        "url": str(r.url),
        "preview": text[: max(0, min(preview, 4000))],
    }

# -----------------------------------------------------------------------------
# Lifecycle: shared httpx client
# -----------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    app.state.httpx_client = httpx.AsyncClient(
        transport=cache_transport,
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(10.0, connect=10.0),
        http2=False,  # keep HTTP/2 off (no 'h2' dep required)
    )

@app.on_event("shutdown")
async def on_shutdown():
    client = getattr(app.state, "httpx_client", None)
    if client is not None:
        await client.aclose()
