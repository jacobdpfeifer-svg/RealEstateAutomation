# How to use

Paste everything between **BEGIN AGENT PROMPT** and **END AGENT PROMPT** into a new
agent chat in this repo once Phase 1 (Harris/Dallas) is accepted per
`artifacts/runtime/READY_FOR_REAL_RUNS.md`. Do not skip the discovery spike for
Tarrant. Do not enable live portal automation for any of these three counties
without operator-confirmed permission — a missing CAPTCHA is not permission,
same rule as Dallas.

Phase 3 discovery already **stopped** two clerk paths; do not treat them as
open TODOs: Maricopa does not produce county-plaintiff tax suits (see
`artifacts/runtime/PHASE3_MARICOPA_MODEL.md`), and Tarrant PublicAccess is
County Clerk probate/JP/CCL, not District Civil Tax (see
`artifacts/runtime/TARRANT_SPIKE.md`).

---

# BEGIN AGENT PROMPT

You are extending `re-tax-leads` from two counties (Harris, Dallas) to include
Bexar, Maricopa, and — conditionally — Tarrant. This is Phase 3 of
`docs/PROJECT_PLAN.md` §7 ("Bootstrap 2–3 new metro counties via the probe
pattern"). Do not jump ahead to Phase 4+ (buyer/recorder adapters, matching,
outreach) — that decision was already made and stands.

## Mission

1. Re-verify the fresh probe evidence in `artifacts/phase2_probe.json`
   (produced 2026-09-17 by `scripts/bootstrap_county.py --county next`) is
   still current — sites change. Re-run the probe if it's more than a couple
   weeks old by the time you start.
2. Build tax (assessor) adapters for all three counties — this is low-risk,
   keyless, reuses existing code, and should be done first regardless of what
   happens with the clerk side.
3. Build clerk (court) adapters for Bexar and Maricopa, following the
   bulk > api > portal_scrape ladder already proven in this repo. Do not write
   a portal scraper until you've confirmed there's no bulk/API tier — the
   probe already did this for you; don't re-litigate it without new evidence.
4. Run a discovery spike on Tarrant's clerk portal *before* writing any
   adapter code for it — its shape is genuinely unconfirmed (see Phase C3).
5. Promote each county in `config/counties.toml` only after its adapters pass
   real (non-mocked) tests, one county at a time, with an audit per county.
6. Stop before flipping any of these into the daily-run rotation. That's an
   operator decision, same as Phase 1.

## What the probe already told us (`artifacts/phase2_probe.json`, `config/counties.toml`)

| County | Tax (assessor) | Clerk (court) |
|---|---|---|
| **Bexar** | Live ArcGIS REST (`maps.bexar.org/.../Parcels/MapServer/0`) — `Owner`, `PropID`, `TotVal`, **`State_cd`** (only one of the three with a property-type field) | Tyler Odyssey **Smart Search** (`portal-txbexar.tylertech.cloud/Portal/`) — same platform family as Dallas. Landing page: *"Registration is not required for public data. Registration is reserved for authorized law enforcement and justice partners."* Probe found `recaptcha_required: false` on the landing page (unconfirmed on an actual submitted search — verify before relying on it). |
| **Maricopa** | Live ArcGIS REST (`gis.mcassessor.maricopa.gov/.../Parcels/MapServer/0`) — `OWNER_NAME`, `APN`, `FCV_CUR`. The separate assessor JSON search (`mcassessor.maricopa.gov/search/property/`) returns HTTP 500 — don't build against it; use ArcGIS. | Plain ASP.NET form (`superiorcourt.maricopa.gov/docket/civilcourtcases/casesearch.asp`) with a **"Search by Business Name"** field visible on the page — this is the plaintiff-search entry point, structurally simpler than Odyssey (no JS-launched modal). No bulk dataset found; AZ bulk civil records require a formal Rule 123 request (operator task, not something to build around yet). |
| **Tarrant** | Live ArcGIS REST (`mapit.tarrantcounty.com/.../TADParcels/FeatureServer/0`) — `OWNER_NAME`, `ACCOUNT`, `LAND_VALUE` | Tyler Odyssey **PublicAccess** (`odyssey.tarrantcounty.com/PublicAccess`) — the **older** Odyssey UI, not Smart Search. "Case Records Search" is a JS-launched modal (`LaunchSearch('Search.aspx?ID=200', ...)`), not a plain linkable page. No CAPTCHA or login wall was hit reaching this point, but **whether it supports a business/plaintiff-name search at all is unconfirmed** — the modal wasn't actually exercised. An optional paid "Draw Down Account" exists for document images; it is not required for case search and needs no advance signup. |

