# Tax Lawsuit RE Lead Pipeline

End-to-end pipeline for tax suit leads: district clerk ingest → assessor match → skip trace → human review → CSV export.

Marketplace roadmap (buyer pipeline, matching, outreach): see [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md). Implement phases in order — do not jump ahead.

**Phase 1 geography:** Texas — Harris (48201) + Dallas (48113)

**Phase 2 probes (disabled, no adapters yet):** Tarrant (48439), Bexar (48029), Maricopa AZ (04013)

## Setup

```bash
cd re-tax-leads
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/secrets.env.example config/secrets.env
# Add BATCHDATA_API_KEY (and optionally ANTICAPTCHA_API_KEY for live Dallas clerk)
```

### Database

Defaults to a local SQLite file (`leads.db`) — nothing else to set up. For Postgres:

```bash
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

# Ingest + enrich Harris tax suits (last 7 days)
python3 -m leads run --county harris --since 7d

# Dallas: DCAD tax lookup is live; clerk ingest uses Odyssey fixtures and/or captcha
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

Anonymous Odyssey search requires reCAPTCHA. Until `ANTICAPTCHA_API_KEY` is set:

1. Open Smart Search on the Dallas Courts Portal
2. Search business name `DALLAS COUNTY TAX*` with a file-date range
3. Save the results HTML to `artifacts/raw/dallas/clerk/*.html`
4. Re-run `python3 -m leads run --county dallas`

## Phase 1 validation

```bash
python3 -m leads validate
```

Uses Dallas Odyssey HTML under `artifacts/raw/dallas/clerk/` when `ANTICAPTCHA_API_KEY` is unset. Harris is dry-run against the live bulk dataset. Skip-trace uses BatchData if `BATCHDATA_API_KEY` is set, otherwise the stub. Results go to `artifacts/phase1_validation.db` (not `leads.db`).

## Daily job

```bash
chmod +x scripts/run_daily.sh
./scripts/run_daily.sh
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```
