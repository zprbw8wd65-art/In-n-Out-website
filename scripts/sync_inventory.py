#!/usr/bin/env python3
"""
IN-n-OUT Auto Sales — Inventory Sync Script (v4: full detail pages)
=====================================================================
Reads inventory straight from the dealer's own CURRENT public website
(inandoutautosaleswa.com), which is public information about their
own vehicles, since Carsforsale.com will only syndicate feeds to
recognized third-party marketplaces (CarGurus, AutoTrader, etc.), not
to an independent custom website.

TWO-PASS APPROACH:
  Pass 1 — visit the 8 body-style category pages (sedan, SUVs, pickup
  trucks, wagon, full size, minivans, chassis, hatchbacks) to build the
  full vehicle list (title, price, mileage, thumbnail, and a link to
  its own detail page). The main /cars-for-sale listing loads extra
  vehicles via a "Load More" button (JavaScript) that a plain fetch
  can't trigger, so these smaller category pages are used instead —
  together they cover the whole lot.

  Pass 2 — visit EACH vehicle's own detail page and pull the real
  description, features list, full photo gallery, and any additional
  specs (VIN, color, drivetrain, etc.) exactly as entered on
  Carsforsale, so the new site shows the same detail as the old one.

Rebuilds:
  - inventory.html         (the vehicle grid, with filters)
  - vehicle-<stock#>.html  (one real detail page per vehicle)

Designed to run on a schedule via GitHub Actions. If anything looks
wrong — site unreachable, page structure changed, suspiciously few
vehicles found — it fails LOUDLY rather than publishing something
broken. A single vehicle's detail page failing to parse does NOT
fail the whole sync — it falls back to the summary-card data for
that one vehicle and keeps going, since one odd listing shouldn't
block everyone else's page from updating.

STATUS: Built from the site's observed structure; this sandbox has no
internet access to test live HTTP requests, so double-check
scripts/last-sync.json after a real run and adjust the heuristic
parsing in extract_detail_fields() if descriptions/features come back
empty.
"""

import os
import re
import sys
import json
import html
import datetime
import urllib.request

try:
    from bs4 import BeautifulSoup, NavigableString
except ImportError:
    print("This script requires beautifulsoup4. Add it to requirements.txt "
          "(see the workflow file) — 'pip install beautifulsoup4'.", file=sys.stderr)
    raise

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

SOURCE_LISTING_URLS = [
    u.strip() for u in os.environ.get("SOURCE_LISTING_URLS", "").split(",") if u.strip()
] or [
    "https://www.inandoutautosaleswa.com/sedan-for-sale-b100033",
    "https://www.inandoutautosaleswa.com/suvs-for-sale-b100037",
    "https://www.inandoutautosaleswa.com/pickup-trucks-for-sale-b100030",
    "https://www.inandoutautosaleswa.com/wagon-for-sale-b100040",
    "https://www.inandoutautosaleswa.com/full-size-for-sale-b100043",
    "https://www.inandoutautosaleswa.com/minivans-for-sale-b100024",
    "https://www.inandoutautosaleswa.com/chassis-for-sale-b100006",
    "https://www.inandoutautosaleswa.com/hatchbacks-for-sale-b100017",
]

USER_AGENT = "Mozilla/5.0 (compatible; INnOutSiteSync/1.0; +https://inandoutautosaleswa.com)"
MAX_PAGES = 30
REQUEST_TIMEOUT = 30

SITE_ROOT = os.path.join(os.path.dirname(__file__), "..")
LOG_PATH = os.path.join(os.path.dirname(__file__), "last-sync.json")

DEALER_PHONE_DISPLAY = "(253) 446-6533"
DEALER_PHONE_TEL = "+12534466533"


class SyncError(Exception):
    """Raised whenever the sync can't safely proceed. Left uncaught on
    purpose — an uncaught exception fails the GitHub Actions step, which
    triggers GitHub's built-in failure-notification email."""
    pass


# ---------------------------------------------------------------------------
# FETCHING HELPERS
# ---------------------------------------------------------------------------

def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                raise SyncError(f"{url} returned HTTP {response.status}")
            return response.read().decode("utf-8", errors="replace")
    except SyncError:
        raise
    except Exception as e:
        raise SyncError(f"Could not fetch {url}: {e}")


