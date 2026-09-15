# Build Prompt: Tax-Lead-to-Buyer Data Marketplace

Use this document as the working spec for extending `re-tax-leads`. Implement in the phase order given in §7 — do not jump ahead to later phases before earlier ones are working and tested.

## 0. Mission

Turn `re-tax-leads` from a single-user CSV export tool into a two-sided data marketplace: distressed-property seller leads matched against verified cash-buyer profiles, sold as data/matches — never as a brokered deal. The system automates research, matching, and drafting of outreach as far as legally and technically sound; a human always approves before anything sends and before any contract term is agreed.

## 1. Product shape

Two parallel pipelines feeding one matching layer:

- **Seller pipeline (exists, extend):** court/tax filings → distressed property → owner contact. This is the current `re-tax-leads` pipeline (`leads/pipeline.py`).
- **Buyer pipeline (new):** county recorder/deed records → all-cash sales + repeat/LLC buyers → buyer profile (geography, price band, property type, purchase cadence).
- **Match layer (new):** score seller leads against buyer profiles. The product sold is the match — data, not a brokered deal. This is what keeps the business out of unlicensed-brokerage territory in every state.

## 2. Outreach subsystem (new — human-in-the-loop, not autonomous send)

This is the piece that makes the pipeline feel end-to-end without crossing into autonomous dealmaking or spam:

