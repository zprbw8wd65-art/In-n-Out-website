#!/usr/bin/env python3
"""
IN-n-OUT Auto Sales — Inventory Sync Script (v2: scrape-based)
================================================================
Carsforsale.com told the dealer they can only syndicate feeds to
recognized third-party marketplaces (CarGurus, AutoTrader, etc.) —
not to an independent custom website. So instead, this script reads
the inventory straight from the dealer's own CURRENT public website
(https://www.inandoutautosaleswa.com), which is public information
about their own vehicles, and uses it to rebuild:

  - inventory.html   (the vehicle grid, with filters)
  - vehicle-<stock#>.html  (one detail page per vehicle)

Designed to run on a schedule via GitHub Actions
(see .github/workflows/sync-inventory.yml). If anything looks wrong —
site unreachable, page structure changed, suspiciously few vehicles
found — it fails LOUDLY (raises an exception) rather than silently
publishing something broken. A failed GitHub Actions run automatically
emails the repo owner (see notes in the workflow file).

STATUS: This is a working scaffold built from the current site's
observed structure. The exact CSS patterns (SOURCE_LISTING_URL parsing
in particular) should be spot-checked against a real run once this is
deployed to GitHub Actions — this sandbox has no internet access to
test live HTTP requests against the real site.
"""

import os
import re
import sys
import json
import html
import datetime
import urllib.request

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("This script requires beautifulsoup4. Add it to requirements.txt "
          "(see the workflow file) — 'pip install beautifulsoup4'.", file=sys.stderr)
    raise

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

SOURCE_LISTING_URL = os.environ.get("SOURCE_LISTING_URL") or "https://www.inandoutautosaleswa.com/cars-for-sale"
USER_AGENT = "Mozilla/5.0 (compatible; INnOutSiteSync/1.0; +https://inandoutautosaleswa.com)"
MAX_PAGES = 20  # safety cap against a pagination loop
REQUEST_TIMEOUT = 30

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..")
LOG_PATH = os.path.join(os.path.dirname(__file__), "last-sync.json")


class SyncError(Exception):
    """Raised whenever the sync can't safely proceed. Left uncaught on
    purpose — an uncaught exception fails the GitHub Actions step, which
    triggers GitHub's built-in failure-notification email."""
    pass


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


def find_next_page_url(soup, current_url: str):
    """Handles a plain 'Next' link, if the site uses one."""
    next_link = soup.find("a", attrs={"rel": "next"})
    if not next_link:
        next_link = soup.find("a", string=re.compile(r"^\s*Next\s*$", re.I))
    if next_link and next_link.get("href"):
        return to_absolute(next_link["href"], current_url)
    return None


def find_numbered_pagination_urls(soup, current_url: str) -> list:
    """Handles numbered pagination (e.g. '1 2 3 4') instead of a 'Next'
    link — collects every page-number link found on the page so all
    pages get visited even if there's no explicit 'Next' button."""
    urls = []
    for link in soup.find_all("a", href=True):
        text = link.get_text(strip=True)
        href = link["href"]
        looks_like_page_number = text.isdigit()
        looks_like_page_param = re.search(r"[?&](page|pg)=\d+", href, re.I) or re.search(r"/page/\d+", href, re.I)
        if looks_like_page_number or looks_like_page_param:
            urls.append(to_absolute(href, current_url))
    return urls


def extract_price(text: str) -> str:
    matches = re.findall(r"\$[\d,]+", text)
    if not matches:
        return ""
    return matches[-1].replace("$", "").replace(",", "")


def extract_mileage(text: str) -> str:
    m = re.search(r"([\d,]+)\s*(?:Miles|miles)", text)
    return m.group(1).replace(",", "") if m else ""


def parse_listing_page(html_text: str) -> list:
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
            "detail_url": href if href.startswith("http") else
                          re.match(r"^(https?://[^/]+)", SOURCE_LISTING_URL).group(1) + href,
            "title": title,
            "price": extract_price(container_text),
            "mileage": extract_mileage(container_text),
            "photo": photo,
        }

    return list(vehicles.values())


def parse_year_make_model(title: str):
    m = re.match(r"(\d{4})\s+(\S+)\s+(.+)", title)
    if not m:
        return "", "", title
    return m.group(1), m.group(2), m.group(3)


def fetch_all_listings() -> list:
    all_vehicles = {}
    seen_urls = set()
    to_visit = [SOURCE_LISTING_URL]

    while to_visit and len(seen_urls) < MAX_PAGES:
        url = to_visit.pop(0)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        page_html = fetch(url)
        soup = BeautifulSoup(page_html, "html.parser")

        for v in parse_listing_page(page_html):
            all_vehicles[v["stock_number"]] = v

        next_url = find_next_page_url(soup, url)
        if next_url and next_url not in seen_urls:
            to_visit.append(next_url)

        for page_url in find_numbered_pagination_urls(soup, url):
            if page_url not in seen_urls and page_url not in to_visit:
                to_visit.append(page_url)

    return list(all_vehicles.values())


def validate_vehicles(vehicles: list) -> None:
    if len(vehicles) == 0:
        raise SyncError(
            "Found zero vehicles on the listing page. Either the site is "
            "genuinely out of inventory (unlikely) or the page structure "
            "changed and the parser in parse_listing_page() needs updating. "
            "Refusing to publish an empty lot."
        )
    missing_price = sum(1 for v in vehicles if not v["price"])
    if missing_price > len(vehicles) * 0.3:
        raise SyncError(
            f"{missing_price} of {len(vehicles)} vehicles have no price "
            "detected — the price-extraction pattern likely needs updating."
        )


def render_vehicle_card(v: dict) -> str:
    year, make, model = parse_year_make_model(v["title"])
    price = v["price"] or "0"
    return f'''
      <article class="tag" data-make="{html.escape(make)}" data-price="{price}" data-body="">
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
    inventory_path = os.path.join(OUTPUT_DIR, "inventory.html")
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
    new_content = (
        current[: start_idx + len(start_marker)]
        + new_cards
        + "\n    "
        + current[end_idx:]
    )

    with open(inventory_path, "w", encoding="utf-8") as f:
        f.write(new_content)


def write_sync_log(vehicle_count: int, status: str) -> None:
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_run": datetime.datetime.utcnow().isoformat() + "Z",
                "status": status,
                "vehicle_count": vehicle_count,
                "source": SOURCE_LISTING_URL,
            },
            f,
            indent=2,
        )


def main():
    vehicles = fetch_all_listings()
    validate_vehicles(vehicles)
    rebuild_inventory_page(vehicles)
    write_sync_log(len(vehicles), "success")
    print(f"Synced {len(vehicles)} vehicles successfully from {SOURCE_LISTING_URL}.")


if __name__ == "__main__":
    try:
        main()
    except SyncError as e:
        write_sync_log(0, f"failed: {e}")
        print(f"SYNC FAILED: {e}", file=sys.stderr)
        sys.exit(1)