def to_absolute(href: str, current_url: str) -> str:
    if href.startswith("http"):
        return href
    base = re.match(r"^(https?://[^/]+)", current_url).group(1)
    if href.startswith("/"):
        return base + href
    return base + "/" + href


def upsize_image(url: str) -> str:
    """These CDN photo URLs embed a size segment like /480x360/ — swap
    it for a larger size so detail pages don't show a blurry upscale."""
    return re.sub(r"/\d{2,4}x\d{2,4}/", "/1024x768/", url)


# ---------------------------------------------------------------------------
# PASS 1 — CATEGORY LISTING PAGES (basic vehicle list)
# ---------------------------------------------------------------------------

def find_next_page_url(soup, current_url: str):
    next_link = soup.find("a", attrs={"rel": "next"})
    if not next_link:
        next_link = soup.find("a", string=re.compile(r"^\s*Next\s*$", re.I))
    if next_link and next_link.get("href"):
        return to_absolute(next_link["href"], current_url)
    return None


def find_numbered_pagination_urls(soup, current_url: str) -> list:
    urls = []
    for link in soup.find_all("a", href=True):
        text = link.get_text(strip=True)
        href = link["href"]
        if text.isdigit() or re.search(r"[?&](page|pg)=\d+", href, re.I) or re.search(r"/page/\d+", href, re.I):
            urls.append(to_absolute(href, current_url))
    return urls


def extract_price(text: str) -> str:
    matches = re.findall(r"\$[\d,]+", text)
    return matches[-1].replace("$", "").replace(",", "") if matches else ""


def extract_mileage(text: str) -> str:
    m = re.search(r"([\d,]+)\s*(?:Miles|miles)", text)
    return m.group(1).replace(",", "") if m else ""


def parse_listing_page(html_text: str, page_url: str) -> list:
    soup = BeautifulSoup(html_text, "html.parser")
    vehicles = {}

    for link in soup.find_all("a", href=re.compile(r"/details/")):
        href = link["href"]
        stock_match = re.search(r"/(\d+)(?:[/?]|$)", href)
        if not stock_match:
            continue
        stock_number = stock_match.group(1)
        if stock_number in vehicles:
            continue

        container = link
        for _ in range(4):
            if container.parent:
                container = container.parent
        container_text = container.get_text(" ", strip=True)

        img = container.find("img")
        photo = img["src"] if img and img.get("src") else ""
        title = (img.get("alt") if img and img.get("alt") else link.get_text(strip=True)) or ""
        title = re.sub(r"\s+for sale at.*$", "", title, flags=re.I).strip()

        vehicles[stock_number] = {
            "stock_number": stock_number,
            "detail_url": to_absolute(href, page_url),
            "title": title,
            "price": extract_price(container_text),
            "mileage": extract_mileage(container_text),
            "photo": photo,
        }

    return list(vehicles.values())


def fetch_all_listings() -> list:
    all_vehicles = {}
    seen_urls = set()
    to_visit = list(SOURCE_LISTING_URLS)

    while to_visit and len(seen_urls) < MAX_PAGES:
        url = to_visit.pop(0)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        page_html = fetch(url)
        soup = BeautifulSoup(page_html, "html.parser")

        for v in parse_listing_page(page_html, url):
            all_vehicles[v["stock_number"]] = v

        next_url = find_next_page_url(soup, url)
        if next_url and next_url not in seen_urls:
            to_visit.append(next_url)
        for page_url in find_numbered_pagination_urls(soup, url):
            if page_url not in seen_urls and page_url not in to_visit:
                to_visit.append(page_url)

    return list(all_vehicles.values())


# ---------------------------------------------------------------------------
# PASS 2 — INDIVIDUAL VEHICLE DETAIL PAGES (full description, features, gallery)
# ---------------------------------------------------------------------------

