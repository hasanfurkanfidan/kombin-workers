# kombin-workers

Kombin is a mobile app that compares clothing prices across Turkish retailers and redirects users to buy. The .NET backend
(`../kombin-backend`) owns the catalog. **This repo is the data-ingestion side**: workers fetch product listings from
retailer and brand web sites, normalize them, and push them to the backend's internal endpoints (contract:
`docs/openapi.json`, copied from the backend):

- `PUT  /api/v1/internal/catalog/sellers/{slug}` – register/update the seller (replaces the whole seller)
- `POST /api/v1/internal/catalog/offers/upsert` – up to 500 items per call, per-item results and errors
- `GET  /api/v1/internal/catalog/sellers/{slug}/offers` – what the backend currently knows for that seller
- `POST /api/v1/internal/catalog/offers/sweep` – deactivate listings not seen for 48 h

All internal calls carry `X-Internal-Key`. First deliverable: the framework, three brand-store sources (Derimod, Koton,
Jack & Jones) and a runnable schedule. Task W3 made every source send the whole gallery, packshots first (see "Images" below), and moved Twist from first pages to its product sitemap. Task W2 added Network, Hotiç, Twist and Colin's (a run takes at most 400 products per
source, `KOMBIN_MAX_ITEMS_PER_SOURCE`). Details of each source, and the allowed / disallowed decision for every candidate
site: `docs/sources.md`.

## Hard rules (legal and ethical) – verbatim from the task

- Fetch only what is needed, at a polite rate: default 1 request/second per host, exponential backoff on 429/5xx, hard stop for the source after 20 consecutive failures.
- Honor `robots.txt` (use `urllib.robotparser`); if a source disallows the paths we need, do not scrape it — mark the source `blocked` in its module and report it. Prefer official/structured data when a site exposes it (JSON endpoints its own storefront uses, `application/ld+json` Product markup, sitemaps, feeds); parse HTML only as a last resort.
- Identify ourselves: `User-Agent: KombinBot/0.1 (+https://kombin.app/bot; contact: <BOT_CONTACT>)`. While `BOT_CONTACT` is unset or still the `[YOUR EMAIL]` placeholder the `contact:` part is omitted (never send the placeholder); the owner sets a real, non-personal address before production.
- No login walls, no CAPTCHAs, no proxy rotation, no headless browsers in this task. If a site cannot be read with plain HTTP, record that and move on.
- Never store personal data. Never fetch more than one image per product (we store URLs, not files).

Terms count as well as robots.txt (task W2): read the site's terms-of-use / member agreement before writing a source. A clause that
forbids crawlers, scraping or data mining without written permission (LC Waikiki, Flo) makes the source disallowed even
though its robots.txt allows the pages; record it in `docs/sources.md` and write no module.

In practice: a site that answers 429/403 is slowed down or left alone, never worked around (no other user agents, no
fingerprint tricks, no retries on a loop). `PoliteClient` enforces the rate, robots, backoff and the 20-failure stop; do
not bypass it with a bare `httpx` call in a source.

## Stack

Python 3.12, `uv` (lock file committed), `httpx` (HTTP/2, timeouts), `pydantic` v2 (models + settings), `selectolax`
(lexbor backend) for HTML, `structlog`, `typer`, `pytest` + `pytest-asyncio` + `respx`, `ruff`, `mypy --strict`. No
Scrapy, no Playwright, no Celery. Scheduling is the tiny loop in `scheduler.py` (`kombin-workers daemon`).

## Layout

```
docs/openapi.json, docs/sources.md
src/kombin_workers/
  settings.py        pydantic-settings (.env: KOMBIN_API_URL, KOMBIN_INTERNAL_KEY, BOT_CONTACT, ...)
  cli.py             typer: run <source>, run-all, sweep, check <source>, sources, daemon
  http.py            PoliteClient: per-host rate limit, retries, robots (RobotsRules), failure stop
  models.py          NormalizedOffer / NormalizedProduct / SellerRegistration (+ enums from the OpenAPI schema)
  backend.py         typed client for the four internal endpoints; batches of 200; logs per-item errors
  pipeline.py        discover -> fetch (4 at a time, still <= 1 rps) -> normalize -> batch upsert -> summary
  scheduler.py       daemon loop (run-all every 6 h, sweep daily, jitter)
  normalize/         colors, categories, sizes, attributes, audience, images, text  (every mapping table lives here)
  sources/           base.py (Source contract), registry.py, embedded.py (__NEXT_DATA__ / ld+json readers),
                     sitemap.py (sitemap reader), derimod.py, koton.py, jackjones.py, network.py, hotic.py, twist.py, colins.py
tests/               fixtures/<source>/ (saved real responses + robots.txt), one test module per area
```

