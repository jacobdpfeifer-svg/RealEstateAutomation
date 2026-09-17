# Tax Lawsuit RE Lead Pipeline

End-to-end pipeline for tax suit leads: district clerk ingest → assessor match → skip trace → human review → CSV export.

Marketplace roadmap (buyer pipeline, matching, outreach): see [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md). Implement phases in order — do not jump ahead.

**Phase 1 geography:** Texas — Harris (48201) + Dallas (48113)

**Phase 2 probes (disabled, no adapters yet):** Tarrant (48439), Bexar (48029), Maricopa AZ (04013)

## Setup

```bash
cd re-tax-leads
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/secrets.env.example config/secrets.env
# Add BATCHDATA_API_KEY. Dallas clerk defaults to saved HTML.
```

### Database

Defaults to a local SQLite file (`leads.db`) when `DATABASE_URL` is unset. This
machine's production store is Homebrew PostgreSQL 16 / `re_tax_leads`: with
`DATABASE_URL` set, the default CLI path and `scripts/run_daily.sh` use Postgres.
Any explicit `--db` scratch path stays SQLite.

Shells (and Cursor agent terminals) that omit Homebrew will miss `brew`/`psql`:

```bash
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/postgresql@16/bin:$PATH"
brew install postgresql@16
brew services start postgresql@16
createdb re_tax_leads
# In config/secrets.env:
# DATABASE_URL=postgresql://localhost:5432/re_tax_leads
```

## CLI

```bash
# Bootstrap county probes (Harris/Dallas plus Tarrant, Bexar, Maricopa)
python3 scripts/bootstrap_county.py --county all
python3 scripts/bootstrap_county.py --county next

# Phase 1 validation (Harris dry-run + Dallas fixture ingest)
python3 -m leads validate -o artifacts/phase1_validation.json

# Ingest Harris tax suits and attempt at most 5 enrichments
python3 -m leads run --county harris --since 7d --max-enrich 5

# Dallas: DCAD tax lookup is live; clerk ingest defaults to saved Odyssey HTML
python3 -m leads run --county dallas --since 30d

# Dry run (no DB writes)
python3 -m leads run --county harris --since 7d --dry-run

# Pipeline status
python3 -m leads state

# Review queue
python3 -m leads review list --status pending
python3 -m leads review approve 1 --note "verified owner"
python3 -m leads review paste 2 --name "Jane Doe" --phone "7135550100"

# Export approved leads for dialer/CRM
python3 -m leads export --status approved -o exports/approved.csv
```

## Architecture