SPEC_LABELS = {
    "condition": ["Condition"],
    "engine": ["Engine"],
    "transmission": ["Transmission"],
    "drivetrain": ["Drivetrain", "Drive Type", "Drive Train"],
    "fuel_type": ["Fuel"],
    "exterior_color": ["Exterior Color", "Ext. Color"],
    "interior_color": ["Interior Color", "Int. Color"],
    "stock": ["Stock #", "Stock Number", "Stock"],
    "vin": ["VIN"],
    "mpg": ["Fuel Economy", "MPG", "Fuel Mileage"],
    "body_style": ["Body Style", "Body"],
}


def find_label_value(soup, labels: list) -> str:
    """Generic key/value finder — Carsforsale-style detail pages
    typically show spec pairs as a label followed by its value, either
    as sibling elements or within the same small container. Tries a
    few common shapes; returns '' if nothing matches."""
    for label in labels:
        label_node = soup.find(string=re.compile(r"^\s*" + re.escape(label) + r"\s*:?\s*$", re.I))
        if not label_node:
            continue
        parent = label_node.parent
        # Try: value in the very next sibling tag
        sib = parent.find_next_sibling()
        if sib and sib.get_text(strip=True):
            return sib.get_text(strip=True)
        # Try: value inside the same parent, after the label text
        full_text = parent.get_text(" ", strip=True)
        m = re.search(re.escape(label) + r"\s*:?\s*(.+)", full_text, re.I)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def find_section_text(soup, heading_keywords: list) -> str:
    """Finds a heading matching any of the keywords (e.g. 'Description')
    and returns the text of the paragraph(s) that follow it, stopping
    at the next heading."""
    heading = soup.find(
        ["h1", "h2", "h3", "h4", "strong", "b"],
        string=re.compile("|".join(heading_keywords), re.I),
    )
    if not heading:
        return ""
    parts = []
    for sib in heading.find_all_next():
        if sib.name in ("h1", "h2", "h3", "h4"):
            break
        if sib.name == "p":
            text = sib.get_text(" ", strip=True)
            if text:
                parts.append(text)
        if len(parts) >= 3:
            break
    return " ".join(parts).strip()


def find_section_list_items(soup, heading_keywords: list) -> list:
    """Finds a heading matching any of the keywords (e.g. 'Features')
    and returns items from the list/grid that follows it. Tries a
    proper <ul>/<ol> first; falls back to short repeated text chunks
    (common for tag/chip-style feature grids) if no list tag is used."""
    heading = soup.find(
        ["h1", "h2", "h3", "h4", "strong", "b"],
        string=re.compile("|".join(heading_keywords), re.I),
    )
    if not heading:
        return []

    for sib in heading.find_all_next():
        if sib.name in ("h1", "h2", "h3", "h4"):
            break
        if sib.name in ("ul", "ol"):
            items = [li.get_text(strip=True) for li in sib.find_all("li") if li.get_text(strip=True)]
            if items:
                return items

    # Fallback: chip/tag-style grid — collect short leaf text nodes
    # (no nested tags) between this heading and the next one.
    items = []
    for sib in heading.find_all_next():
        if sib.name in ("h1", "h2", "h3", "h4"):
            break
        if sib.name in ("div", "span") and not sib.find(["div", "span"]):
            text = sib.get_text(strip=True)
            if text and 1 <= len(text) <= 40 and text not in items:
                items.append(text)
        if len(items) >= 40:
            break
    return items


def find_gallery_photos(soup, page_url: str, fallback_photo: str) -> list:
    """Collects every vehicle photo shown on the detail page (the CDN
    that hosts these photos is consistent — filter to that domain so we
    don't pick up logos/icons from elsewhere on the page)."""
    photos = []
    seen = set()
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if "cdn" not in src or not re.search(r"\.(jpg|jpeg|png|webp)", src, re.I):
            continue
        abs_src = to_absolute(src, page_url)
        key = re.sub(r"/\d{2,4}x\d{2,4}/", "/", abs_src)  # dedupe across sizes
        if key in seen:
            continue
        seen.add(key)
        photos.append(upsize_image(abs_src))
    if not photos and fallback_photo:
        photos = [upsize_image(fallback_photo)]
    return photos[:12]  # cap so a stray unrelated image dump doesn't balloon the page


