# IN-n-OUT Auto Sales — Website + Inventory Sync

## What's in here
- `index.html`, `inventory.html`, `about.html`, `contact.html`, `finance.html`,
  `vehicle-detail.html`, `thank-you.html` — the site itself
- `styles.css`, `script.js` — shared design + interactivity
- `scripts/sync_inventory.py` — reads inventory from the CURRENT live site
  (inandoutautosaleswa.com) and updates `inventory.html` automatically
- `.github/workflows/sync-inventory.yml` — runs the sync script every 4
  hours automatically (and lets you trigger it manually anytime)

## Why it reads from the current site instead of a feed

Carsforsale.com confirmed they can only send inventory feeds to
recognized third-party marketplaces (CarGurus, AutoTrader, etc.) — not
to an independent custom website. So instead of a feed, the sync script
reads the vehicle listings straight from the dealer's own current public
website, which is publicly visible information about their own
inventory. When a vehicle is added/removed/updated on the current site,
this script picks up the change on its next run.

**Tradeoff to know:** if the current site's page template ever changes
(e.g. Carsforsale updates their design), the script's parsing logic may
need a small update. It's written to fail safely and loudly if that
happens — see "If something breaks" below.

## One-time setup

1. **Create a free GitHub account** at github.com (if you don't have one).
2. **Create a new repository** and upload everything in this folder to it.
3. **Turn on failure email alerts** (so you know if a sync ever breaks):
   - GitHub.com → your profile photo → Settings → Notifications →
     under "Actions," make sure email notifications are on.
4. **Connect the repo to Netlify:**
   - Netlify → "Add new site" → "Import from Git" → pick this repo
   - Netlify will auto-deploy every time the sync script updates the site.
5. **Point your domain at Netlify** (see earlier steps we walked through).

No secrets or API keys needed for the sync itself — it reads a public
page, the same way a browser would.

## If something breaks down the line

- Check the "Actions" tab on GitHub — it shows every sync run and whether
  it succeeded or failed, with a full error message.
- `scripts/last-sync.json` also tracks the last successful/failed run
  and how many vehicles were found.
- The script is written to fail loudly and safely — if the page looks
  broken or returns zero vehicles, it will NOT publish a broken/empty
  inventory page. It leaves the last good version up and alerts you
  instead.
- Most likely failure mode: the current site's template changes and the
  parsing patterns in `scripts/sync_inventory.py` need a small update.
  Send me the error message from the Actions tab and I can fix it.
