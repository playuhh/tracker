# Scoped private market collection

Some portfolio pages display multiple buildings under the same availability
widget. Keep the target's real name, page ID, URL and verified floor-plan
prefixes exclusively in the ignored private registry. Use the existing example
as a template; never commit real values.

An optional `source.floorplan_prefixes` list matches complete leading tokens
in the verified floor-plan name (case-insensitive). For example, the fictional
prefixes `TowerN` and `TowerS` include `TowerN A1` and `TowerS A1`, but not
`TowerOther A1`. This is a proposed scope mechanism, not a verified live
contract. All overview/detail records and the global count must validate before
filtering, so mixed inventory cannot silently become an incomplete snapshot.
A zero-match scope fails closed. The existing request budget is unchanged;
if full mixed inventory exceeds it, stop and verify a provider-supported
server-side scope and its count contract before enabling collection.

Complete the existing compatibility/compliance review first, obtain a verified
page ID through ordinary public page behavior, confirm the actual overview and
detail schema and scope, and set the private market-only entry to enabled
and approved_private only after that review. Run:

```bash
export RENTAL_PROPERTY_REGISTRY="$PWD/private/property-registry.json"
export UNIT_ID_HASH_KEY="<existing private key>"
python private_market.py property-b --dry-run
python private_market.py property-b
```

Output stays under `private/market/<opaque-key>/`, including a separate HTML
report and all history CSVs. Existing public data and Pages scheduling are
unchanged. A private scheduler can invoke this command once daily after a
successful live dry run; this change does not enable such a scheduler.