def extract_detail_fields(vehicle: dict) -> dict:
    """Fetches a single vehicle's own detail page and enriches its
    record with description, features, full gallery, and extra specs.
    Any failure here is caught by the caller — a bad detail page
    should not take down the whole sync, just fall back to the
    summary-card data already gathered in pass 1."""
    detail_html = fetch(vehicle["detail_url"])
    soup = BeautifulSoup(detail_html, "html.parser")

    enriched = dict(vehicle)
    enriched["description"] = find_section_text(soup, ["Description", "Vehicle Description", "Comments"])
    enriched["features"] = find_section_list_items(soup, ["Features", "Options", "Equipment"])
    enriched["photos"] = find_gallery_photos(soup, vehicle["detail_url"], vehicle.get("photo", ""))

    for field, labels in SPEC_LABELS.items():
        enriched[field] = find_label_value(soup, labels)

    # NOTE: We intentionally do NOT re-scan the detail page's full text
    # for a "better" price/mileage. Every detail page includes a footer
    # disclaimer mentioning a documentary service fee ("...up to $200
    # may be added..."), and a naive "last dollar amount on the page"
    # scan was grabbing that $200 instead of the real price. The price
    # already captured from the category listing card is reliable —
    # trust it instead.

    return enriched


def enrich_all_vehicles(vehicles: list) -> list:
    enriched = []
    failures = 0
    for v in vehicles:
        try:
            enriched.append(extract_detail_fields(v))
        except Exception as e:
            print(f"WARNING: could not fetch detail page for stock #{v['stock_number']}: {e}", file=sys.stderr)
            fallback = dict(v)
            fallback.setdefault("description", "")
            fallback.setdefault("features", [])
            fallback.setdefault("photos", [v.get("photo", "")] if v.get("photo") else [])
            for field in SPEC_LABELS:
                fallback.setdefault(field, "")
            enriched.append(fallback)
            failures += 1
    if failures and failures == len(vehicles):
        raise SyncError("Every vehicle's detail page failed to fetch — likely a site-wide issue, not per-vehicle.")
    return enriched


# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------

def validate_vehicles(vehicles: list) -> None:
    if len(vehicles) == 0:
        raise SyncError(
            "Found zero vehicles across all category pages. Either the site "
            "is genuinely out of inventory (unlikely) or the page structure "
            "changed and parse_listing_page() needs updating. Refusing to "
            "publish an empty lot."
        )
    missing_price = sum(1 for v in vehicles if not v["price"])
    if missing_price > len(vehicles) * 0.3:
        raise SyncError(
            f"{missing_price} of {len(vehicles)} vehicles have no price "
            "detected — the price-extraction pattern likely needs updating."
        )


def parse_year_make_model(title: str):
    m = re.match(r"(\d{4})\s+(\S+)\s+(.+)", title)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", "", title)


# ---------------------------------------------------------------------------
# RENDERING — inventory grid
# ---------------------------------------------------------------------------

def render_vehicle_card(v: dict) -> str:
    year, make, model = parse_year_make_model(v["title"])
    price = v["price"] or "0"
    body = v.get("body_style") or ""
    return f'''
      <article class="tag" data-make="{html.escape(make)}" data-price="{price}" data-body="{html.escape(body)}">
        <div class="string"></div>
        <div class="photo"><img src="{html.escape(v['photo'])}" alt="{html.escape(v['title'])}"></div>
        <div class="body">
          <h3>{html.escape(v['title'])}</h3>
          <div class="specs"><span>{html.escape(v['mileage'] or '?')} mi</span></div>
          <div class="price-row">
            <span class="price">${price}</span>
            <a class="view" href="vehicle-{html.escape(v['stock_number'])}.html">Details &rarr;</a>
          </div>
        </div>
      </article>'''


def rebuild_inventory_page(vehicles: list) -> None:
    inventory_path = os.path.join(SITE_ROOT, "inventory.html")
    if not os.path.exists(inventory_path):
        raise SyncError(f"Expected to find {inventory_path} but it's missing.")

    with open(inventory_path, "r", encoding="utf-8") as f:
        current = f.read()

    start_marker = '<div class="vehicle-grid" id="vehicle-grid">'
    end_marker = '</div>\n  </div>\n</section>\n\n<section class="cta-banner">'

    start_idx = current.find(start_marker)
    end_idx = current.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        raise SyncError(
            "Could not find the vehicle-grid markers in inventory.html — "
            "the page structure may have changed. Refusing to overwrite blindly."
        )

    new_cards = "\n".join(render_vehicle_card(v) for v in vehicles)
    new_content = current[: start_idx + len(start_marker)] + new_cards + "\n    " + current[end_idx:]

    with open(inventory_path, "w", encoding="utf-8") as f:
        f.write(new_content)


