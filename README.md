# Apartment Price Tracker

Tracks advertised prices at Anonymous building and saves each run as
a CSV snapshot. The scheduled collector uses direct, public inventory requests;
it does not launch a browser or install Chromium.

The tracker records:

- floor plan, square footage, move-in date, and advertised starting rent
- individual unit number, rent, availability date, and floor-plan ID
- UTC snapshot timestamp

## Setup

The core tracker has no third-party Python dependencies.

```bash
python3 -m venv .venv
.venv/bin/python scraper.py --no-sheets
```

Each run updates three local files:

- `data/unit_prices.csv`: floor-plan price history
- `data/unit_snapshots.csv`: individual-unit history, including room numbers such as `UNIT-0704`
- `data/report.html`: a local dashboard with floor-plan sparklines, a filterable
  unit-level price-history chart, and a sortable current-unit table

On later runs, the terminal reports every floor-plan price increase or decrease
compared with the last saved price. Open the dashboard after a run:

```bash
open data/report.html
```

Useful flags:

```bash
# Verify the collector without writing CSV rows.
.venv/bin/python scraper.py --dry-run --no-sheets

# Regenerate the dashboard from saved history without collecting new data.
.venv/bin/python scraper.py --report-only
```

## Google Sheets (optional)

The collector works without Google credentials. To append each floor-plan
snapshot to a sheet, install the optional dependency, create a Google
service-account JSON file, share the target sheet with its service-account
email, then set:

```bash
.venv/bin/pip install gspread
export GOOGLE_SERVICE_ACCOUNT_FILE="/absolute/path/to/service-account.json"
export GOOGLE_SHEET_NAME="apt data"
.venv/bin/python scraper.py
```

Credentials are excluded from Git. Price history is committed by the remote
tracker so its trend data persists between runs.

## Remote daily tracking

GitHub Actions runs the standard-library collector every day at `13:00 UTC`
(9 AM during Toronto daylight time, 8 AM during standard time). It commits the
two CSV histories and regenerated report back to the repository, then deploys
the report to GitHub Pages.

Before the first remote run, push the repository changes to GitHub and enable
**Settings -> Pages -> Source: GitHub Actions** in the GitHub repository. The
report will then be published at:

```text
https://account.github.io/tracker/
```

You can run it immediately from GitHub: **Actions -> Apartment Price Tracker
-> Run workflow**. To change it from daily to weekly, edit the `cron` line in
`.github/workflows/scraper.yml`.

## Troubleshooting a site change

The collector sends one overview request and one detail request per floor plan,
with short timeouts and limited retries. It deliberately stops before writing a
snapshot if the site returns no floor plans, malformed records, or a unit count
that does not match the overview. This protects the historical data from empty
or partial runs.

If that happens, use a browser manually to inspect network activity on the
Building availability page. Find the public `admin-ajax.php` request with
`action=omg_apt_search_main_query`, then the `floorplan_query` detail requests.
Update the fixtures and parser only after confirming the new public response
shape. The browser is a discovery and debugging tool, not part of the scheduled
workflow.
