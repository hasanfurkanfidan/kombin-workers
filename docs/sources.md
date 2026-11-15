# Sources

What was found while investigating each candidate (first three: 2026-11-01; task W2 candidates: 2026-11-14; task W3 changes: 2026-11-15), what we use, and what we left alone. Every source goes
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
| Images | `images[]` by `position` (up to 10 seen). File names are hashes, `alt` is empty, `variant_ids` is `[]`: **no hint**, so the site's order stands (see "Images" below) |

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
| Images | `productimage_set` (active, by `order`; 3-5 per colourway). File names are GUIDs, no alt text: **no hint**, site order. On the one product page looked at in the browser pane, the gallery opens with on-figure shots |
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
| Images | `media[]` (7-9 per product): `imageUrl`, `mimeType`, `isFrontImage` / `isBackImage`, no alt. **Hint: `.png` files** (transparent-background cut-outs) go first; the `.jpg` shots follow in site order. Front and back flags are not used (a front image can be on-figure). Unverified by eye, see "Images" below |
| Rate used | 1 request/second; pages are ≈ 0.5–1 MB each, so a 1500-product run takes ≈ 30 minutes |

### Network – `network` → seller `network-store`

| | |
|---|---|
| Site | https://www.network.com.tr (premium casual, women and men; Ay Marka Mağazacılık) |
| Platform | server-rendered storefront. Each listing page embeds the **complete record of its 24 products** as JSON (`window.defaultProductList["N"] = {...{"ID":223002,...}}`, repeated in a second script block) |
| robots.txt | disallows `/product/` (and `/en/product/`), `/search`, `/api/`, `/cart/`, `*customer*`, `*login*` and the filter / sort query strings (`*filterIDList=*`, `*orderOption=*`, `*minPrice=*`, ...). Product pages are `/<slug>-p-<id>` and category pages `/<slug>-<id>`: neither is under a disallowed path. We only add `?page=N` |
| Discovery | 23 category pages, women's and men's alternating (shirt, t-shirt, knitwear, trousers, jeans, skirt, jacket, coat, boots, loafers, sneakers); `?page=N`, 24 products a page, `<link rel="next">` says whether there is another page |
| Fetch | none: the listing record is the product record |
| Price / crossed-out | `ActualPriceToShowOnScreen`; crossed-out = `StrikeThroughPriceToShowOnScreen` when higher. The record also has `ProductPromotion.PromotedPrice` (a basket-only "Sepette" price) – **ignored**, it is not the shelf price |
| Stock / sizes | `VariantWithStockList[].StockExists`; `HasLowStock` → `LowStock` |
| externalId / modelCode | record `ID` / `ExternalSystemCode`, e.g. `1099332-536` (style – colour) |
| Audience / category | `GenderAttribute`; the `good-type-description` attribute ("Dış Gömlek") first, then the last part of `CategoryLevelDisplayName` ("Mont"). Network files overshirts under Mont; the garment type wins |
| Colour | `color-group` attribute ("Kahverengi"), then the colour name ("Tütün") |
| Images | `MediaList[]` by `DisplayOrder` (3-7 per product; `DisplayOrder` differs from the `_01`.. file number on 1 of 21 products read), `ImageResizePath` – a template with `{width}`/`{height}`; filled with `1000` and `-` (the form the site's own cards use). `AltText`, `Title` and `MediaFormat` are always null: **no hint**, site order |
| Rate used | 1 request/second, one request per 24 products. Pages are big (≈ 4.7 MB for 24 products) but nothing else is fetched |
| Gotcha | a product has three copies of its attribute list in the record; the parser reads `NonVariantAttributeList` only |

### Hotiç – `hotic` → seller `hotic-store`

| | |
|---|---|
| Site | https://www.hotic.com.tr (footwear and bags, women and men) |
| Platform | Next.js. Every listing page's `__NEXT_DATA__` holds `productList.products.data`: the **complete record of 20 products** (variants with stock quantity, prices, pictures, colour, category breadcrumb) |
| robots.txt | `Allow: /`; disallows account / basket paths and the query strings `*category`, `*ordering`, `*searchValue`, `*defaultMinPrice`, `*defaultMaxPrice`, `*script*`. We only add `?pageIndex=N` |
| Discovery | 14 category pages, women's and men's alternating (sneakers, loafers, oxford/classic, boots, boots/chelsea, shoulder / messenger / backpack bags, belt). `?pageIndex=N` is **zero-based** (`?page=2` is ignored by the site and returns page 1 again); `products.hasNextPage` ends the loop. Sandals, slippers, flats, heels and wallets are left out: no category for them |
| Fetch | none: the listing record is the product record |
| Price / crossed-out | `price.newPrice`; `price.oldPrice` when higher. No reduced product was on the pages read, so the crossed-out path is covered by a unit test on edited data |
| Stock / sizes | `variants[]` with `quantity > 0` → sizes; ≤ 3 pairs in total → `LowStock`. Bags are one size (`STD`) |
| externalId / modelCode | `erpCode` / style + colour: `02AYH333890A100` → `02AYH33389-0A100` (the last five characters are the colour code: `0A100` black, `0A480` brown) |
| Audience / category | the category names of the breadcrumb ("Erkek Loafer Ayakkabı") and the product name |
| Colour | the `isSelected` entry of `relatedVariants` ("SİYAH") |
| Images | `pictures[]` by `displayOrder` (4-8 per product). `mediaType` is 1 and `mediaPositionType` 0 on all 33 products read, `name` is null, file names carry only a position number: **no hint**, site order |
| Rate used | 1 request/second, one request per 20 products (≈ 90 KB each) |

### Twist – `twist` → seller `twist-store`

| | |
|---|---|
| Site | https://www.twist.com.tr (women's fashion, İpekyol Group). Womenswear only |
| Platform | Next.js (`__NEXT_DATA__` on category and product pages) |
| robots.txt | disallows `/*?page=*` and `/*&page=`, the size / colour / sort filters (`/*?beden=*`, `/*?renk*`, `/*?sort*`), `*sezon*`, `*cinsiyet*`, `*gender*`, `/u/*`, `/Page/Get*`, `/Widget/Get*` and one product (`/*urun/siyah-nakisli-askili-elbise`). Category pages and `/urun/<slug>` are allowed |
| Discovery | **Product sitemap** (task W3, below): `robots.txt` → `/sitemap.xml` (index) → `/export/Twist/sitemap/products-1.xml`, ~1260 `/urun/<slug>` URLs. `?page=` is disallowed and never built (`tests/test_twist_sitemap.py` asserts it). Fallback if the sitemap cannot be read: the first page of 10 category pages (24 products each, as before). Dresses, blouses, sweatshirts, vests, jewellery ... are left out: no category |
| Fetch | `/urun/<routePath>`: `__NEXT_DATA__.props.pageProps.data.response.product` has the barcodes (size labels) with `stocksByBarcode`, prices, `documents` (images) and attributes; `application/ld+json` `Product` gives the fabric composition |
| Price / crossed-out | `salesPrice`; `basePrice` when higher. No reduced product was on the pages read (unit test on edited data) |
| Stock / sizes | `barcodes[].stockTypeValues[0].name` where `stocksByBarcode[barcode id] > 0`; the site reports 0/1 per size, so ≤ 3 sizes in stock → `LowStock` |
| externalId / modelCode | `sku` (`TW6260025124003`) / `TW6260025124-003`: the sku without its last three digits, a dash, `attr_ColorCode` (`003` natural, `015` navy, `074` brown, ...) |
| Audience / category | `gender` attribute ("Kadın"); `categories[0].name` ("Gömlek"), `attr_UrunAltGrubu`, then the name |
| Images | `documents[]` of type Image (default first, then `order`; 6-7 per product) on `https://cdn.twist.com.tr/mnresize/2400/-` + `filePath`. **Hint: the folder in the path.** `.../FW2627/Stillife/...` (still-life shots) is likely person-free and goes first; `.../Koleksiyon/...` (collection shots) gets no verdict and keeps its order |
| Gotcha | product names are in capitals; `title_case_tr` turns them into "Ekose Desen Yakası ..." |
| Rate used | 1 request/second: 2 sitemap requests (≈ 210 KB), then 1 per product (≈ 30 KB transferred each) |

### Colin's – `colins` → seller `colins-store`

| | |
|---|---|
| Site | https://www.colins.com.tr (jeans and casual wear, women and men) |
| Platform | server-rendered ASP.NET storefront; no JSON endpoint, no embedded state. Each listing card carries `data-ga` (analytics record: colour variant, price) and `data-variants` (JSON: url, images, size ids/names, price, `oldPrice`) as HTML attributes; product pages have `application/ld+json` `Product` |
| robots.txt | disallows `/urunler/`, `/markalar/`, `/hedefler/`, `/sporlar/`, `/kampanyalar/`, `/cart`, `/checkout`, the account paths, and any URL containing `search`, `price`, `term`, `orderby`, `returnUrl`, `subscribe`. `/c/<category>-<id>` and `/p/<slug>-<id>` are allowed; we add `?pagenumber=N` |
| Discovery | 23 category pages, women's and men's alternating; `?pagenumber=N`, 48 cards a page; a `pagenumber=<next>` link says whether there is another page |
| Fetch | the product page (≈ 1 MB), for what a card does not have: `sku`, the in-stock sizes and the breadcrumb |
| Price / crossed-out | ld+json `offers.price` (dot decimal); crossed-out = the card's `oldPrice` ("1.499,90 TL", Turkish format) when higher |
| Stock / sizes | `<option class="have-stock">` of the size select. **A page with no in-stock size option is sold out**: it has no size select at all. The ld+json `availability` says `InStock` on sold-out pages too, and the add-to-cart button is disabled until a size is chosen, so neither is used |
| externalId / modelCode | the card / product id (the URL ends in `-<id>`) / `<style>-<colour>` from the sku `CL1076502_Q1.V1_PGR`: `CL1076502-PGR` (the colour is a three-letter abbreviation, or `DN45196` for denim washes) |
| Audience / category | the first breadcrumb part ("ERKEK GİYİM") and the name ("... Erkek Yeşil Uzun Kol Gömlek"); category from the sub-category crumb ("ERKEK UZUN KOL GÖMLEK"), then the name. Some pages (jeans) have no sub-category crumb; the name decides |
| Colour | `data-ga.variant` of the card ("Yeşil") |
| Images | ld+json `image`: the whole gallery (6-8 full-size URLs, the card carries only 5 downsized ones). File names are numeric ids, no alt text: **no hint**, site order |
| Rate used | 1 request/second: 1 per category page + 1 per product. 400 products ≈ 8 minutes |

## Decisions for the task W2 candidates (2026-11-14)

For every candidate: `robots.txt` was read first, then the terms page (member agreement / terms of use) the footer links to,
then (only if both allow it) one category page and one product page. "Allowed" means robots.txt allows category and product
pages for `KombinBot` **and** the terms contain no clause against automated access. Nothing was fetched from a site
that refused us.

| Candidate | Decision | What decided it |
|---|---|---|
| **Mavi** | allowed by robots.txt, **not implemented (403 to KombinBot, task W3, 2026-11-15)** | robots.txt (`User-agent: *`): `Disallow: /search/`, `/login`, `/checkout`, `/cart`, `/my-account`, `/*?q=*`, `/*?_gl=*`, `/*?f=*`, `Allow: /*/jean/*?q=*fit*`; category (`/erkek/jean/c/2`) and product (`/<slug>/p/<sku>`) pages are allowed, and the member agreement (`/uyelik-sozlesmesi`) only forbids overloading the servers with automatic programs (read 2026-11-14). On 2026-11-14 a listing page carried a JSON-LD `ItemList` of `Product`s (24 a page, `?page=N`) while sizes with stock and the reduced price sat only in the Angular transfer state (`ng-state` → `__spaTransferState__`, a schema-compressed `values`/`schemas` array with `~<base36>` references). **On 2026-11-15, task W3 tried to read that listing page again (`/erkek/jean/c/2`) and got `HTTP 403` three times (over about an hour), from the same client that had been served the day before, and `robots.txt` itself still answered 200.** A 403 means the site is refusing us, so per the hard rules we leave it alone: no other user agent, no browser-pane copy, no retry loop. Consequently **no Mavi module, no fixtures and no decoder were written**: a decoder for the transfer state cannot be built or tested without real pages, and inventing a sample would be fabricating data. Follow-up: retry in a few days, or take Mavi from an affiliate feed; if a page becomes readable the plan is the one in the task (decode the state, fall back to JSON-LD fields with `stock = Unknown`) |
| **LC Waikiki** | **disallowed (terms)** | robots.txt allows `/` (it disallows only `/marka/lc-waikiki*`, `/*marka=lc-waikiki*`, `/magaza/`, `/outlet/` and filter parameters), **but the terms of use (`/statik/kullanimkosullari`) forbid, without LC Waikiki's prior written permission, using automatic programs, web crawlers, robots, data mining / data crawling or "screen scraping" software on the site to take its content**. No module |
| **DeFacto** | **disallowed (robots unreadable)** | `https://www.defacto.com.tr/robots.txt` → `HTTP 403 Access Denied` (Akamai edge error page) for `KombinBot`, again on 2026-11-14 (first seen 2026-11-01). Rules we cannot read are treated as `Disallow: /`; we do not retry with another identity. No module |
| **Colin's** | **allowed, implemented** | robots.txt: `Disallow: /urunler/`, `/markalar/`, `/hedefler/`, `/sporlar/`, `/kampanyalar/`, `/cart`, `/checkout`, `*search*`, `*price*`, `*term*`, `*orderby*` (full list in `tests/fixtures/colins/robots.txt`); `/c/…` and `/p/…` are not covered. The pages the footer links to (`/t/politikalarimiz`, `/t/uyelik-islemleri`, `/t/gizlilikveguvenlik`) have no clause on automated access; there is no separate terms-of-use page |
| **Network** | **allowed, implemented** | robots.txt: `Disallow: /product/`, `/en/product/`, `/search`, `/api/`, `/cart/`, `*customer*`, `*login*`, filter and sort query strings. That `/product/` rule does **not** cover the product pages (`/<slug>-p-<id>`) nor the category pages (`/<slug>-<id>`). Terms of use (`/content/kullanim-sartlari`): a general copyright clause (no copying or republishing the site's material, code and software included) and no clause on automated access. We store facts (name, price, sizes, availability), the product URL and image **URLs**, never the images |
| **İpekyol** | **disallowed (robots unreadable)** | `https://www.ipekyol.com.tr/robots.txt` → `HTTP 418` with a "CloudWAF – Erişiminiz Engellendi!" block page. Unreadable rules count as `Disallow: /`. Same group as Twist, but Twist runs on its own host with readable rules. No module |
| **Twist** | **allowed, implemented (first page of each category)** | robots.txt: `Disallow: /*?page=*`, `/*&page=`, `/*?beden=*`, `/*?renk*`, `/*?sort*`, `*sezon*`, `*cinsiyet*`, `*gender*`, `/u/*`, `/Page/Get*`, `/Widget/Get*`, `/*urun/siyah-nakisli-askili-elbise`; category pages and `/urun/<slug>` are allowed. Because paging is disallowed we read each category's first page only. The footer has no terms-of-use page (only returns, contact, cookie policy, BDDK information) |
| **Hotiç** | **allowed, implemented** | robots.txt: `Allow: /`, `Disallow: *category`, `*ordering`, `*searchValue`, `*defaultMinPrice`, `*defaultMaxPrice`, `*script*`, account and basket paths. `?pageIndex=N` is not covered. The footer's policy pages (sales policy, distance-sales agreement, KVKK) have no clause on automated access; there is no terms-of-use page |
| **Flo** | **disallowed (terms)** | robots.txt allows `/` for `*` (filters disallowed) – and lists AI crawlers (ClaudeBot, anthropic-ai, GPTBot-style names, Bytespider, ...) under `Disallow: /`; `KombinBot` is not named, so robots.txt alone would not stop us. **The member agreement (`/uyelik-sozlesmesi`) forbids, without Flo's prior written permission, automatic programs, robots, web crawlers, data mining / crawling and "screen scraping" software on the platform.** No module |

Marketplaces (Trendyol, Hepsiburada, Amazon) were not looked at: they come through affiliate feeds later.

## Images: all of a product's pictures, packshots first (task W3, 2026-11-15)

**Why.** The try-on feature only works with photos that show the garment without a person (flat / packshot, ghost
mannequin, on a hanger). So each source sends a product's whole gallery and puts the images most likely to be person-free
first. `normalize/images.py` does the ordering; the sources only call `order_gallery` from their `gallery_urls()`.

**Backend contract** (`kombin-backend`: `UpsertOffers/Validator.cs`, `OfferUpserter.AddMissingImagesAsync`, README "Images"):
- `imageUrls` accepts **at most 6** entries per item (`MaxImages = 6`) – more than 6 fails validation of the whole item. The task
  asked for a cap of 8: the worker therefore *collects* up to 8, orders them, and sends the first 6 (`MAX_IMAGES = 6` in
  `models.py`). Ordering happens before the cut, so a packshot that is 7th on the site still goes out. Raising the backend
  limit to 8 would be a one-line change on its side.
- The order is kept (`SortOrder` follows the array), duplicates by URL are dropped, and `PrimaryImageUrl` is the first image.
- **Images are added only when an offer is created or re-linked, not on plain re-observation.** Products that already exist
  in the catalog therefore keep the gallery (and the primary image) they got on their first upsert; a new order or extra
  images only reach new products. Fixing that needs a backend change (e.g. refresh when the first image differs); this task
  does not touch the backend.

**Rule.** Evidence is the URL and the site's own data only – an image is never downloaded to be classified (the vision
service does that later):
1. a word in the file path (`packshot`, `flat`, `flatlay`, `ghost`, `hanger`, `still-life`/`stillife`, `cutout`, `detail`/`detay`) or a
   per-host rule below → likely person-free;
2. a word such as `model`, `manken`, `lookbook`, `lifestyle`, `editorial`, `campaign` → likely worn;
3. otherwise unknown. Order: likely person-free, unknown, likely worn – each group in the site's own order. A site with
   no hint keeps its own order.

| Source | Gallery read from | Hint used | Verified how |
|---|---|---|---|
| Derimod | `images[]` by `position` (up to 10 seen) | none: hashed file names, no `alt`, `variant_ids` empty | metadata of 2 products from 2 collections (live) and the saved collection fixture |
| Koton | `productimage_set` by `order` (3-5) | none: GUID file names, no alt | metadata of 3 product pages; gallery opens with on-figure shots on the one page viewed |
| Jack & Jones | `media[]` (7-9) | **`.png` (`mimeType image/png`) first**: transparent-background cut-outs; `.jpg` shots after. `isFrontImage` / `isBackImage` are not used | metadata of 2 product pages (5 of 9 files are `.png` on one, 1 of 7 on the other). **Not confirmed by eye** |
| Network | `MediaList[]` by `DisplayOrder` (3-7) | none: `AltText` / `Title` / `MediaFormat` null | metadata of 21 products (listing records) |
| Hotiç | `pictures[]` by `displayOrder` (4-8) | none: `mediaType` 1 / `mediaPositionType` 0 on all | metadata of 33 products (listing records) |
| Twist | `documents[]` (6-7) | **path folder `/Stillife/` first** (still-life shots); `/Koleksiyon/` unknown | metadata of 2 product pages (one of each folder). **Not confirmed by eye** |
| Colin's | ld+json `image` (6-8) | none: numeric ids, no alt | metadata of the saved pages and 2 live pages |

**What is and is not verified.** The hint rules rest on file names, MIME types, folder names and field values read from real
pages and listing records on 2026-11-15. The images themselves were **not** classified: the task forbids downloading images to
classify them, and a visual spot check (bulk image download, an in-page contact sheet) was refused in this session, so the
"person-free" meaning of `.png` at Jack & Jones and of `Stillife` at Twist is an inference from naming and format, not an
observation, and the other five sources give no hint at all (so for them the share of "first image likely person-free" is 0 by
construction). A person looking at one product page per brand should confirm or drop the two rules in `HOST_RULES`.

**Twist: beyond page one (decision).** `Disallow: /*?page=*` and `/*&page=` stay in force, so no `?page=` URL is ever built. The
`Sitemap:` line of robots.txt lists `/sitemap.xml`, an index of `/export/Twist/sitemap/products-1.xml` (1263 `/urun/<slug>`
URLs, 210 KB, all three files allowed by robots.txt), `sitemap-categories-tr.xml` and `sitemap-blogs-tr.xml`; only product
sitemaps are followed. The terms are unchanged (no terms-of-use page). The slugs end in the product type, so slugs whose
type has no category in our taxonomy (dress, blouse, vest, earrings ... – 585 of 1263) are dropped before any request, and
the remaining ~680 are dealt out one category at a time so the per-source cap (400) takes an even share. Each product costs one
request at 1 request/second, as before; `robots.txt` is also checked per URL (`/urun/siyah-nakisli-askili-elbise` is
disallowed). If the sitemap cannot be read the source falls back to the first page of every category.

**Bot contact.** `BOT_CONTACT` still has no real value. The `User-Agent` now omits the `contact:` part when it is unset, empty or
still a `[...]` placeholder (`KombinBot/0.1 (+https://kombin.app/bot)`); `[YOUR EMAIL]` is never sent, and a
`bot_contact_not_set` warning is logged at start. **Set a real, non-personal address (a role mailbox, not a private one) before
production.** Note: every request made before this change, in earlier tasks, carried the literal `[YOUR EMAIL]`.

### Runs after the image change (2026-11-15)

`uv run kombin-workers run <slug>` for every source, defaults (cap 400, 1 request/second per host), against
`http://localhost:8081`, all seven in parallel (one process per host). "Images" is the average number of image URLs **sent**
(the backend takes at most 6, so galleries of 7-10 count as 6); "first likely person-free" is the share of products whose first
sent image matches a hint rule (`normalize.images`), i.e. it measures the rule, not the pictures.

| Source | Upserted (created / updated) | Skipped | Failures | Images / product | First image likely person-free | Duration |
|---|---|---|---|---|---|---|
| derimod | 387 (366 / 21) | unknown-category 13 | none | 5.8 | 0 % (no hint on this site) | 200 s |
| koton | 387 (193 / 194) | unknown-category 13 | none | 5.3 | 0 % (no hint) | 420 s |
| jack-jones | 372 (7 / 365) | unknown-category 28 | none | 5.7 | **99 %** (`.png` rule, unverified by eye) | 416 s |
| network | 398 (0 / 398) | unknown-category 2 | none | 5.5 | 0 % (no hint) | 28 s |
| hotic | 400 (3 / 397) | – | none | 5.9 | 0 % (no hint) | 39 s |
| twist | 399 (222 / 177) | unknown-category 1 | none | 5.4 | **21 %** (`Stillife` folder) | 405 s |
| colins | 393 (1 / 392) | unknown-audience 7 | none | 5.5 | 0 % (no hint) | 434 s |

No backend errors, no fetch failures, no robots blocks. Derimod answered 429 (`Retry-After: 60`) to its first request twice and
was served after the client waited (200 s in total). Twist went from 202 to 399 products (+222 new) through the sitemap and
now has `kaban`, `sneaker` and `bot` products it did not have from the category pages.

**Effect on the catalog:** because the backend stores images only when an offer is created, the 1,944 *updated* offers kept
the gallery they already had. Checked on the local catalog: of 372 Jack & Jones products 51 have a `.png` as primary image
(the 7 new ones plus products whose first image happened to be a `.png`), and of 424 Twist products 86 have a `Stillife`
primary image (new products, whose primary image therefore follows the new order; galleries were not compared item by item).

## Looked at and not used (earlier tasks)

| Candidate | Finding |
|---|---|
| **Boyner** | robots.txt has `Disallow: /product/` – the product detail pages. **Blocked by robots**, no module written |
| **Vakko** | robots.txt allows product pages (`Allow: /`) and has a sitemap (21k products); product pages embed the full SAP Spartacus state (`cx-state`) with price, stock, sizes, brand. But the catalogue is mixed (home goods, other designer brands), pagination parameters are disallowed and discovery would have to be sitemap-only with a per-item brand filter. A good candidate for a later task |
| **Pull&Bear, Beymen Club** | not investigated (Inditex-style bot protection expected for Pull&Bear; Beymen Club is not a standalone store). Nike, Adidas and Zara are excluded by the task |

## First runs against the local backend (2026-11-14)

`uv run kombin-workers run <slug>` with the defaults (cap 400, 1 request/second per host), against `http://localhost:8081`.
Upserted = accepted by the backend (no per-item errors in any run). Fetch failures: none.

| Source | Discovered | Upserted | Skipped | Women / Men | Duration |
|---|---|---|---|---|---|
| hotic | 400 | 400 | – | 213 / 187 | 34 s |
| network | 400 | 398 (second run; see below) | unknown-category 2 | 212 / 186 | 25 s |
| twist | 203 | 202 | unknown-category 1 | 202 / – | 214 s |
| colins | 400 | 393 | unknown-audience 7 | 208 / 185 | 428 s |

Per category (upserted):

| Category | hotic | network | twist | colins |
|---|---|---|---|---|
| ust-giyim/tisort | | 42 | 23 | 60 |
| ust-giyim/gomlek | | 46 | 24 | 58 |
| ust-giyim/triko | | 45 | 35 | 64 |
| ust-giyim/blazer | | | 7 | |
| alt-giyim/pantolon | | 44 | 24 | 45 |
| alt-giyim/jean | | 43 | 24 | 21 |
| alt-giyim/etek | | 21 | 24 | 10 |
| dis-giyim/mont | | 36 | | 40 |
| dis-giyim/kaban | | 39 | | 9 |
| dis-giyim/ceket | | 44 | 17 | 37 |
| ayakkabi/sneaker | 81 | 13 | | |
| ayakkabi/bot | 119 | 16 | | |
| ayakkabi/loafer | 136 | 9 | | |
| aksesuar/canta | 48 | | 24 | 13 |
| aksesuar/kemer | 16 | | | 36 |

Notes:
- **Network, first run:** 396 upserted and 2 items rejected by our own model: two products have an image file name with a
  space (`..._04 .jpg`). Spaces are now percent-encoded; the second run upserted those (created 2, updated 396).
- **Twist** discovers only ~200: first page per category (24), because `?page=` is disallowed. The "dış giyim" page holds
  jackets and blazers, no coats (`dis-giyim/mont` and `kaban` are empty for Twist).
- **Colin's** 7 skipped items are unisex accessories whose name and breadcrumb carry no gender word.
- **Shoe categories at Network** are small because the site's boot / loafer / sneaker pages hold only 9-16 products.
- Colin's `kemer` and Twist/Hotiç `canta` counts are higher than the 400-product even split suggests because those
  listings are the only ones in their category.

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
- **Per-run cap.** `--max-pages` (default 5) pages per category, read page-major; a source also stops after
  `KOMBIN_MAX_ITEMS_PER_SOURCE` discovered products (default **400**; `Settings.max_items_per_source`).
- **A capped run stays balanced.** `paged_discovery` reads page 1 of every listing, then deals the products out one
  listing at a time (first of listing A, first of B, ...). The sources list women's and men's category pages alternately, so
  400 products are an even share of every category and of both audiences, not the first few categories.
- **Structured data first.** Network, Hotiç and Twist read JSON the site embeds for its own front end (`sources/embedded.py`:
  `__NEXT_DATA__`, `application/ld+json`); Colin's reads the data attributes of its listing cards plus the product
  page's ld+json. No CSS selectors for prices, sizes or stock.
- **Stock signals that lie.** Colin's ld+json says `InStock` on sold-out pages; Network's `ProductPromotion.PromotedPrice` is
  a basket-only price. Each source documents which field it trusts.