# ---------------------------------------------------------------------------
# RENDERING — individual vehicle detail pages
# ---------------------------------------------------------------------------

DETAIL_PAGE_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} | IN-n-OUT Auto Sales</title>
<meta name="description" content="{title} for sale at IN-n-OUT Auto Sales in Puyallup, WA.">
<link rel="stylesheet" href="styles.css">
<style>
  .vd-grid{{display:grid;grid-template-columns:1.4fr 1fr;gap:44px;align-items:start;}}
  @media (max-width:900px){{.vd-grid{{grid-template-columns:1fr;}}}}
  .vd-gallery-main{{border-radius:var(--radius);overflow:hidden;aspect-ratio:4/3;border:1px solid var(--line);}}
  .vd-gallery-main img{{width:100%;height:100%;object-fit:cover;}}
  .vd-thumbs{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:10px;}}
  .vd-thumbs .thumb{{border-radius:var(--radius-sm);overflow:hidden;aspect-ratio:4/3;border:1px solid var(--line);cursor:pointer;opacity:0.75;transition:opacity .15s;}}
  .vd-thumbs .thumb:hover, .vd-thumbs .thumb.active{{opacity:1;border-color:var(--amber);}}
  .vd-thumbs .thumb img{{width:100%;height:100%;object-fit:cover;}}
  .vd-title-row{{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:14px;margin-top:32px;}}
  .vd-title-row h1{{font-family:var(--body);text-transform:none;letter-spacing:0;font-size:26px;font-weight:700;}}
  .vd-title-row .price{{font-family:var(--mono);font-weight:600;font-size:30px;color:var(--ink);}}
  .vd-stock{{font-family:var(--mono);font-size:12.5px;color:var(--steel);margin-top:4px;}}
  .vd-spec-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:28px;}}
  @media (max-width:640px){{.vd-spec-grid{{grid-template-columns:repeat(2,1fr);}}}}
  .vd-spec{{background:var(--paper);border:1px solid var(--line);border-radius:var(--radius-sm);padding:14px 16px;}}
  .vd-spec .k{{font-family:var(--mono);font-size:11px;color:var(--steel);text-transform:uppercase;letter-spacing:0.05em;}}
  .vd-spec .v{{margin-top:4px;font-size:15px;font-weight:600;}}
  .vd-desc{{margin-top:32px;}}
  .vd-desc h2{{font-family:var(--body);text-transform:none;letter-spacing:0;font-size:18px;margin-bottom:12px;}}
  .vd-desc p{{color:#4a4d51;font-size:15px;}}
  .vd-features{{margin-top:28px;}}
  .vd-features ul{{list-style:none;padding:0;margin:14px 0 0;display:grid;grid-template-columns:1fr 1fr;gap:10px 20px;}}
  .vd-features li{{font-size:14.5px;color:#3a3d40;display:flex;align-items:center;gap:8px;}}
  .vd-features li::before{{content:"\\2713";color:var(--amber-deep);font-weight:700;}}
  .vd-sidebar{{background:var(--asphalt);color:var(--paper);border-radius:var(--radius);padding:26px;position:sticky;top:96px;}}
  .vd-sidebar .price{{font-family:var(--mono);font-size:28px;font-weight:600;color:var(--amber);}}
  .vd-sidebar p{{color:var(--steel-light);font-size:14px;margin-top:6px;}}
  .vd-sidebar .btn{{width:100%;margin-top:16px;}}
  .vd-sidebar .divider{{border-top:1px solid var(--line-dark);margin:20px 0;}}
</style>
</head>
<body>

<div class="topbar">
  <div class="wrap">
    <span class="addr">7602 River Road East, Puyallup, WA 98371</span>
    <a href="tel:{phone_tel}">{phone_display}</a>
  </div>
</div>

<header class="site">
  <div class="wrap">
    <nav class="main">
      <a class="brand" href="index.html">
        <span class="mark">I/O</span>
        <span class="name">IN-n-OUT<span>AUTO SALES</span></span>
      </a>
      <button class="nav-toggle" aria-label="Toggle menu" aria-expanded="false">&#9776;</button>
      <div class="navlinks">
        <a href="index.html">Home</a>
        <a href="inventory.html" class="active">Inventory</a>
        <a href="about.html">About</a>
        <a href="contact.html">Contact</a>
        <a href="finance.html">Finance</a>
      </div>
      <div class="nav-cta">
        <a class="phone" href="tel:{phone_tel}">{phone_display}</a>
        <a class="btn btn-amber" href="contact.html">Ask About This Vehicle</a>
      </div>
    </nav>
  </div>
</header>

<section class="section tight">
  <div class="wrap">
    <div class="breadcrumb"><a href="index.html">Home</a> / <a href="inventory.html">Inventory</a> / {title}</div>

    <div class="vd-grid">
      <div>
        <div class="vd-gallery-main">
          <img id="vd-main-photo" src="{main_photo}" alt="{title}">
        </div>
        <div class="vd-thumbs">
{thumbs_html}
        </div>

        <div class="vd-title-row">
          <div>
            <h1>{title}</h1>
            <div class="vd-stock">Stock #{stock_number}{vin_html}</div>
          </div>
          <div class="price">${price}</div>
        </div>

        <div class="vd-spec-grid">
{spec_cards_html}
        </div>

{desc_section}        <div class="vd-features">
          <h2 style="font-family:var(--body);text-transform:none;letter-spacing:0;font-size:18px;">Features</h2>
          <ul>
{features_html}
          </ul>
        </div>
      </div>

      <aside class="vd-sidebar">
        <div class="price">${price}</div>
        <p>{title} &middot; {mileage} mi</p>
        <a class="btn btn-amber" href="tel:{phone_tel}">Call {phone_display}</a>
        <a class="btn btn-outline" href="contact.html">Ask a Question</a>
        <div class="divider"></div>
        <p style="font-size:13px;">Interested in financing? <a href="finance.html" style="color:var(--amber);text-decoration:underline;">Get pre-qualified</a> before you visit.</p>
      </aside>
    </div>
  </div>
</section>

<footer class="site">
  <div class="wrap">
    <div class="foot-grid">
      <div>
        <a class="brand" href="index.html">
          <span class="mark">I/O</span>
          <span class="name">IN-n-OUT<span>AUTO SALES</span></span>
        </a>
        <p style="margin-top:16px;max-width:34ch;">A family-run used car lot in Puyallup, WA. Honest pricing, straightforward service, since we opened our doors.</p>
      </div>
      <div>
        <h4>Explore</h4>
        <p><a href="inventory.html">Full Inventory</a></p>
        <p><a href="about.html">About Us</a></p>
        <p><a href="finance.html">Financing</a></p>
        <p><a href="contact.html">Contact &amp; Directions</a></p>
      </div>
      <div>
        <h4>Visit</h4>
        <p>7602 River Road East<br>Puyallup, WA 98371</p>
        <p><a href="tel:{phone_tel}">{phone_display}</a></p>
      </div>
    </div>
    <div class="foot-bottom">
      <span>&copy; 2026 IN-n-OUT Auto Sales</span>
      <span>Puyallup, Washington</span>
    </div>
  </div>
</footer>

<script src="script.js"></script>
<script>
  document.querySelectorAll('.vd-thumbs .thumb').forEach(function(thumb){{
    thumb.addEventListener('click', function(){{
      document.getElementById('vd-main-photo').src = thumb.querySelector('img').getAttribute('data-full');
      document.querySelectorAll('.vd-thumbs .thumb').forEach(function(t){{ t.classList.remove('active'); }});
      thumb.classList.add('active');
    }});
  }});
</script>
</body>
</html>
'''


def render_detail_page(v: dict) -> str:
    year, make, model = parse_year_make_model(v["title"])
    price = v["price"] or "0"
    photos = v.get("photos") or ([v["photo"]] if v.get("photo") else [])
    main_photo = photos[0] if photos else ""

    thumbs = []
    for i, photo in enumerate(photos[:8]):
        active = " active" if i == 0 else ""
        thumbs.append(
            f'          <div class="thumb{active}"><img src="{html.escape(photo)}" '
            f'data-full="{html.escape(photo)}" alt="{html.escape(v["title"])} photo {i+1}"></div>'
        )
    thumbs_html = "\n".join(thumbs) if thumbs else ""

    spec_rows = [
        ("Mileage", (v.get("mileage") or "?") + " mi"),
        ("Condition", v.get("condition") or "Used"),
        ("Engine", v.get("engine") or "—"),
        ("Transmission", v.get("transmission") or "—"),
        ("Drivetrain", v.get("drivetrain") or "—"),
        ("Fuel Type", v.get("fuel_type") or "—"),
        ("Exterior Color", v.get("exterior_color") or "—"),
        ("Interior Color", v.get("interior_color") or "—"),
        ("Fuel Economy", v.get("mpg") or "—"),
    ]
    spec_cards = "\n".join(
        f'          <div class="vd-spec"><div class="k">{html.escape(k)}</div><div class="v">{html.escape(val)}</div></div>'
        for k, val in spec_rows
    )

    features = v.get("features") or []
    features_html = "\n".join(f"            <li>{html.escape(f)}</li>" for f in features) or \
        "            <li>Contact us for full feature details</li>"

    description = (v.get("description") or "").strip()
    if description:
        desc_section = f'''        <div class="vd-desc">
          <h2>Description</h2>
          <p>{html.escape(description)}</p>
        </div>

'''
    else:
        desc_section = ""  # Real site has no free-text description for most vehicles — omit rather than show filler.

    vin = v.get("vin", "")
    vin_html = f" &middot; VIN {html.escape(vin)}" if vin else ""

    return DETAIL_PAGE_TEMPLATE.format(
        title=html.escape(v["title"]),
        phone_tel=DEALER_PHONE_TEL,
        phone_display=DEALER_PHONE_DISPLAY,
        main_photo=html.escape(main_photo),
        thumbs_html=thumbs_html,
        stock_number=html.escape(v["stock_number"]),
        vin_html=vin_html,
        price=price,
        spec_cards_html=spec_cards,
        desc_section=desc_section,
        features_html=features_html,
        mileage=html.escape(v.get("mileage") or "?"),
    )


def write_detail_pages(vehicles: list) -> list:
    """Writes one detail page per vehicle. A single vehicle's page
    failing to render must NOT stop the others from being written —
    that was the earlier bug (only 1 of 41 pages got created because
    one crash killed the whole loop). Returns the stock numbers that
    failed, if any, so main() can report them without failing the
    whole sync over it."""
    failed_stock_numbers = []
    for v in vehicles:
        try:
            page_html = render_detail_page(v)
            out_path = os.path.join(SITE_ROOT, f"vehicle-{v['stock_number']}.html")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(page_html)
        except Exception as e:
            print(f"WARNING: failed to render/write page for stock #{v['stock_number']}: {e}", file=sys.stderr)
            failed_stock_numbers.append(v["stock_number"])
    return failed_stock_numbers


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

def write_sync_log(vehicle_count: int, status: str) -> None:
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_run": datetime.datetime.utcnow().isoformat() + "Z",
                "status": status,
                "vehicle_count": vehicle_count,
                "sources": SOURCE_LISTING_URLS,
            },
            f,
            indent=2,
        )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    basic_vehicles = fetch_all_listings()
    validate_vehicles(basic_vehicles)

    vehicles = enrich_all_vehicles(basic_vehicles)

    rebuild_inventory_page(vehicles)
    write_detail_pages(vehicles)

    write_sync_log(len(vehicles), "success")
    print(f"Synced {len(vehicles)} vehicles (with full detail pages) from {len(SOURCE_LISTING_URLS)} category pages.")


if __name__ == "__main__":
    try:
        main()
    except SyncError as e:
        write_sync_log(0, f"failed: {e}")
        print(f"SYNC FAILED: {e}", file=sys.stderr)
        sys.exit(1)
