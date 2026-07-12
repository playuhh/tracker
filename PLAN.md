# HTTP Scraper Migration Plan

## Goal

Replace the daily Playwright and Chromium scrape with direct HTTP calls to the
public inventory endpoints used by Building A website. Preserve the existing
CSV history, static report, GitHub Pages deployment, and optional Google Sheets
export.

The browser remains a manual discovery and debugging tool only. It is not part
of the scheduled GitHub Actions path.

## Verified Site Contract

Building A page currently uses this public WordPress AJAX endpoint:

```text
POST https://verisresidential.com/wp-admin/admin-ajax.php
```

### Floor-plan overview

Send `action=omg_apt_search_main_query` with a JSON `payload` that includes:

- `index_table`: `omg_apt_idx`
- `environment`: `{"page_id":"private-page-id","custom_post_type":"property_id"}`
- `group_by`: `omg_feeds_floorplan_id`
- `results_per_page`: `999`
- a minimal result structure requesting `floorplan_name`,
  `omg_feeds_floorplan_id`, `rent_formatted`, `rent_from_price`,
  `move_in_date`, `date_formatted`, and `sqft_commas`

The verified response reports six floor plans and an `apt_count` of 17.

### Individual unit details

For each `omg_feeds_floorplan_id`, send:

```text
action=floorplan_query
id=<floorplan id>
```

The response's `query_response` contains individual unit records. The verified
S3 response includes `UNIT-0704` at `$3,757` and `UNIT-0804` at `$3,925`.
Relevant fields are `the_title`, `ra_rent`, `ra_date_available`,
`omg_feeds_floorplan_id`, and `omg_feeds_apartment_squarefootage`.

## Implementation Steps

1. Save sanitized overview and floor-plan-detail response fixtures under tests.
2. Replace Playwright scraping with standard-library HTTP requests in
   `scraper.py`.
3. Map overview rows to `unit_prices.csv` and detail rows to
   `unit_snapshots.csv` without changing their schemas.
4. Format past availability dates as `Immediate`; preserve future dates in the
   current report-friendly format.
5. Add bounded timeouts, a small retry policy, and failures for empty or
   inconsistent results. The overview's `apt_count` must match the number of
   collected detail rows.
6. Remove Playwright and Chromium installation from the GitHub Actions
   workflow. The scheduled job should need only Python's standard library.
7. Keep Google Sheets optional. It must remain disabled by `--no-sheets` and
   must not add a dependency to the cloud job.
8. Update README setup and troubleshooting notes to describe the direct HTTP
   collector and the browser-only debugging fallback.

## Validation

- Unit tests cover overview parsing, detail parsing, availability-date
  formatting, empty responses, count mismatches, and price-change reporting.
- A local dry run returns floor plans and individual units without Chromium.
- A manual GitHub Actions run writes non-empty `data/unit_snapshots.csv`,
  commits the history, and deploys the Pages report.
- Verify https://account.github.io/tracker/ shows the current unit table.

## Operational Guardrails

- Make one overview request followed by one detail request per floor plan.
- Use a descriptive User-Agent, short timeouts, and limited retries.
- Do not bypass authentication, bot challenges, or access controls.
- Treat these endpoints as an internal public contract: fixtures and validation
  should fail loudly if the site changes instead of committing an empty snapshot.
- If the contract changes, use Playwright network logging to rediscover the
  request before changing the daily collector.
