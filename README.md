Mightstone GPT Webservice

FastAPI microservice that powers a Commander/EDH deckbuilding GPT with Scryfall lookups and EDHREC theme/tag ingestion.
It exposes clean, rate-limited endpoints your custom GPT (or any client) can call to fetch theme data (e.g., Jeskai Prowess) and hydrate it with Scryfall IDs (and optionally images).

✨ Features

EDHREC Tag/Theme ingestion
Pulls data from EDHREC Tag pages (e.g. /tags/prowess/jeskai) via their Next.js data layer and returns a normalized JSON object:

{
  "header": "Jeskai Prowess | EDHREC",
  "description": "Popular Jeskai Prowess EDH commanders",
  "container": { "collections": [ { "header": "Cardviews", "items": [ { "name":"Monastery Mentor", "id":"<scryfall-uuid>" } ] } ] }
}


Hydration with Scryfall IDs
Bulk fills in missing Scryfall UUIDs (and, optionally, image URLs) by exact-name matching.

Polite, configurable rate-limiting to respect Scryfall (low concurrency + small per-request delay by default).

Simple Scryfall search proxy for debugging and client hydration.

Works great with a GPT Action: predictable schemas, no brittle HTML scraping on the client.

Endpoints
Method	Path	Purpose
GET	/health	Service health check.
GET	/edhrec/theme	Fetch EDHREC tag page for a theme & color identity (e.g. prowess + wur → Jeskai).
GET	/edhrec/theme_nextdebug	Return the raw EDHREC tag payload for debugging a theme query.
GET	/edhrec/theme_hydrated	Same as /edhrec/theme, then hydrates items with Scryfall IDs (and optionally images).
GET	/cards/search	Thin pass-through to Scryfall’s /cards/search for debugging/hydration.
GET	/edhrec/average-deck	Fetch EDHREC “Average Deck” list for a commander (all or bracketed lists).
(optional)	/docs	FastAPI Swagger UI (auto-generated).
(optional)	/openapi.json	FastAPI OpenAPI spec (auto-generated).

Note: The service targets EDHREC Tag pages like /tags/prowess/jeskai. That’s the most stable structure for extracting theme data. If you need /themes/... pages, open an issue and we can extend the extractor.

Quick Start
1) Requirements

Python 3.11+

pip install -r requirements.txt (FastAPI, httpx, etc.)

2) Configure (optional)

Environment variables (sensible defaults baked in):

PORT — default 8080

MIGHTSTONE_UA — default Mightstone-GPT/1.0 (+https://mtg-mightstone-gpt.onrender.com)

SCRYFALL_MAX_CONCURRENCY — default 4

SCRYFALL_DELAY_SECONDS — default 0.12 (slows to ~8 req/s theoretical)

3) Run
uvicorn app:app --host 0.0.0.0 --port 8080


Open local docs:

Swagger: http://localhost:8080/docs

OpenAPI JSON: http://localhost:8080/openapi.json

Usage Examples
Health
curl -s http://localhost:8080/health

EDHREC Theme (Tag) – Jeskai Prowess
# bash / zsh
curl -sG "http://localhost:8080/edhrec/theme" \
  --data-urlencode "name=prowess" \
  --data-urlencode "identity=wur" | jq .


Windows PowerShell

curl -Method GET "http://localhost:8080/edhrec/theme?name=prowess&identity=wur"


Windows cmd.exe

curl -sG "http://localhost:8080/edhrec/theme" ^
  --data-urlencode "name=prowess" ^
  --data-urlencode "identity=wur"

Debugging Endpoint – Raw EDHREC Payload
curl -sG "http://localhost:8080/edhrec/theme_nextdebug" \
  --data-urlencode "name=prowess" \
  --data-urlencode "identity=wur" | jq .

# Example 404 (unknown theme)
curl -sG "http://localhost:8080/edhrec/theme_nextdebug" \
  --data-urlencode "name=totally-made-up-theme" \
  --data-urlencode "identity=wur" -w "\nHTTP %{http_code}\n"

