# kombin-workers

Ingestion workers for Kombin. They read product listings from Turkish brand stores, normalize them (colour, category,
sizes, audience, fit, ...) and push them to the Kombin backend's internal catalog endpoints.

| Source | Slug | Platform | Data used |
|---|---|---|---|
| Derimod | `derimod` | Shopify | `collections/<handle>/products.json` |
| Koton | `koton` | Akinon | `?format=json` on category and product pages |
| Jack & Jones | `jack-jones` | server-rendered Vue | `window.__INITIAL_STATE__` in listing and product pages |

How each site was investigated, what it exposes and what was left out: [docs/sources.md](docs/sources.md).
Working conventions (legal rules, how to add a source): [CLAUDE.md](CLAUDE.md).

## Setup

```bash
uv sync
cp .env.example .env     # then fill in KOMBIN_INTERNAL_KEY (= INTERNAL_API_KEY of the backend's docker/.env)
```

`BOT_CONTACT` goes into the `User-Agent` (`KombinBot/0.1 (+https://kombin.app/bot; contact: ...)`). It keeps the
`[YOUR EMAIL]` placeholder until the owner sets it – do that before running against real sites at any volume.

## Use

```bash
uv run kombin-workers sources
uv run kombin-workers check koton --limit 20       # dry run, prints normalized items as JSON
uv run kombin-workers run koton --max-pages 1      # register seller, fetch, upsert, print a summary
uv run kombin-workers run-all
uv run kombin-workers sweep                        # deactivate listings unseen for 48 h
uv run kombin-workers daemon                       # run-all every 6 h + sweep daily, JSON logs
```

Defaults: 1 request/second per host, 4 fetches in flight, 5 pages per category (`--max-pages`), at most 1500 products
per source, upserts in batches of 200. Settings can be overridden with `KOMBIN_*` environment variables (see
`settings.py`).

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