- **Drafting:** for each qualified seller lead or buyer match, generate a personalized email referencing the specific research (case filing, delinquency amount/years, property details, comparable matches) using an LLM call grounded in the DB record — not a generic template blast.
- **Queueing:** every drafted email is created as a Gmail draft via the Gmail MCP tools (`create_draft`), never sent directly. Nothing leaves the account without a human click.
- **Batch review:** build a daily digest (CLI command or simple export) listing all pending drafts with the underlying research, so the user can review/edit/approve a batch in one pass rather than reading each one from scratch. Sending itself is still a per-message user action (assistant tooling requires explicit per-send permission; do not attempt to build a standing auto-send rule).
- **Reply handling:** when a seller or buyer replies to a sent email, draft a response grounded in the thread + the property/buyer record (e.g., answering a question about the delinquency amount, confirming a buyer's criteria match) as a new Gmail draft on that thread — again, queued for human approval, never auto-sent.
- **Compliance baked into every generated draft:**
  - CAN-SPAM: accurate sender identification, truthful subject line, physical mailing address in the footer, a working opt-out/unsubscribe mechanism, and honor opt-outs within 10 business days (build an opt-out/suppression list the drafting step checks before generating anything for that contact again).
  - TCPA/DNC: screen phone-based outreach (if any) against the national DNC registry before a contact is eligible for a non-email touch.
  - No property marketing language implying the sender has an ownership/contractual interest in a specific home unless that's true — outreach to buyers should read as data/match delivery, not deal-brokering.
  - Every generated draft is traceable to the source record (case ID / match ID) for audit.

## 3. Geographic rollout

Prioritize counties with strong investor demand **and** usable public data (bulk export/API over hostile portal-only access) — same ladder `config/counties.toml` already encodes (`bulk_dataset` > `api` > `portal_scrape`):

- Keep finishing: Harris & Dallas, TX
- Add next: Tarrant & Bexar (TX), Maricopa (AZ), Cook (IL), Miami-Dade & Broward (FL), Fulton (GA), Wayne (MI), Clark (NV), Franklin (OH)
- Each new county is bootstrapped the way Harris/Dallas were: run a probe script first to determine what tier of data access is available before writing an adapter. Do not hand-write a portal scraper for a county until bulk/API access has been ruled out.

## 4. Where cloud agents earn their keep

Not "scrape everything nationwide" — PropStream, BatchLeads, DealMachine, and LienSuite (413 counties already) have years of head start on breadth. The edge here is using cloud agents to **generate and repair per-county adapters on demand**: probe a new county's clerk/recorder/appraisal site, draft the adapter, register it in `counties.toml`, and flag when an existing county's site changes and breaks its scraper. This turns county expansion from a manual engineering bottleneck into a queueable, on-demand task.

## 5. New components to build

- `adapters/*_recorder.py` — deed/recorder adapters per county (mirrors the existing clerk/tax adapter pattern in `adapters/base.py`), flagging cash sales (no mortgage instrument recorded) and repeat/LLC buyers
- `leads/buyers.py` — buyer profile table + upsert logic, parallel to `case_record`/`property_record` in `leads/db.py`
- `leads/matching.py` — scoring function joining seller leads to buyer profiles by geography/price band/property type, following the pattern of `Pipeline.score()` in `leads/pipeline.py`
- `leads/outreach.py` — drafting engine described in §2: builds grounded email drafts from lead/match records, calls the Gmail MCP `create_draft` tool, maintains the opt-out/suppression list, and generates the daily review digest
- `leads/delivery.py` — packages matches for sale: per-lead export, buyer-facing digest, or a simple subscriber view (CSV/email digest for phase 1 — no marketplace UI yet)
- Compliance module — TCPA/DNC screening, CAN-SPAM footer/opt-out injection, suppression-list enforcement, shared by both `outreach.py` and `delivery.py`

## 6. Guardrails (hold across every phase)

- Only scrape sources that are public government records or already offer bulk/API access — never screen-scrape private marketplaces (MLS, Zillow, etc.); that's where legal risk concentrates.
- The system drafts and, on approval, sends outreach and responses — it never negotiates terms, drafts a purchase/assignment contract, or executes/signs anything. Any lead that reaches contract stage stops for full human (and where relevant, licensed/legal) handling outside this system.
- No standing auto-send automation — every send action requires an explicit human approval at send time, not a one-time blanket authorization.
- Re-verify per-state wholesaling/broker rules before enabling any outreach in high-risk states (KY, MD, OK, NC, SC, PA, IL) — the drafting engine should be able to suppress or flag outreach by state pending review.
- Every generated email must pass the CAN-SPAM/TCPA compliance module before it's created as a draft.

## 7. Phased build order

1. **Done (code):** Finish the Dallas adapter + wire BatchData.
   - Dallas tax: live DCAD ASP.NET owner search (`adapters/platforms/dcad_owner.py`).
   - Dallas clerk: Tyler Odyssey Smart Search (`adapters/platforms/dallas_odyssey.py`) with fixture ingest and optional Anti-Captcha reCAPTCHA.
   - Skip-trace: `counties.toml` → `batchdata`; secrets loader; stub fallback if key missing.
   - **Validate next:** set `BATCHDATA_API_KEY`; for live Dallas clerk set `ANTICAPTCHA_API_KEY` or drop Odyssey HTML into `artifacts/raw/dallas/clerk/`; run `python3 -m leads run --county dallas`.
2. **Done (code):** Outcomes ledger (§8) — `outcome_event` table, `leads/ledger.py`, `leads ledger log|report|history` CLI, wired into `approve_lead`/`reject_lead`/`skip_lead` so every human review decision already writes a win/loss row. Tests in `tests/test_ledger.py`.
3. Bootstrap 2–3 new metro counties via the probe pattern (§3).
4. Build the recorder/buyer-side adapter for one county end-to-end; prove cash-buyer detection works before replicating it.
5. Build `leads/matching.py` + `leads/delivery.py`; sell the first matched leads manually (no UI, no automated outreach yet) to validate willingness to pay before investing further. Every match/sale should log to the ledger (§8) from day one.
6. Build the outreach subsystem (§2): drafting, Gmail draft queueing, daily review digest, reply-drafting, compliance module. Ship it first against the seller pipeline (owner contact) before extending it to buyer-side delivery. Every drafted/sent/replied event logs to the ledger, stamped with `template_version`.
7. Only after 1–6 are working and validated: invest in scraper breadth / cloud-agent-driven county expansion (§4), any self-serve buyer-facing surface, and the first automated pass over the ledger (§8) that adjusts template/matching choices — still behind human approval, per §6.

## 8. Outcomes ledger (built) and the Postgres migration (built)

**Built:** an append-only `outcome_event` table (`entity_type`, `entity_id`, `event_type`, `weight`, `channel`, `template_version`, `matching_version`, `notes`, `occurred_at`) plus `leads/ledger.py` (a canonical event→default-weight vocabulary in `DEFAULT_WEIGHTS`, `record()`, `history()`, `report()`) and a `leads ledger` CLI. This is the "remembers wins and losses" structure discussed: nothing is trained on it yet, it's the ledger everything else will eventually learn from. `report()` does plain aggregation (counts, total/avg weight) grouped by entity/event/template/matching version — that's intentional; don't replace this with a model until there are months of real outcomes to train on, and any automated behavior change built on top of it still needs a human-approval checkpoint, same as sends and contracts.

**Postgres migration (done):** `leads/db.py` now dispatches on `DATABASE_URL` — unset (or no scheme) keeps the original local SQLite file (`leads.db`); `postgres://`/`postgresql://` routes through a `PGConnection` wrapper that translates `?`/`:name` placeholders to psycopg2's `%s`/`%(name)s` and exposes the same `.execute()/.commit()/.rollback()/.close()` surface `sqlite3.Connection` already had, so `pipeline.py`/`review.py`/`ledger.py` needed no call-site changes beyond the `RETURNING id` swap below. Schema: `SCHEMA_POSTGRES` is `SCHEMA_SQLITE` with `SERIAL PRIMARY KEY` swapped in for `INTEGER PRIMARY KEY AUTOINCREMENT` — everything else (types, `ON CONFLICT ... EXCLUDED`, indexes) is valid on both engines unchanged. The two SQLite-only call sites flagged here previously (`leads/pipeline.py` `_enrich_case`'s `contact_id = ...` line, `leads/review.py` `paste_contact`'s `contact_id = ...` line) both now use `INSERT ... RETURNING id` instead of `SELECT last_insert_rowid()`; `leads/db.py`'s `insert_outcome_event` got the same swap. This was verified by running the full test suite against both a local SQLite file and a local Homebrew Postgres 16 instance, and by a live non-dry-run `leads run --county harris` against real Postgres.

**Local setup:** `brew install postgresql@16 && brew services start postgresql@16 && createdb re_tax_leads`, then set `DATABASE_URL=postgresql://localhost:5432/re_tax_leads` in `config/secrets.env` (gitignored). Leave `DATABASE_URL` unset to keep using SQLite (e.g. for quick tests). Cloud agents should be pointed at Postgres, not the local SQLite file, since only Postgres is reachable remotely.