Hydrated Theme (adds Scryfall IDs, optional images)
# No images (default) – best for GPT/tooling
curl -sG "http://localhost:8080/edhrec/theme_hydrated" \
  --data-urlencode "name=prowess" \
  --data-urlencode "identity=wur" | jq .

# With images (opt-in)
curl -sG "http://localhost:8080/edhrec/theme_hydrated" \
  --data-urlencode "name=prowess" \
  --data-urlencode "identity=wur" \
  --data-urlencode "include_images=true" \
  --data-urlencode "image_size=normal" | jq .

Scryfall Search (debug)
curl -sG "http://localhost:8080/cards/search" \
  --data-urlencode 'q=! "Monastery Mentor"' \
  --data-urlencode 'limit=5' | jq .

### Debugging Endpoint

`GET /edhrec/theme_nextdebug?name=<theme>&identity=<colors>`

Returns the unshaped Next.js payload straight from EDHREC for troubleshooting theme extractions.

- 404 → Not Found when EDHREC has no data for the requested combination.
- 502 → Upstream error for timeouts, 5xxs, or malformed responses.

### EDHREC Average Decks

**HTTP Endpoint**

```
GET /edhrec/average-deck?name=<Commander Name>&bracket=<precon|upgraded>
```

- `name`: commander’s printed name (e.g., `Jodah, the Unifier`).
- `bracket` (optional): defaults to `upgraded`. Accepts `precon` or `upgraded`.

**Response Shape**

- `commander`: the requested commander name.
- `bracket`: normalized bracket (`precon` or `upgraded`).
- `source_url`: EDHREC page used to build the list.
- `cards`: list of `{name, qty}` entries representing ~99 mainboard cards.
- `commander_card` (optional): commander card metadata when EDHREC lists it separately.
- `error`: `null` on success, otherwise `{message, url, details?}` describing the failure.

**JIT Tool (for GPT)**

- Tool: `edhrec_average_deck`
- Params: `{ name, bracket? }`

Examples:

```
GET /edhrec/average_deck?name=Jodah,%20the%20Unifier&bracket=upgraded
GET /edhrec/average_deck?name=Donatello,%20the%20Brains%20//%20Michelangelo,%20the%20Heart&bracket=upgraded

tool: edhrec_average_deck name=Donatello, the Brains // Michelangelo, the Heart bracket=upgraded
```

## EDHREC Themes via Mightstone

Primary endpoint:

```
GET https://mtg-mightstone-gpt.onrender.com/edhrec/theme?name={theme}&identity={code}
```

- `name`: EDHREC tag (lowercase), e.g. `zombies`, `prowess`, `aristocrats`
- `identity`: canonical color code sorted W→U→B→R→G (e.g., `wur` for Jeskai)

Examples:

```bash
curl -sG "https://mtg-mightstone-gpt.onrender.com/edhrec/theme" \
  --data-urlencode "name=prowess" \
  --data-urlencode "identity=wur"

curl -sG "https://mtg-mightstone-gpt.onrender.com/edhrec/theme" \
  --data-urlencode "name=aristocrats" \
  --data-urlencode "identity=bg"
```

Direct EDHREC URL (debugging only):

Use slugs, not codes:

- ✅ `/tags/zombies/mono-black`, `/tags/prowess/jeskai`
- ❌ `/tags/zombies/b`, `/tags/prowess/wur`

Request Parameters
/edhrec/theme

name (required) — EDHREC tag (e.g., prowess, spellslinger, etc.)

identity (required) — Color identity:

- Accepts color codes ("wur"), labels ("Jeskai"), or slugs ("jeskai").
- Codes are canonicalized to W→U→B→R→G order.
- Slugs map to EDHREC color words (e.g., mono-black, izzet, witch-maw).

/edhrec/theme_hydrated

Includes all /edhrec/theme params plus:

include_images (bool, default false) — When true, returns Scryfall image URLs.

