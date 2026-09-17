# How to use

Paste everything between **BEGIN AGENT PROMPT** and **END AGENT PROMPT** into a new Cursor agent chat in this repo. Do not skip the audit. Do not start production `leads.db` / cron runs until all five real-world tests have passed and each post-test audit is written.

---

# BEGIN AGENT PROMPT

You are bringing `re-tax-leads` from “code exists” to “this operator can actually run the Phase 1 tax-suit lead pipeline.” Work in this order and do not jump ahead: **audit → run plan → complete what you can → five real-world full-program tests with an audit after each → stop before real runs.**

This is Phase 1 only (Harris + Dallas ingest → assessor match → skip trace → review → CSV). Do not start Project Plan phases 3–7 (new counties, buyer pipeline, matching marketplace, outreach).

## Mission

1. Read the repo and determine the exact current state.
2. Figure out every concrete step required for the program to run on this machine.
3. Do every step you can complete yourself. If a step is genuinely impossible without the operator (secrets they have not provided, paid accounts, county permission, physical mail, browser logins you cannot complete), stop that item, write a precise handoff, and continue everything else.
4. After the program can execute, run **five full-program tests against real-world sources/applications**, not unit tests and not mocks.
5. After **each** of those five tests, write an audit of what went well and what must change **before real runs**. Fix what you can, then proceed to the next test. Do not begin real runs in this session.

Real runs mean: writing to production `leads.db`, enabling cron/`scripts/run_daily.sh` on a schedule, unbounded paid skip-trace, or treating output as dialer-ready inventory.

## Hard rules

- Never print, commit, or paste `config/secrets.env`, API keys, passwords, or production `leads.db` contents.
- Never use production `leads.db` for these tests. Use a scratch database under `artifacts/runtime/` (create it). `--dry-run` for tests that must not persist.
- Never set `DALLAS_CLERK_LIVE_ENABLED=1`. Dallas clerk input is saved Odyssey HTML unless the operator has already explicitly confirmed the county permits portal automation. A CAPTCHA key is not permission.
- Never enable `LEADS_SAVE_RAW=1` unless a source contract is broken and you need one diagnostic capture. Turn it back off.
- Never replay POST automatically. BatchData skip-trace is paid. Until the operator confirms a spending ceiling, **enrich at most 5 leads per test** that would call BatchData. If the CLI cannot cap that, add a `--max-enrich` (or equivalent) flag as part of making the program runnable, with tests.
- Prefer Python 3.12+ with OpenSSL. The existing `.venv` may be 3.9/LibreSSL; recreate it on 3.12 if that interpreter exists. Do not “fix” TLS by ignoring warnings.
- Do not scrape MLS/Zillow/private marketplaces. Stay on public government sources already in this repo.
- Do not rewrite git history, relocate iCloud-synced runtime data, or invent retention policy. Flag those as operator decisions.
- If you cannot complete something, say so in one short block: what is blocked, why only a human can do it, the exact command/file/URL, and how you will know it is done. Then keep going.

## Phase A — Repo audit (do this first, fully)

Read before changing anything. Do not skim.

Required reading:

- `README.md`
- `docs/PROJECT_PLAN.md` (context only; do not implement later phases)
- `docs/RELIABILITY_REVIEW.md`
- `config/counties.toml`
- `config/secrets.env.example` (not `secrets.env`)
- `scripts/run_daily.sh`, `scripts/bootstrap_county.py`
- `leads/cli.py`, `leads/pipeline.py`, `leads/db.py`, `leads/validate.py`, `leads/secrets.py`, `leads/review.py`, `leads/http.py`
- Harris and Dallas adapters under `adapters/tx/` and `adapters/platforms/`
- `providers/skip_trace/`
- `requirements.txt`, `.gitignore`
- Latest `artifacts/phase1_validation.json` and `artifacts/phase2_probe.json` if present
- Test layout under `tests/` (know what is already covered; do not treat passing unit tests as “the program runs”)

Also inspect the machine, without dumping secrets:

- Python versions (`python3.12`, `python3`); whether `.venv` exists and which version it is
- Whether `config/secrets.env` exists (yes/no only) and whether `BATCHDATA_API_KEY` is **present or missing** — never the value
- Whether Dallas HTML exists under `artifacts/raw/dallas/clerk/*.html`
- Whether `leads.db` already exists (do not open/query it for PII)
- Whether Postgres is installed/running (optional; SQLite is the default)

Write `artifacts/runtime/AUDIT.md` with:

- What is implemented vs documented vs broken
- Exact command path for a working run
- Blockers classified as **agent-can-fix** vs **operator-only**
- Spend/PII/legal risks that affect testing
- Pass/fail criteria for “the program runs”