- **Harris Stage 1:** Bulk civil case summaries from [District Clerk Public Datasets](https://www.hcdistrictclerk.com/common/e-services/PublicDatasets.aspx) (form POST, not interactive scrape)
- **Harris Stage 2:** HCAD ArcGIS REST owner search
- **Dallas Stage 1:** Tyler Odyssey Smart Search (`courtsportal.dallascounty.org`) — reCAPTCHA for anonymous use, or save result HTML under `artifacts/raw/dallas/clerk/`
- **Dallas Stage 2:** DCAD ASP.NET owner search (`searchowner.aspx`)
- **Skip trace:** BatchData v3 (`BATCHDATA_API_KEY`); falls back to stub if the key is missing
- **Phase 2:** Tarrant/Bexar/Maricopa are in `config/counties.toml` as `enabled = false`. Probe them with `python3 scripts/bootstrap_county.py --county next` before writing adapters.

## Dallas clerk fixtures

Saved HTML is the default clerk input. For permitted manual portal use:

1. Open Smart Search on the Dallas Courts Portal
2. Search business name `DALLAS COUNTY TAX*` with a file-date range
3. Save the results HTML to `artifacts/raw/dallas/clerk/*.html`
4. Re-run `python3 -m leads run --county dallas`

## Phase 1 validation

```bash
python3 -m leads validate
```

Uses Dallas Odyssey HTML under `artifacts/raw/dallas/clerk/` by default. Harris is dry-run against the live bulk dataset. Skip-trace uses BatchData if `BATCHDATA_API_KEY` is set, otherwise the stub. Results go to `artifacts/phase1_validation.db` (not `leads.db`).

Last run (2026-09-15, no API keys): Harris live bulk dry-run found 126 tax suits in 60d; Dallas fixture ingest wrote 3 cases; DCAD enrich populated `property_type` / `total_value` and queued all 3 for review (stub skip-trace, no contacts). Set `BATCHDATA_API_KEY` before treating this as daily-ready.

## Daily job

The script runs enabled counties with `--max-enrich 5 --append-only`: the cap is
shared across counties, and existing leads are not resumed. Run it only after
completing the [operator handoff](docs/OPERATOR_COMPLETION_PROMPT.md). An attempt
cap is not a dollar ceiling; paid use requires a separately approved spending
ceiling and verified vendor costs/limits. This repository does not enforce a
cross-run dollar budget. No scheduler is installed by this script.

```bash
chmod +x scripts/run_daily.sh
./scripts/run_daily.sh
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Reliability and operations

Research, rationale, and deferred decisions: [2026 reliability review](docs/RELIABILITY_REVIEW.md).

- Use Python 3.12+ with OpenSSL for daily runs. The Python 3.9 pins preserve existing
  offline test compatibility; system Python on this Mac uses unsupported LibreSSL.
- `run` / `pipeline enrich-pending` return exit status 1 on processing failures.
  `run` commits ingest and each completed lead so the next invocation can resume.
  Local pipeline commands share a nonblocking file lock; the kernel releases it
  after a crash. Postgres sessions additionally coordinate through a database
  advisory lock across updated checkouts/hosts (details below).
- Errors retry on later runs, up to `LEADS_MAX_ENRICH_ATTEMPTS` (default 3), then
  enter `dead_letter`. No owner/contact results go to human review with a reason.
- `--dry-run` uses an in-memory DB and does not open the target DB. It still fetches
  source data; explicitly enabled raw capture can still write files.
- JSON operational events go to stderr (time, county, stage, run ID for county
  events, counts, duration, safe error type). Stdout keeps command results.
  Configure cron's exit/absence alerts and rotate redirected logs on the host.
- GET retries honor `Retry-After`; 401/403 and exhausted 429 stop further requests
  to that host for the process. POST requests are never replayed by the HTTP layer.
  A later enrichment retry can still repeat a paid call after an ambiguous timeout;
  check vendor billing before explicitly requeueing it.

```bash
python3 -m leads review list --status dead_letter
python3 -m leads review retry 123 --note "source repaired; checked provider billing"
python3 -m leads pipeline enrich-pending --county harris
```

Dallas County offers an [official civil index subscription](https://www.dallascounty.org/dcSubServicePaymentus/),
and DCAD publishes [bulk appraisal files](https://www.dallascad.org/DataProducts.aspx).
These are the preferred next integrations. Legacy portal automation requires
`DALLAS_CLERK_LIVE_ENABLED=1` plus a token/provider key and confirmation that the
county permits the workflow. A working CAPTCHA service is not that confirmation.

Raw Harris capture is off unless `LEADS_SAVE_RAW=1`. New raw captures and CSV file
exports use owner-only permissions. Existing data is not deleted or moved.
`leads.db`, SQLite sidecars, raw artifacts, validation databases, and exports are
Git-ignored; Git does not control iCloud sync, backups, or previously committed
fixture data. Choose a retention policy and storage location before sharing data.

### Bounded scratch rehearsal

`--max-enrich N` caps enrichment attempts across all counties in one command,
including failures. Zero ingests without enrichment; omitted means unbounded.
Deferred rows remain pending for a later command. Each lead calls skip trace at
most once per attempt. A new invocation has a new cap; include reruns in your
spending ceiling. `validate` defaults to a cap of 5.

For a production run that must preserve every existing row, add `run --append-only`.
Duplicate cases retain their stored fields, and enrichment/scoring only touch
cases inserted by that invocation. Existing pending/error leads stay untouched;
even new leads deferred by the cap require a separately authorized normal resume
command later. This flag does not change the configured database or enrichment cap.

```bash
DALLAS_CLERK_LIVE_ENABLED=0 LEADS_SAVE_RAW=0 .venv/bin/python3 -m leads run --county harris --since 7d --max-enrich 5 --db artifacts/runtime/test_leads.db
.venv/bin/python3 -m leads state --db artifacts/runtime/test_leads.db
```

`--db` works before or after subcommands. Explicit scratch paths override
`DATABASE_URL`. The daily script caps attempts at five and uses append-only mode;
it has no scratch override and is a production entry point. Do not schedule it
before completing the operator handoff and accepting production readiness.

### Database coordination and private review summaries

Postgres application sessions acquire a database advisory lock before migrations
or work. This coordinates updated checkouts/hosts sharing the same database and
holds through incremental commits; connection close releases it. A competing
command exits 1 with a retry message. All `db_session` callers participate,
including state/review/export commands because they currently initialize schema.
Older versions, external SQL clients, and code using bare `connect()` do not
participate: identify and coordinate those writers before scheduling. SQLite
pipeline runs retain their local file lock.

For routine reports without owner names, addresses, contacts or free-form notes:

```bash
.venv/bin/python3 -m leads review summary --status pending
.venv/bin/python3 -m leads review summary --status new
.venv/bin/python3 -m leads review summary --status dead_letter
```

`summary` aggregates by county/status and known reason categories inside the
database. Reasons can overlap; a zero reason count does not certify a lead.
`review list` still exposes detailed records for authorized local human review.
