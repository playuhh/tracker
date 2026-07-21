# Project Context

This is a renter-oriented tracker of advertised apartment prices. Its public
dashboard is designed to help with timing and comparison decisions, not to
identify a particular building or unit.

## Non-negotiable privacy rules

- Publish stable pseudonyms only: **Building A**, **Layout 1**, and anonymous
  listing keys. Do not reintroduce a real building name, address, source-page
  ID, apartment number, or floor number into tracked files or the dashboard.
- Keep the live target only in `RENTAL_BUILDING_PAGE_ID` (a local environment
  variable or GitHub repository secret). Never commit its value.
- Treat an advertisement disappearing as **no longer advertised**, not proof
  that it was rented. Listings may be withdrawn, edited, or temporarily hidden.
- The private canonical catalog may map room number to exact floor and traits,
  but it must stay under ignored `private/`. Tracked files may contain only a
  keyed unit identifier and anonymous floor band; never publish the source map.
- Individual listing snapshots exist solely to derive aggregates. The public
  report must remain layout-first and must not expose a browsable unit list.

## What is measured

The collector records the public advertised rent, layout, square footage, and
available/move-in date for each visible listing. A completed collection is
validated before it is written, so a broken or partial source response does not
look like inventory disappearing.

| File | Role |
| --- | --- |
| `data/unit_prices.csv` | Pseudonymized layout price observations |
| `data/unit_snapshots.csv` | Pseudonymized listing observations used for calculations |
| `data/floorplan_daily.csv` | Daily layout aggregates: inventory, min/median/max rent, $/sq ft, new visibility, and reductions |
| `data/scrape_runs.csv` | Successful-run coverage used to separate missing data from absent listings |
| `data/unit_traits.csv` | Anonymous per-residence exposure, facade, pool-facing, sunlight, view, floor-band, and disturbance inputs |
| `data/report.html` | Public anonymous renter dashboard |

The current min / median / max values describe **all listings visible in the
latest complete snapshot for that layout**. They are not a history-wide
summary. Rent per square foot is the current median advertised rent divided by
that layout's recorded square footage.

## Decision interpretation

- Compare layouts using current inventory, median rent, and median $/sq ft;
  min/max indicate the currently advertised range rather than a guaranteed
  negotiable price.
- A 7- or 30-day median-rent change appears only after sufficient complete
  daily history. Until then, `Collecting history` is the correct signal.
- Inventory rising, new listings appearing, or observed price reductions can
  strengthen a renter's position; low inventory or earlier move-in dates can
  imply urgency. These are decision aids, not predictions of final lease price.
- Personalized fit uses a renter-specific preference for southeast exposure,
  pool-facing interior facade, and observed sunlight. Northwest exposure with
  mountain shade is penalized. Pool activity/noise remains a separate risk.
  These are personal-fit weights, not a universal market valuation model.
- Floor 5 is the minimum: below-floor-5 homes are excluded whenever a layout
  has an eligible current alternative. Floors 5–6 are acceptable but capped
  below `Best match`; floor 7+ is preferred.
- Never infer a Manhattan/skyline view from direction alone. The current model
  requires floor 7+ and appropriate facade geometry; resident confirmation is
  stronger evidence and must be labeled separately from modeled geometry.
- A flat chart is valid: the same advertised prices can persist across several
  snapshots.

## Dashboard conventions

- Layout labels and listing identifiers are intentionally generic.
- In a layout price chart, min, median, and max share one y-axis. This makes
  their vertical relationship comparable at each date.
- Hover each chart marker to see its date, series name, and exact value. The
  inventory chart uses the same behaviour for listing counts.
- The report calls only complete snapshot days usable history; it does not fill
  gaps with assumed values.
- The layout recommendation uses the best verified current option and publishes
  only aggregate counts and reasons. It never exposes which listing produced the
  score. Unknown traits remain explicitly unrated.

## Operating runbook

### Local

```bash
export RENTAL_BUILDING_PAGE_ID="private-page-id"
export UNIT_ID_HASH_KEY="private-random-key"
python3 -m unittest discover -q
python3 scraper.py --report-only
python3 scraper.py --dry-run --no-sheets
```

Use `--report-only` after dashboard-code changes to rebuild the committed HTML
from saved history. Use `--dry-run --no-sheets` to validate collection without
writing observations.

### GitHub Actions and Pages

- The workflow is scheduled and can be run manually; it does **not** deploy on
  every ordinary `git push`.
- After changing dashboard or collector code, manually run **Apartment Price
  Tracker** from GitHub Actions (or `gh workflow run scraper.yml`) to collect,
  regenerate, commit the data, and publish Pages.
- A successful workflow creates its own data commit. Before pushing more local
  work afterward, fetch/rebase on `origin/main` so that generated snapshot
  commit is preserved.
- Check the deployed dashboard at `https://playuhh.github.io/tracker/` after a
  successful workflow. Confirm privacy, chart scale, hover values, and history
  coverage rather than relying only on local HTML.

## Change checklist

1. Keep source identifiers and personal information out of tracked output.
2. Validate the 360-row private catalog and compile its keyed anonymous public
   counterpart before collecting inventory.
3. Run the test suite for code changes and regenerate `data/report.html` when
   report logic changes.
4. Review `git status`; do not accidentally add local files such as `.DS_Store`
   or private planning notes.
5. For public-facing changes, trigger the workflow and verify its GitHub Pages
   result.