image_size (default normal) — One of small | normal | large | png | art_crop | border_crop.

max_to_hydrate (int, default 350) — Upper bound on items to hydrate for safety.

Tip: Your custom GPT doesn’t need images to reason; name + id is sufficient. Images are for UI polish.

How It Works (High Level)

EDHREC ingestion
The service fetches the Tag page HTML (e.g., /tags/prowess/jeskai) and its Next.js data JSON (/_next/data/<buildId>/tags/...json).
It extracts:

header (HTML <title>)

description (<meta name="description">)

card/group lists by scanning the JSON for arrays of objects with name fields (e.g., Cardviews, Cards), then normalizes into:

{ "container": { "collections": [ { "header": "...", "items": [ { "name": "..." } ] } ] } }


Hydration
For items missing id (and optionally image), the service calls Scryfall cards/named?exact=<name> with polite concurrency & delay.
Successful matches fill id (Scryfall UUID) and, if requested, image.

Deploy (Render.com)

Render detects the open port from logs; we recommend specifying it explicitly:

Start Command: uvicorn app:app --host 0.0.0.0 --port 8080

Environment: set vars noted above as needed

Health Check Path: /health

Common Render Tips

If you see ImportError: Using http2=True, but the 'h2' package is not installed, ensure http2=False (default in this app) or install httpx[http2].

If Render “detects a new port,” it’s usually because your server bound a different port mid-deploy—keep it fixed to 8080.

Troubleshooting

Empty collections or placeholder data

Check the endpoint you’re targeting: this service currently supports Tag pages (/tags/<theme>/<colorWord>), not arbitrary EDHREC routes.

## Privacy Policy

Mightstone serves a public privacy policy at:

- `GET /privacy` → HTML page (no auth, HTTPS on Render)

This URL can be used in OpenAI’s “Privacy policy URL” field to publish the GPT publicly.
You can set the contact email with `PRIVACY_CONTACT_EMAIL` (defaults to `pommnetwork@gmail.com`).

Run the page in your browser to confirm it exists and shows card lists.

Some Next.js keys evolve; we use a robust “find arrays of named items” strategy, but open an issue if a specific tag returns nothing.

403 when hitting json.edhrec.com

Expected sometimes. The service falls back to the public Next.js data route under edhrec.com/_next/data/<buildId>/.... That path is what we prefer.

Scryfall rate limiting

Tune SCRYFALL_MAX_CONCURRENCY and SCRYFALL_DELAY_SECONDS if you hydrate tons of items.

Be considerate—Scryfall is a free community resource.

Windows quoting/escaping

PowerShell: pass query params inline (no --data-urlencode).

cmd.exe: use line continuation ^ and --data-urlencode as shown above.

Security, ToS & Robots

This service sends normal GET requests with a descriptive User-Agent.

It follows redirects and behaves politely (low concurrency, small delays).

You are responsible for complying with EDHREC & Scryfall Terms of Service and any usage limits.

If you build a public app, consider caching your results and avoiding excessive hydration calls.

Action / OpenAPI

FastAPI auto-serves the OpenAPI document at /openapi.json, which you can wire directly into a GPT Action.
Swagger UI is available at /docs.

If you need a pinned/hand-curated Action schema (e.g., with reduced surface or descriptions tuned for a GPT), add a action.yaml and keep it in sync with the live endpoints.

Roadmap

Optional support for EDHREC /themes/... routes (in addition to /tags/...)

Smarter hydration (split cards, MDFCs, punctuation & localization fallback)

Optional caching layer (filesystem/redis) to minimize repeat upstream calls

EDHREC “Combos” and “High Synergy” specialized endpoints

Contributing

PRs welcome! Please include:

Repro steps / tag & identity that fail

Expected vs actual JSON

Logs (without secrets) if relevant

License

MIT © KnackAtNite

Acknowledgements

Scryfall — Incredible API & dataset for Magic.

EDHREC — Community insights that power theme exploration.

FastAPI — Developer-friendly web framework with great docs.
