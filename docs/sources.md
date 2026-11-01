# Sources

What was found while investigating each candidate (2026-11-01), what we use, and what we left alone. Every source goes
through `PoliteClient`: 1 request/second per host, exponential backoff on 429/5xx, `Retry-After` honoured, robots.txt
checked before every URL, hard stop after 20 consecutive failures. Nothing here needs a login, a CAPTCHA solver, a
proxy or a headless browser.

Seller slugs follow the backend convention `<brand-slug>-store` (the upsert endpoint creates that seller on demand for
brand stores; we register it explicitly first with `PUT /sellers/{slug}`). **The PUT replaces the whole seller, so
affiliate settings are owned by the source module** (`BaseSource.seller`) – an affiliate template entered only in the
backend would be cleared by the next run.

## In use

### Derimod – `derimod` → seller `derimod-store`

| | |
|---|---|
| Site | https://derimod.com.tr (leather shoes, bags, belts, leather outerwear; also resells Geox, Camper, ... – skipped by `vendor`) |
| Platform | Shopify |
| robots.txt | allows `/collections/*/products.json`; disallows `/search`, `/cart`, `*sort_by*`, `*q=*`, `*filter*` etc. We use none of those |
| Discovery | `/collections/<handle>/products.json?limit=100&page=N`. 13 collections (sneaker, bot, loafer, deri ceket, deri mont, canta, kemer; men's and women's). Page-major, so categories stay balanced when capped |
| Fetch | none: a collection page is the full product record, so `fetch` makes no request |
| Price / crossed-out | variant `price`; `compare_at_price` when greater (Shopify uses `"0.00"` for "none") |
| Stock / sizes | variant `available`; sizes from the `Beden` option, available variants only |
| externalId | Shopify product id (a Derimod product is one colourway) |
| modelCode | `<style>-<colour code>`, e.g. `WM790004DN-5637145360`. Style = the tag shaped like `WM790004DN`; colour code = the number at the end of the handle (same style in another colour has another colour code). Documented format for all sources: *style code, a dash, colour code* – the style part alone is what a marketplace calls the model number |
| Audience | `feed-gender-*`, `Erkek` / `Kadın` tags |
| Colour | the `Renk` option, then title, then tags |
| Rate used | 1 request/second (one collection page ≈ 0.7 MB). During the investigation Derimod answered **429 with `Retry-After: 60`** after a burst of manual probing; the client waited and succeeded on the retry. Keep runs sequential |
| Images | `images[].src`, first 6 |

### Koton – `koton` → seller `koton-store`