## Phase B — Exact run plan

From the audit, produce a numbered run plan in `artifacts/runtime/RUN_PLAN.md`. Every item must be a concrete action (install X, set key K, save HTML to path P, run command C). No vague “set up environment.”

The program is runnable for Phase 1 when all of this is true:

1. Python 3.12+ venv with `pip install -r requirements.txt` and `pip check` clean.
2. `config/secrets.env` exists (copied from example if needed).
3. Offline tests pass: `python3 -m unittest discover -s tests -v`.
4. Harris can ingest live bulk civil summaries from the District Clerk public datasets.
5. Harris can match owners on live HCAD ArcGIS REST.
6. Dallas can ingest from saved Odyssey HTML (live portal off).
7. Dallas can match owners on live DCAD `searchowner.aspx`.
8. Skip-trace uses BatchData when the key is present, otherwise stub — and the operator is told which one is active.
9. Review CLI can list / approve / reject / paste / retry.
10. `export` writes a CSV a human could load into a dialer/CRM.
11. `state` and `ledger` work on the scratch DB.
12. Daily script path is understood (`scripts/run_daily.sh` → `leads run --all-enabled --since 7d`) but **not** scheduled.

## Phase C — Complete what you can

Execute every **agent-can-fix** item now. Typical work you should just do:

- Create/recreate `.venv` on Python 3.12; install pins; run the unit suite; fix failures you caused or that block running
- Copy `config/secrets.env.example` → `config/secrets.env` if missing (leave keys empty)
- Create `artifacts/runtime/`, a scratch DB path, and export dir under `artifacts/runtime/exports/`
- Add `--max-enrich N` (or equivalent) if there is no safe way to run a live enrich without charging BatchData for every new Harris suit
- Fix adapter/HTTP/config bugs discovered while making the program start
- Document exact Dallas HTML capture steps if fixtures are stale or missing

Operator-only (handoff, do not fake):

- Obtain/paste `BATCHDATA_API_KEY`
- Confirm BatchData spend ceiling
- Save fresh Dallas Odyssey Smart Search HTML (business name `DALLAS COUNTY TAX*`, file-date range) to `artifacts/raw/dallas/clerk/`
- Buy Dallas civil-index subscription / DCAD bulk products (preferred long-term; not required to finish these tests if fixtures + live DCAD work)
- Decide Postgres vs SQLite for production (tests stay on scratch SQLite unless they already use a scratch Postgres)
- Move runtime data off iCloud Drive (flag only)

If a blocker stops a later test, still run every test that does not need it, and mark the skipped test as blocked with the handoff.

## Phase D — Five full-program tests (real-world applications)

These are **not** `unittest` tests. Each one must exercise the real CLI against real county/vendor systems or real operator artifacts. Use the scratch DB:

```bash
cd re-tax-leads
source .venv/bin/activate
export PYTHONPATH="$PWD"
SCRATCH="artifacts/runtime/test_leads.db"
```

Pass `--db "$SCRATCH"` on every command that writes. If a test would call BatchData, cap enrich at 5 leads.

After each test: stop, write the audit (template below) to `artifacts/runtime/TEST_N_AUDIT.md`, apply any code/config fixes that the audit requires and that you can do, re-run that test if the fix is needed for a truthful pass, then continue.

### Test 1 — Harris live ingest (District Clerk public datasets)

Real application: Harris County District Clerk Public Datasets (bulk civil case summaries).

```bash
python3 -m leads run --county harris --since 7d --dry-run
```

Must prove: HTTP/pacing against the real listing, tax-suit filtering, nonzero or honestly zero `found` count, no production DB write, exit status truthful.

### Test 2 — Harris live persist + HCAD match (+ skip-trace if keyed)

Real applications: same bulk listing **and** HCAD ArcGIS Parcel REST, plus BatchData v3 if `BATCHDATA_API_KEY` is present (else stub, and the audit must say contacts are not production-ready).

Ingest into `$SCRATCH` (not dry-run). Enrich at most 5 leads. Then:

```bash
python3 -m leads state --db "$SCRATCH"
```

Must prove: cases inserted, leads created, owner match attempted against live GIS, statuses are `enriched` / `needs_review` / `error` with useful reasons, not silent empties.

### Test 3 — Dallas fixtures + live DCAD

Real applications: saved Odyssey HTML (real court portal capture) and live DCAD owner search.

```bash
python3 -m leads run --county dallas --since 30d --db "$SCRATCH"
python3 -m leads review list --status pending --db "$SCRATCH"
```