None of the three has a public bulk civil-case dataset (no Harris-style
`PublicDatasets.aspx` equivalent) — all clerk sides land on `portal_scrape`,
same tier as Dallas.

## Hard rules (same spirit as `docs/MAKE_IT_RUN_PROMPT.md`)

- Never use production `leads.db` or the production Postgres database for
  these builds or tests. Use a scratch SQLite file under `artifacts/runtime/`
  or a throwaway Postgres database you create and drop yourself
  (`createdb re_tax_leads_<scratch-name>` ... `dropdb` when done) — never the
  `re_tax_leads` database `DATABASE_URL` already points at.
- Do not solve, bypass, or pay for CAPTCHA solving for any of these three
  counties. A missing CAPTCHA on a landing page is not proof one won't appear
  on a real search or in reCAPTCHA v3's invisible scoring — verify by hand
  first (see Phase C1). If a CAPTCHA appears, default to the same
  saved-HTML-fixture pattern used for Dallas, gated behind an explicit
  `*_CLERK_LIVE_ENABLED` env var that defaults to `0`.
- Do not treat "registration not required" (Bexar) or "no login wall found"
  (Tarrant) as legal permission to automate the portal at volume. Confirm
  each county's terms of use / robots.txt, and prefer low-frequency,
  clearly-identified requests (reuse `SourceSession`'s existing per-host
  pacing — do not add a second, faster HTTP path for these counties).
  Flag anything ambiguous as an operator decision rather than assuming yes.
- Cap any live enrichment the same way Phase 1 does: `--max-enrich 5` or less
  per test, since skip-trace is billed via BatchData once a key is present.
- Do not flip `enabled = true` in `config/counties.toml` for a county whose
  clerk adapter hasn't passed at least one real (non-fixture-mocked) test.
  Bexar/Maricopa can be tested live directly (no fixture needed if the
  portal turns out to be open); Tarrant needs its spike to pass first.
- Do not implement Phase 4+ (`adapters/*_recorder.py`, `leads/buyers.py`,
  `leads/matching.py`, `leads/outreach.py`) as part of this work, even if it
  looks like a natural next step. That's explicitly out of scope per
  `PROJECT_PLAN.md` §7 and a standing decision, not an oversight.
- Research, don't assume, each state's plaintiff-name convention for tax
  suits before wiring `plaintiff_terms`. Harris/Dallas both file as
  `"<COUNTY> COUNTY (TAX ASSESSOR-COLLECTOR)"` — verify Bexar and Tarrant
  follow the same Texas convention (likely, but confirm against a handful of
  real case captions), and **do not assume Arizona uses the same pattern**.
  Maricopa tax-lien suits may be filed under a different party name (e.g. a
  trustee, the county treasurer, or a certificate-holder structure specific
  to Arizona's tax-lien-sale system, which works differently from Texas's
  judicial tax-suit process). Pull 3–5 real Maricopa case captions from the
  business-name search before hardcoding `plaintiff_terms` — Arizona's
  underlying tax collection mechanism is not guaranteed to produce the same
  kind of judicial "suit" Harris/Dallas ingest at all. If it doesn't, say so
  plainly rather than forcing Maricopa into the existing model — an
  ArcGIS-only "distressed property" signal (e.g. delinquent-status flags on
  the parcel layer, if the layer exposes one) might be the right adapter
  shape instead of a clerk suit adapter. Check the full field list already
  captured in `artifacts/phase2_probe.json` for Maricopa's `tax_candidates`
  before assuming a court-suit model applies.

## Phase A — Re-verify and read

```bash
cd re-tax-leads
source .venv/bin/activate
export PYTHONPATH="$PWD"
python3 scripts/bootstrap_county.py --county next -o artifacts/phase2_probe.json
```

Read before writing code:
- `artifacts/phase2_probe.json` (fresh)
- `config/counties.toml` — Bexar/Maricopa/Tarrant entries already exist with
  `enabled = false` and the URLs/fields above pre-filled from the original
  2026-09-15 probe
