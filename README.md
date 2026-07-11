# Apartment Price Tracker

Tracks advertised prices at Anonymous building and saves every run as a CSV snapshot. The scraper expands the availability list, then opens each floor plan to collect individual unit availability.

The tracker currently records:

- floor plan
- square footage, when the listing exposes it
- move-in date
- advertised starting rent
- individual unit number, rent, and available date
- UTC snapshot timestamp

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH=0 .venv/bin/playwright install chromium
```

Run a first local snapshot:

```bash
PLAYWRIGHT_BROWSERS_PATH=0 .venv/bin/python scraper.py --no-sheets
```

Each run updates three local files:

- `data/unit_prices.csv`: floor-plan price history
- `data/unit_snapshots.csv`: individual-unit history, including room numbers such as `UNIT-0704`
- `data/report.html`: a local dashboard with floor-plan sparklines and the current unit table

On later runs, the terminal reports every floor-plan price increase or decrease compared with the last saved price.

Open the dashboard after a run:

```bash
open data/report.html
```

Useful flags:

```bash
# Verify the scrape without writing a CSV row.
PLAYWRIGHT_BROWSERS_PATH=0 .venv/bin/python scraper.py --dry-run --no-sheets

# Open the browser while diagnosing a page change.
PLAYWRIGHT_BROWSERS_PATH=0 .venv/bin/python scraper.py --headed --no-sheets

# Regenerate the dashboard from the saved history without scraping.
.venv/bin/python scraper.py --report-only
```

## Google Sheets (optional)

The tracker works without Google credentials. To append each snapshot to a sheet, create a Google service-account JSON file, share the target sheet with its service-account email, then set:

```bash
export GOOGLE_SERVICE_ACCOUNT_FILE="/absolute/path/to/service-account.json"
export GOOGLE_SHEET_NAME="apt data"
PLAYWRIGHT_BROWSERS_PATH=0 .venv/bin/python scraper.py
```

Credentials are excluded from Git. Price history is committed by the remote tracker so its trend data persists between runs.

## Remote daily tracking

GitHub Actions runs the scraper remotely every day at `13:00 UTC` (9 AM during Toronto daylight time, 8 AM during standard time). The workflow commits the two CSV histories and regenerated report back to the repository, so the history survives even when your computer is asleep.

Before the first remote run, push the repository changes to GitHub and enable **Settings -> Pages -> Source: GitHub Actions** in the GitHub repository. The report will then be published at:

```text
https://account.github.io/tracker/
```

You can run it immediately from GitHub: **Actions -> Apartment Price Tracker -> Run workflow**. To change it from daily to weekly, edit the `cron` line in `.github/workflows/scraper.yml`.
