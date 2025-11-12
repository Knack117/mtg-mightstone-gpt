# app.py
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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

# Scryfall: be respectful; their docs suggest ~10 req/s cap. We’ll be far lower.
SCRYFALL_MAX_CONCURRENCY = int(os.environ.get("SCRYFALL_MAX_CONCURRENCY", "4"))
SCRYFALL_DELAY_SECONDS = float(os.environ.get("SCRYFALL_DELAY_SECONDS", "0.12"))  # ~8/s theoretical cap

logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("mightstone")

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
    container: ThemeContainer

class HealthResponse(BaseModel):
    status: str

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
    app.state.sf_sem = asyncio.Semaphore(SCRYFALL_MAX_CONCURRENCY)
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

async def _fetch_text(url: str) -> str:
    log.info('HTTP GET %s', url)
    r = await app.state.client.get(url, follow_redirects=True)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Upstream fetch failed ({r.status_code} {url})")
    return r.text

async def _fetch_json(url: str) -> Any:
    log.info('HTTP GET %s', url)
    r = await app.state.client.get(url, follow_redirects=True)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Upstream JSON fetch failed ({r.status_code} {url})")
    try:
        return r.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f"Invalid JSON from {url}")

def _snakecase(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _extract_build_id_from_html(html: str) -> Optional[str]:
    m = _build_id_rx.search(html)
    if m:
        return m.group(1)
    return None

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

async def fetch_theme_tag(name: str, identity: str) -> PageTheme:
    """
    Pulls the EDHREC Tag (e.g., /tags/prowess/jeskai) Next.js JSON and builds a PageTheme.
    """
    tag_slug = name.strip().lower()
    # EDHREC uses color words here, not WUR
    color_word = {
        "wur": "jeskai", "rwu": "jeskai",
        "wubrg": "five-color",
    }.get(identity.lower(), identity.lower())

    tag_html_url = f"{EDHREC_BASE}/tags/{tag_slug}/{color_word}"
    html = await _fetch_text(tag_html_url)
    header, description = _extract_title_description_from_head(html)

    build_id = _extract_build_id_from_html(html)
    if not build_id:
        # Fallback: try the tags JSON without an explicit buildId (EDHREC often accepts this form)
        json_url = f"{EDHREC_BASE}/_next/data/{'C2WISSDrnMBiFoK_iJlSk'}/tags/{tag_slug}/{color_word}.json"
    else:
        json_url = f"{EDHREC_BASE}/_next/data/{build_id}/tags/{tag_slug}/{color_word}.json"

    data = await _fetch_json(json_url)

    # Heuristic extraction
    buckets = _walk_for_named_arrays(data)
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

    return PageTheme(
        header=header,
        description=description,
        container=ThemeContainer(collections=collections)
    )

# -----------------------------------------------------------------------------
# Helpers: Scryfall lookups (exact-name)
# -----------------------------------------------------------------------------
async def scryfall_named_exact(name: str, image_size: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (scryfall_id, image_url_or_none) for an exact name match.
    image_size: one of None | 'small' | 'normal' | 'large' | 'png' | 'art_crop' | 'border_crop'
    """
    # Guard: Scryfall exact match wants printable names; leave quotes out, we’ll use url params.
    params = {"exact": name}
    url = f"{SCRYFALL_BASE}/cards/named"
    async with app.state.sf_sem:
        r = await app.state.scryfall.get(url, params=params)
        # polite spacing
        await asyncio.sleep(SCRYFALL_DELAY_SECONDS)
    if r.status_code != 200:
        return None, None
    data = r.json()

    sid = data.get("id")
    img_url = None

    if image_size:
        # Prefer single-faced image_uris
        iu = data.get("image_uris")
        if isinstance(iu, dict) and image_size in iu:
            img_url = iu.get(image_size)
        else:
            # Try card_faces
            faces = data.get("card_faces")
            if isinstance(faces, list) and faces:
                first = faces[0]
                iu2 = first.get("image_uris") if isinstance(first, dict) else None
                if isinstance(iu2, dict) and image_size in iu2:
                    img_url = iu2.get(image_size)

    return sid, img_url

async def hydrate_items(
    items: List[ThemeItem],
    include_images: bool,
    image_size: str,
) -> List[ThemeItem]:
    """
    Hydrates missing Scryfall IDs (and optional images) for a list of ThemeItem.
    """
    async def hydrate_one(it: ThemeItem) -> ThemeItem:
        if it.id and (not include_images or it.image):
            return it
        sid, img = await scryfall_named_exact(it.name, image_size if include_images else None)
        if sid:
            it.id = sid
        if include_images and img:
            it.image = img
        return it

    # Keep order stable
    tasks = [hydrate_one(it) for it in items]
    return await asyncio.gather(*tasks)

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")

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

@app.get("/edhrec/theme_hydrated", response_model=PageTheme)
async def edhrec_theme_hydrated(
    name: str = Query(..., description="EDHREC tag/theme name, e.g. 'prowess'"),
    identity: str = Query(..., description="Color identity (e.g., 'wur' -> Jeskai)"),
    include_images: bool = Query(False, description="If true, also return Scryfall image URLs"),
    image_size: str = Query("normal", description="Scryfall image size if include_images=true (small|normal|large|png|art_crop|border_crop)"),
    max_to_hydrate: int = Query(350, ge=1, le=1000, description="Upper bound on total items to hydrate for safety"),
):
    """
    Same as /edhrec/theme, but fills in missing Scryfall IDs (and optionally images) by exact-name lookup.
    Default keeps bandwidth small by *not* including images (set include_images=true to receive them).
    """
    theme = await edhrec_theme(name=name, identity=identity)  # reuse above

    # Gather all items (respect a max just in case pages explode)
    all_items: List[ThemeItem] = []
    for col in theme.container.collections:
        all_items.extend(col.items)
    slice_items = all_items[:max_to_hydrate]

    hydrated_map: Dict[Tuple[str, Optional[str]], ThemeItem] = {}

    # Hydrate only those missing an id or (if requested) missing an image
    to_hydrate: List[ThemeItem] = []
    for it in slice_items:
        need = (it.id is None) or (include_images and it.image is None)
        if need:
            to_hydrate.append(it)

    if to_hydrate:
        hydrated_items = await hydrate_items(to_hydrate, include_images=include_images, image_size=image_size)
        # Put them back into the original collections (objects are mutated, but we’re explicit)
        idx = 0
        for col in theme.container.collections:
            for i, it in enumerate(col.items):
                if idx < len(slice_items) and it is slice_items[idx]:
                    # If this item was in the hydrated set, it was mutated in place by gather().
                    pass
                idx += 1

    return theme

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
