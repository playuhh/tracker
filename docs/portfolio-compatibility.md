# Portfolio compatibility and compliance notes

Last reviewed: 2026-07-21. Scope: one ordinary public-page read for each of the
three requested research properties, one unsuccessful attempt to read the
domain robots file, and one read of the site's linked terms. No availability
endpoint was called, no source/page ID was guessed or enumerated, no raw response
was saved, and no history or portfolio report was written.

## Evidence matrix

| Property | Location | Probe status | Adapter/schema | Market capability | Trait capability | Evidence | Request count | Blocker |
| --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| RiverHouse 9 | Weehawken | `not_tested` | proposed `veris_wp_ajax_v1` | not enabled | unavailable (`market_only`) | Public page displayed Availability, floor-plan selection, sqft, move-in and rent labels | 1 page | robots unknown; private source config absent; overview/detail not tested |
| RiverTrace | West New York | `not_tested` | proposed `veris_wp_ajax_v1` | not enabled | unavailable (`market_only`) | Public page displayed Availability, floor-plan selection, sqft, move-in and rent labels | 1 page | robots unknown; private source config absent; overview/detail not tested |
| Soho Lofts | Jersey City | `not_tested` | proposed `veris_wp_ajax_v1` | not enabled | unavailable (`market_only`) | Public page displayed Availability, floor-plan selection, sqft, move-in and rent labels | 1 page | robots unknown; private source config absent; overview/detail not tested |

The shared robots read was unsuccessful and is recorded as `unknown`, not as
permission. The public pages prove only that an Availability UI exists. They do
not prove the overview/detail JSON shapes, required fields, counts, absence of
authentication/challenges, or compatibility with the existing adapter.

The linked Veris terms say site materials may be retrieved for personal use and
otherwise restrict copying, modification, and distribution without permission.
Accordingly, this project's portfolio output remains local/private. The existing
anonymous Building A report is the only Pages artifact, and the explicit public
portfolio permission gate remains off.

## Safe adapter path

1. Create `private/property-registry.json` from the tracked placeholder example;
   keep real names, URLs, locations, source IDs and catalogs only in that ignored
   file.
2. Complete and record the robots/terms/owner compliance decision. If robots
   remains unknown, leave the property disabled and use manual research only.
3. If approved, obtain the source configuration only from the property's normal
   public page/network behavior. Never guess or enumerate it.
4. Run one sequential `--probe-properties --no-write` research pass within the
   shared domain budget. Stop the entire domain on 401/403/429 or a challenge.
5. Save only the smallest sanitized fixture for a genuinely distinct response
   shape. Validate overview, detail, required market fields and exact count
   agreement before changing a status to compatible.
6. Keep the property `market_only`. Promotion to `trait_enriched` is a separate
   evidence/catalog project and cannot borrow another property's geometry.

The collector uses atomic portfolio persistence in this phase: all enabled
properties must validate before any CSV append. This intentionally avoids the
more complex partial-coverage model and prevents failures from appearing as
inventory disappearance.

Implementation boundaries are explicit: `portfolio.py` owns registry,
capabilities, normalized models and the shared domain governor;
`provider_veris.py` owns versioned overview/detail parsing into normalized
floor-plan/listing objects; `scraper.py` owns orchestration, anonymization,
atomic CSV persistence and aggregate generation; and `report.py` reads only the
normalized anonymous CSV contract and capability-safe recommendation inputs.