| | |
|---|---|
| Site | https://www.koton.com |
| Platform | Akinon ("Omnishop"). `?format=json` on a listing or product URL returns the JSON the front end renders from |
| robots.txt | disallows `/list/*`, `/shopping-page/*`, `/baskets/*`, `/orders/*`, `/account/*`, `/users/*`, `/login/*`, `/*?category_ids=`, `/*?search_text=`. Category and product pages are allowed |
| Discovery | `/<gender>-<category>/?format=json&page=N`, 24 products a page, each one a colourway. Categories found by probing: tisort, gomlek, pantolon, mont, kaban, blazer-ceket, ceket (women), etek (women), canta (women), kemer. **No men's jean / knitwear listing was found under the obvious paths** – those categories stay empty for Koton until someone finds the right paths |
| Fetch | `<product url>?format=json`: variants per size with stock, all images, attribute set |
| Price / crossed-out | `product.price`; `product.retail_price` when greater. No discounted product was on sale while building this, so the crossed-out path is covered by a unit test on edited data only |
| Stock / sizes | the `integration_size_id` variant group: in-stock options become `availableSizes`; ≤ 3 units in total → `LowStock` |
| externalId / modelCode | `<base_code>-<listing_code>`, e.g. `5SAK60396UW-031` (Koton's style code + colour code) |
| Audience | `filterable_gender` / `morhipo_cinsiyet` |
| Fit / material / season | `filterable_fit`, `filterable_kumas_tipi` + composition lines of the description, `integration_season_desc` |
| Gotchas | robots.txt has blank lines between `User-agent` and its rules (see below). `/robots.txt` answers **500 to `Accept: text/plain`** – the robots request therefore sends the default `Accept: */*` |
| Rate used | 1 request/second: 1 listing page + 1 product request per product |

### Jack & Jones – `jack-jones` → seller `jack-jones-store`

| | |
|---|---|
| Site | https://www.jackjones.com.tr (Bestseller group storefront; menswear plus some womenswear) |
| Platform | server-rendered Vite/Vue app. Every page embeds its data as `window.__INITIAL_STATE__ = {...}`; product pages also have `application/ld+json` `Product` |
| robots.txt | `Allow: /`, disallows `/tr-tr/login*`, `/account*`, `/checkout*`, `/favoriler`, `/return-items*`, `/track-order*`, `/arama*` (search). Sitemaps exist but are not needed |
| Discovery | `/tr-tr/<category>?page=N`, 60 products a page; the listing state (`$getProducts-*.results`) gives id, url and price. 13 category pages (tisort, gomlek, pantolon, jean, kazak, triko, mont-ceket, ayakkabi, aksesuar; women's gomlek, pantolon, jean, mont-ve-ceket) |
| Fetch | the product page; its state has every size variant with `inStockEcom`, all media, fabric composition, tags |
| Price / crossed-out | `price.salesPrice.amount`. A crossed-out price would sit beside it (`listPrice`, `originalPrice`, ... are read if present); **no discounted product was visible while building this**, so the sale format is unverified |
| externalId / modelCode | product id `<style>_<colour>` / `<styleNumber>-<colour id>`, e.g. `12298420-2161` |
| Audience | `englishGender`, `targetGroup`, tags (`gender-male`) |
| Category | `ediCategory` / `ediSubCategory` ("Tişört") and the title |
| Rate used | 1 request/second; pages are ≈ 0.5–1 MB each, so a 1500-product run takes ≈ 30 minutes |

## Looked at and not used

| Candidate | Finding |
|---|---|
| **Defacto** | `robots.txt` itself answers `403 Access Denied` (Akamai) to a plain request. Treated as **blocked**: we cannot read the rules and the edge refuses us. No module written |
| **Boyner** | robots.txt has `Disallow: /product/` – the product detail pages. **Blocked by robots**, no module written |
| **Mavi** | robots.txt allows category pages (filters `?q=`, `?f=` are disallowed), but the site is an Angular app whose data is a compressed transfer-state array, product pages carry no `Product` JSON-LD and `/sitemap.xml` is 404. Not readable with plain HTTP in reasonable effort – revisit if an official feed appears |
| **Vakko** | robots.txt allows product pages (`Allow: /`) and has a sitemap (21k products); product pages embed the full SAP Spartacus state (`cx-state`) with price, stock, sizes, brand. But the catalogue is mixed (home goods, other designer brands), pagination parameters are disallowed and discovery would have to be sitemap-only with a per-item brand filter. A good candidate for a later task |
| **LC Waikiki** | robots.txt allows `/` but disallows its own brand pages (`/marka/lc-waikiki*`, `*marka=lc-waikiki*`); not investigated further |
| **Pull&Bear, Beymen Club** | not investigated (Inditex-style bot protection expected for Pull&Bear; Beymen Club is not a standalone store). Nike, Adidas and Zara are excluded by the task |

## Things learned that apply to every source

- **robots.txt blank lines.** `urllib.robotparser` ends a group at every blank line, so
  `User-agent: *` / empty line / `Disallow: ...` (Koton and Jack & Jones both write it that way) parses as "no rules" –
  everything allowed. `RobotsRules.from_text` drops blank lines first. It also re-does the matching because the stdlib
  matcher has no `*` / `$` wildcards (`Disallow: /*?category_ids=` would never match). Both are tested against the real
  files saved in `tests/fixtures/*/robots.txt`.
- **An unreadable robots.txt blocks the host** (5xx after retries, 401, 403); a missing one (404) allows it.
- **Colourway = product.** The backend merges offers by `(brand, modelCode)`, and a product has one `primaryColor`, so
  every source puts the colour code into `modelCode`.
- **Categories we cannot file** (no slug in the backend): sweatshirts/hoodies, dresses, blouses, shorts, sandals,
  slippers, socks, wallets, ties, vests, heels. They are skipped and counted as `unknown-category`. Adding
  `ust-giyim/sweatshirt` to the backend would recover a large share of Koton and Jack & Jones.
- **Per-run cap.** `--max-pages` (default 5) pages per category, interleaved page-major; a source also stops at 1500
  discovered products (`pipeline.DEFAULT_MAX_ITEMS`).