- `adapters/base.py` (the `ClerkAdapter`/`TaxAdapter` Protocol contract)
- `adapters/platforms/arcgis_owner.py` and `adapters/tx/harris_tax.py`
  (the reusable ArcGIS tax pattern — copy this shape, don't rewrite it)
- `adapters/platforms/dallas_odyssey.py` and `adapters/tx/dallas_clerk.py`
  (the Smart Search Odyssey pattern — Bexar's likely starting point)
- `adapters/tx/dallas_tax.py` and `adapters/platforms/dcad_owner.py` (a second
  example of the thin-adapter-over-platform-client pattern, for contrast)
- `tests/test_adapter_contracts.py` (what "passing" looks like for an adapter)
- `docs/RELIABILITY_REVIEW.md` §A "HTTP access, retries, and source changes"
  (the pacing/retry/POST-safety rules every new adapter must inherit via
  `SourceSession` — don't hand-roll a second HTTP client)

## Phase B — Tax adapters (do this first; all three, low risk)

For each county, write a thin adapter in `adapters/tx/<county>_tax.py` (or
`adapters/az/maricopa_tax.py` — create the `adapters/az/` package) mirroring
`HarrisTaxAdapter` exactly: wrap `ArcGISOwnerSearch` from
`adapters/platforms/arcgis_owner.py`, passing the county's `arcgis_url`,
`owner_field`, `apn_field` (already in `counties.toml`) plus `value_field`
and, for Bexar, `type_field="State_cd"` if `ArcGISConfig` doesn't already
support it — check the dataclass in `arcgis_owner.py` before adding a field;
extend it once, generically, if it doesn't have `value_field`/`type_field`
yet (Harris's adapter doesn't request them today — see
`docs/RELIABILITY_REVIEW.md`'s "HCAD requested fields omit type/value" note
under Remaining limitations. This is a good moment to close that gap for
all counties at once rather than patching it three separate times later).

Write offline contract tests in `tests/test_adapter_contracts.py` using a
recorded/synthetic ArcGIS JSON response shaped like the real
`?f=json` metadata already captured in `artifacts/phase2_probe.json` (the
`fields` list per county is already there — use it, don't guess field names).

Then run one live, read-only smoke test per county (owner-name search against
the real ArcGIS endpoint, no DB write) before moving to Phase C. Record
results in `artifacts/runtime/PHASE3_TAX_SMOKE.md` (command, county, one
sample match, exit code).

## Phase C — Clerk adapters

### C1. Bexar (try first — most code reuse, no confirmed CAPTCHA)

1. Manually open `https://portal-txbexar.tylertech.cloud/Portal/` in a
   browser, use Smart Search for business name `BEXAR COUNTY TAX*` (or
   whatever the confirmed real plaintiff term turns out to be), and observe:
   does a CAPTCHA appear on an actual search submission (not just the landing
   page)? Save the rendered results HTML the same way Dallas's fixtures were
   captured (`artifacts/runtime/DALLAS_CAPTURE.md` is the template — write a matching
   `BEXAR_CAPTURE.md` if you have to repeat this by hand later).
2. If the HTML shape matches Dallas's Smart Search output, adapt
   `DallasOdysseyClient` to accept a `portal_base` and plaintiff terms as
   parameters (it may already do this — check before assuming a rewrite is
   needed) and write `BexarClerkAdapter` in `adapters/tx/bexar_clerk.py`
   following `DallasClerkAdapter`'s exact structure: fixture-first, live only
   behind `BEXAR_CLERK_LIVE_ENABLED=0` by default.
3. If the HTML shape differs meaningfully, note exactly how before writing a
   new platform module — don't force-fit a working parser onto slightly
   different markup and call it done; a silent partial parse is worse than a
   visible failure (same principle `RELIABILITY_REVIEW.md` applied to Dallas).

### C2. Maricopa (second — simpler HTML, but verify the legal model first)

1. Resolve the plaintiff-name question from the Hard Rules section above
   *before* writing the parser. Pull a handful of real "Search by Business
   Name" results for whatever the correct Arizona tax-collection party name
   turns out to be, using the visible form at
   `https://www.superiorcourt.maricopa.gov/docket/civilcourtcases/casesearch.asp`.
2. Write `adapters/platforms/maricopa_docket.py` (new — this HTML shape has
   no existing counterpart in this repo) parsing the results table, then a
   thin `adapters/az/maricopa_clerk.py` wrapper, same fixture-first/live-flag
   pattern as Dallas/Bexar (`MARICOPA_CLERK_LIVE_ENABLED=0` default).
3. If Phase C2.1 concludes Arizona's mechanism doesn't produce a matching
   "tax suit" the existing pipeline model expects, stop and write up the
   actual mechanism you found instead of forcing it — this is exactly the
   kind of judgment call that should reach the operator, not get silently
   worked around.

### C3. Tarrant — discovery spike required before any adapter code

Tarrant is not ready for adapter-writing yet. Do this first, as its own
dated file `artifacts/runtime/TARRANT_SPIKE.md`:

1. Manually trigger the `Search.aspx?ID=200` modal from
   `https://odyssey.tarrantcounty.com/PublicAccess/default.aspx` → "Case
   Records Search" and determine: does it support a business/plaintiff-name
   search, or only party name / case number / date range? Screenshot or save
   the actual search form.
2. If business-name search exists, run one for the correct Tarrant plaintiff
   term (verify the term the same way as Bexar — don't assume it matches
   Dallas's convention without checking a real caption) and capture what the
   results list and a case-detail page look like.
3. Only after 1–2 confirm the workflow is structurally viable, decide whether
   to reuse the same older-Odyssey shape for a future county too (worth
   checking if Tarrant's `Search.aspx?ID=200` pattern is common to other
   PublicAccess-flavor Odyssey counties, which would make this spike pay off
   more than once) and write `adapters/platforms/tarrant_odyssey.py` +
   `adapters/tx/tarrant_clerk.py` following the same fixture-first pattern.
4. If business-name/plaintiff search isn't available at all in this UI,
   stop and report that as a finding, not a failure — note whatever search
   mode *is* available (party name, case number) and flag to the operator
   that Tarrant's ingest strategy may need to be different (e.g., date-range
   + case-type browse instead of plaintiff search) before deciding whether
   to proceed.

## Phase D — Promote in `config/counties.toml`

Only after a county's clerk adapter has a passing real test (Phase E), flip
its `enabled = true`. Do this one county at a time, not as a batch, so each
promotion has its own clean audit trail. Leave Tarrant `enabled = false`
until its spike (C3) and adapter both land.

## Phase E — Per-county real-world test (mirrors `docs/MAKE_IT_RUN_PROMPT.md` Phase D, scaled down)

For each newly-enabled county, on a scratch DB (never production):

```bash
SCRATCH="artifacts/runtime/test_leads.db"   # or a scratch Postgres DB you create/drop
python3 -m leads run --county <county> --allow-disabled --since 30d --max-enrich 5 --db "$SCRATCH"
python3 -m leads state --db "$SCRATCH"
python3 -m leads review list --status pending --db "$SCRATCH"
```

Write `artifacts/runtime/PHASE3_<COUNTY>_AUDIT.md` using the same template
as `TEST_N_AUDIT.md` from Phase 1 (what ran, real systems hit, evidence,
what went well, what must change, verdict). Do not mark a county
"daily-ready" while its plaintiff-term research (Hard Rules) is still
unconfirmed.

## Phase F — Stop before scheduling

After all in-scope counties (Bexar, Maricopa, and Tarrant if its spike
passed) have a passing audit, update
`artifacts/runtime/READY_FOR_REAL_RUNS.md` with a new section for Phase 3
counties rather than overwriting the Phase 1 findings. Do not add any new
county to `scripts/run_daily.sh`'s `--all-enabled` rotation without the
operator explicitly accepting the updated readiness report — `--all-enabled`
already means every `enabled = true` county runs unattended, so flipping
`enabled = true` in Phase D is itself the consequential step; treat it with
the same care as the original Phase 1 acceptance gate.

## Done looks like

- `adapters/tx/bexar_tax.py`, `adapters/tx/tarrant_tax.py`,
  `adapters/az/maricopa_tax.py` (or your chosen module layout) — all reusing
  `arcgis_owner.py`
- `adapters/tx/bexar_clerk.py`, `adapters/az/maricopa_clerk.py`, and — only
  if the spike passes — `adapters/tx/tarrant_clerk.py`
- `artifacts/runtime/TARRANT_SPIKE.md`, `PHASE3_TAX_SMOKE.md`,
  `PHASE3_<COUNTY>_AUDIT.md` per promoted county
- `config/counties.toml` updated one county at a time, each with a matching
  audit
- Updated `READY_FOR_REAL_RUNS.md`
- Production `leads.db` and production Postgres untouched; no county added
  to the daily rotation without operator acceptance; no CAPTCHA solved; no
  Phase 4+ buyer/marketplace code written

# END AGENT PROMPT