## How to add a source

1. **Investigate.** Fetch `robots.txt`, one category page and one product page (politely, by hand, a handful of
   requests). Look for storefront JSON (`?format=json`, Shopify `products.json`), `ld+json`, an embedded state, a sitemap.
   Check that the paths you need are allowed (mind blank lines and `*`/`$` in robots.txt – `RobotsRules` handles them).
2. **Write the `docs/sources.md` entry**: platform, how listings are discovered, where price / stock / sizes / model code
   come from, rate used, anything disallowed. A candidate that is blocked gets a row under "Looked at and not used" (or a
   module with `blocked = True` and the reason in `notes`).
3. **Module** `sources/<slug>.py`: subclass `BaseSource` (`slug`, `brand_name`, `seller_name`, `website_url`, `notes`),
   implement `discover()` (use `paged_discovery`) and `fetch(ref)`; raise `SkipItem("reason")` for products that are not for
   us. Register it in `registry.py`. The seller slug is `<slug>-store`. Keep the parsing in a pure function
   (`normalize_product(data, url)`) so tests can feed it saved data.
4. **Fixtures**: save small, real responses under `tests/fixtures/<slug>/` (trim them; no personal data) plus the site's
   `robots.txt`.
5. **Tests** in `tests/test_sources.py`: normalization against the fixtures, sold-out / crossed-out / skip reasons,
   discovery with `respx`, and the robots rules in `tests/test_http.py`.
6. **`uv run kombin-workers check <slug> --limit 20`** and read the JSON: category, audience, colour, model code, sizes,
   images, price. Then **`uv run kombin-workers run <slug> --limit 20`** against the local backend, then
   `GET /api/v1/catalog/products?brandSlug=<brand>` to see the result.

## The normalizer rule

**Every mapping table lives in `normalize/` with tests, never inline in a source.** A source extracts raw labels (a
colour word, a category breadcrumb, a size string, a gender tag) and calls `map_color`, `map_category(..., source=slug)`,
`normalize_sizes`, `map_audience`, `infer_attributes`. A source-specific exception goes in
`categories.SOURCE_OVERRIDES[slug]` (exact folded label -> slug, or `None` to skip), with a test. An unknown category skips
the item (`SkipItem("unknown-category")`, counted in the run summary) – never guess a slug.

`modelCode` is `<style code>-<colour code>` for every source, so one product = one colourway (the backend merges offers by
brand + model code and a product has a single primary colour).

## Images (task W3)

Try-on needs photos that show the garment without a person. Every source builds its gallery with a pure
`gallery_urls(record)` function that collects every gallery image (site order) and passes it to
`normalize.images.order_gallery`: likely person-free first (a hint in the file path, never `detail`, or a per-host rule in
`images.HOST_RULES`), then unknown, then likely worn, each group in site order, capped at 8. **Never download an image to
classify it** – the vision service does that. The backend accepts at most 6 `imageUrls` per item (more fails the whole
item) and only stores images when an offer is created, so `NormalizedOffer` keeps the first 6 and a changed order does
not reach products that already exist. A new hint rule goes into `HOST_RULES` with a test in `tests/test_images.py` and
a line in `docs/sources.md`; do not state a rule as verified unless it was checked on real pages.

## Commands

```bash
uv sync                                     # install (Python 3.12 is pinned in .python-version)
uv run kombin-workers sources               # list sources
uv run kombin-workers check koton --limit 20          # dry run: prints normalized items as JSON, no backend calls
uv run kombin-workers run koton --limit 50 --max-pages 1
uv run kombin-workers run-all
uv run kombin-workers sweep [--seller koton-store]
uv run kombin-workers daemon                # run-all every 6 h, sweep daily (what the Docker image runs)
uv run pytest
uv run ruff check .
uv run mypy
```

Local backend: `http://localhost:8081`; the internal key is `INTERNAL_API_KEY` in `../kombin-backend/docker/.env`, copied
into this repo's git-ignored `.env` as `KOMBIN_INTERNAL_KEY`. `BOT_CONTACT` stays unset (the UA then has no
`contact:` part) until the owner fills in a real, non-personal address.

## Rules for working here

- **Do not commit unless asked.** (No pushing either; the remote will be `https://github.com/hasanfurkanfidan/kombin-workers`.)
- Never print or log `KOMBIN_INTERNAL_KEY`. `docker compose config` prints it – don't paste its output.
- Keep `uv run ruff check . && uv run mypy && uv run pytest` green.

## Out of scope

Marketplaces (Trendyol, Hepsiburada – until partner access exists), headless browsers, proxies / IP rotation, CAPTCHA or
bot-protection bypasses, downloading images (we store URLs only), ML-based product matching.
