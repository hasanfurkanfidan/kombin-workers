# kombin-workers

Ingestion workers for Kombin. They read product listings from Turkish brand stores, normalize them (colour, category,
sizes, audience, fit, ...) and push them to the Kombin backend's internal catalog endpoints.

| Source | Slug | Audience | Platform | Data used |
|---|---|---|---|---|
| Derimod | `derimod` | women, men | Shopify | `collections/<handle>/products.json` |
| Koton | `koton` | women, men | Akinon | `?format=json` on category and product pages |
| Jack & Jones | `jack-jones` | men (+ some women) | server-rendered Vue | `window.__INITIAL_STATE__` in listing and product pages |
| Network | `network` | women, men | server-rendered | full product JSON embedded in each listing page (`window.defaultProductList`) |
| Hotiç | `hotic` | women, men | Next.js | `__NEXT_DATA__` of listing pages (full product records) |
| Twist | `twist` | women | Next.js | product sitemap (robots.txt forbids `?page=`) + `__NEXT_DATA__` of product pages; category first pages as fallback |
| Colin's | `colins` | women, men | server-rendered ASP.NET | data attributes of listing cards + `ld+json` `Product` on product pages |

How each site was investigated, what it exposes and what was left out: [docs/sources.md](docs/sources.md). That file also
holds the **decision for every candidate** (robots.txt lines, terms-of-use findings, date). Of the task W2 candidates,
Network, Hotiç, Twist and Colin's are in use; LC Waikiki and Flo forbid automated access in their terms, DeFacto and
İpekyol do not let us read robots.txt (403 / 418). Mavi's robots.txt allows category and product pages, but the site
answered **403 to KombinBot** when task W3 tried to read a page, so it is not implemented (see `docs/sources.md`).
Working conventions (legal rules, how to add a source): [CLAUDE.md](CLAUDE.md).

## Setup

```bash
uv sync
cp .env.example .env     # then fill in KOMBIN_INTERNAL_KEY (= INTERNAL_API_KEY of the backend's docker/.env)
```

`BOT_CONTACT` goes into the `User-Agent` (`KombinBot/0.1 (+https://kombin.app/bot; contact: ...)`). While it is unset,
empty or still the `[YOUR EMAIL]` placeholder the `contact:` part is **left out** (the UA is then
`KombinBot/0.1 (+https://kombin.app/bot)` and a `bot_contact_not_set` warning is logged) – the placeholder is never sent.
Before running at volume, set it to a real, **non-personal** address the site owners can write to (a role mailbox such
as `bot@<your domain>`, not someone's private email).

## Use

```bash
uv run kombin-workers sources
uv run kombin-workers check koton --limit 20       # dry run, prints normalized items as JSON
uv run kombin-workers run koton --max-pages 1      # register seller, fetch, upsert, print a summary
uv run kombin-workers run-all
uv run kombin-workers sweep                        # deactivate listings unseen for 48 h
uv run kombin-workers daemon                       # run-all every 6 h + sweep daily, JSON logs
```

Defaults: 1 request/second per host, 4 fetches in flight, 5 pages per category (`--max-pages`), at most **400 products
per source per run** (`KOMBIN_MAX_ITEMS_PER_SOURCE`), upserts in batches of 200. A capped run takes an even share of every
category and of both audiences (women's and men's listings alternate and are dealt out one product at a time). The run
summary prints what the backend accepted per category and per audience. Settings can be overridden with `KOMBIN_*`
environment variables (see `settings.py`).

## Images

Every source sends a product's whole gallery (up to 8 collected), **likely person-free images first** (packshot / flat /
ghost / hanger shots), because try-on needs photos without a person. The order comes from hints in the file path or the
site's data only (`normalize/images.py`; rule per source in `docs/sources.md`); a site without a hint keeps its own
order. Images are never downloaded here – the vision service classifies them later. The backend accepts **at most 6**
`imageUrls` per item, so the first 6 of that order go out, and it adds images only when an offer is *created* (not on a
plain re-observation). The run summary prints the average images per product and the share of products whose first
image is likely person-free.

## Docker

```bash
docker compose up worker                       # the daemon: run-all every 6 h, sweep once a day
docker compose run --rm worker run koton --limit 50
```

The image is multi-stage, runs as a non-root user and logs JSON. From the container the backend is reached at
`host.docker.internal:8081`; `docker-compose.yaml` explains how to join the backend's compose network instead.

## Develop

```bash
uv run pytest && uv run ruff check . && uv run mypy
```

Tests use saved real responses (`tests/fixtures/`) and `respx`; nothing in the test suite touches the network.