Must prove: fixture ingest works without live Odyssey; DCAD returns property fields or an explicit no-match review reason; Dallas does not crash if Harris data is already in the scratch DB.

If there is no Dallas HTML, this test is operator-blocked: write the capture recipe and skip rather than enabling live clerk mode.

### Test 4 — Operator workflow on real rows (review, ledger, CRM export)

Real applications: the review queue and a CSV a human would load into a dialer/CRM (Excel, Google Sheets, or a CSV-based dialer). Do not email or autodial anyone.

On `$SCRATCH` rows from tests 2–3:

- `review list` pending and any `dead_letter`
- `review approve` one lead with a note
- `review reject` or `skip` one lead
- `review paste` a **synthetic** contact on a lead that has a property and no contact (fake name/phone; never a real person from skip-trace dumps into chat)
- `ledger report` and `ledger history` for those lead IDs
- `export --status approved -o artifacts/runtime/exports/test4_approved.csv`

Then open/inspect the CSV: headers, one approved row, no secrets, mode/permissions sane.

Must prove: human-in-the-loop path works end to end and the export is actually usable.

### Test 5 — Daily-job rehearsal (both enabled counties) + resume/failure visibility

Real application: the operator’s daily path, which is `scripts/run_daily.sh` → `leads run --all-enabled --since 7d`.

Rehearse it against `$SCRATCH`, not production:

- Run both enabled counties into `$SCRATCH` with enrich capped
- `python3 -m leads state --db "$SCRATCH"`
- Confirm a second run is resumable (no duplicate-case explosion; completed leads are not re-enriched)
- If any lead is `error`/`dead_letter`, exercise `review retry` only after recording why; do not blindly requeue paid skip-trace
- Confirm `run_daily.sh` points at the venv Python and would hit production `leads.db` if invoked for real — do **not** invoke it against production

Must prove: the real daily command shape works, failures are visible, and it is safe to consider scheduling later (scheduling itself is a real-run step; do not cron it).

## Post-test audit template (required after every test)

Write `artifacts/runtime/TEST_N_AUDIT.md` using this structure:

```markdown
# Test N audit — <name> — <date>

## What we ran
- Commands, scratch DB path, since-window, enrich cap, Python version

## Real-world systems hit
- Source URLs/hosts (no query PII), provider name, fixture files used

## Evidence
- Exit code
- Counts: found / inserted / processed / enriched / needs_review / errors / dead_letter
- One-line sample of non-PII evidence (case counts, property_type present, CSV row count)
- Whether BatchData was called (yes/no) and how many times

## What went well
- ...

## What must change before real runs
- Bugs to fix (fix now if you can, then re-run this test)
- Operator decisions still open
- Spend, permission, fixture freshness, completeness gaps

## Verdict
- PASS / FAIL / BLOCKED
- Safe to proceed to Test N+1? yes/no
- Safe for real runs yet? no (until Test 5 also passes and remaining blockers are listed)
```

Do not start Test N+1 until Test N’s audit file exists. If Test N fails for a reason you can fix, fix it, re-run Test N, and update the audit. If it fails for an operator-only reason, mark BLOCKED and continue only with tests that do not depend on that blocker.

## Phase E — Stop before real runs

After Test 5, write `artifacts/runtime/READY_FOR_REAL_RUNS.md`:

- Checklist of the 12 “program is runnable” items (done / blocked)
- Table of the five tests (verdict + one-line finding)
- Remaining operator work, copy-pasteable
- Explicit statement: **do not run `./scripts/run_daily.sh` or write to `leads.db` until the operator accepts this report**
- Recommended first real run *when they take over*: county, since-window, enrich cap, BatchData ceiling, Dallas HTML freshness

## Operator handoff format (when you cannot complete something)

```markdown
### BLOCKED: <short name>
- Why only the operator can do this:
- Exact action: (URL, file to edit, value to paste — never ask them to paste the secret back into chat)
- How I will detect it is done: (e.g. preflight shows BATCHDATA_API_KEY=present)
- Tests waiting on this: Test N, ...
```

Ask the operator only when you are actually stuck. Batch independent questions. Do not wait to start Tests 1–3 if only BatchData is missing; those can use stub skip-trace with a loud audit note.

## Done looks like

- `artifacts/runtime/AUDIT.md`
- `artifacts/runtime/RUN_PLAN.md`
- `artifacts/runtime/TEST_1_AUDIT.md` … `TEST_5_AUDIT.md`
- `artifacts/runtime/READY_FOR_REAL_RUNS.md`
- Code/config fixes you made so the program can run and the five tests were honest
- Production `leads.db` untouched; cron not installed; Dallas live portal still off

# END AGENT PROMPT
