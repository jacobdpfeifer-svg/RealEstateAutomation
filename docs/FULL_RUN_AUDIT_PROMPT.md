# How to use

Paste everything between **BEGIN AGENT PROMPT** and **END AGENT PROMPT** into
a new agent chat in this repo. Unlike
[`FIRST_REAL_RUN_PROMPT.md`](FIRST_REAL_RUN_PROMPT.md), this is **not** a
production run — it deliberately stays on scratch storage so it can exercise
every command path (including ones nobody has run in a while) without risking
real inventory, and spends its second half comparing what actually happened
against what the docs and code claim. Use it periodically as a smoke test +
truth-check of the repo, not as a substitute for the real run.

---

# BEGIN AGENT PROMPT

You are auditing `re-tax-leads` end to end: exercise every CLI command and
pipeline stage against **scratch** storage and fixtures, then audit the
results — both against each other and against what `README.md` and the
`docs/` folder currently claim. The goal is a single report a human can read
in a few minutes that says, with evidence, what actually works today, what's
stale documentation, and what's broken.

## Mission

1. Preflight: capture environment/tooling state before touching anything.
2. Run every command surface the CLI exposes, on scratch DBs/fixtures only —
   never the production database, never a live portal, never a paid call.
3. Audit the results: does the code do what the docs say it does? Do the
   docs contradict each other (e.g. a stale count, a command that no longer
   exists, a claim a recent fix already invalidated)? Do tests cover what
   just got exercised?
4. Fix small, provable bugs you actually hit while running things — same
   bounded standard as `FIRST_REAL_RUN_PROMPT.md`'s Phase D. Do not redesign
   anything or start Phase 4+ work (`leads/buyers.py`, `leads/matching.py`,
   `leads/outreach.py`, any recorder adapter).
5. Write one final report: a run log (what was executed, exit codes, what it
   produced) plus an audit section (claim vs. observed reality, one row per
   discrepancy).

## Hard rules

- **Never touch the production database.** Every command below either uses
  `--db` with an explicit scratch path, `--dry-run`, or `artifacts/phase1_validation.db`.
  Do not unset `--db` and do not rely on `DATABASE_URL` pointing anywhere
  except by accident — if `DATABASE_URL` is set to Postgres, still pass
  `--db` explicitly to every command that accepts it so scratch runs cannot
  silently land in production. If a command has no scratch/dry-run mode,
  read its code instead of running it against a real target and say so in
  the report.
- Never set `DALLAS_CLERK_LIVE_ENABLED=1`, `BEXAR_CLERK_LIVE_ENABLED=1`, or
  enable any disabled county in `config/counties.toml`. Never attempt to
  solve or bypass a CAPTCHA/WAF challenge.
- Never make a real BatchData call. Confirm `BATCHDATA_API_KEY` presence
  only (grep for the line, never print the value); if it's present, force
  the stub path or skip that exercise rather than spending money — paid
  calls require an explicit operator-given dollar ceiling, which this audit
  does not have.
- Never print or commit secrets. Never read owner names/addresses/contacts
  out of any database into chat or the report — counts and categories only.
- "Fix as you go" is bounded exactly like `FIRST_REAL_RUN_PROMPT.md`: fix a
  bug in this codebase with a test, don't weaken a safety check, don't flip
  a disabled county on, don't lower a confidence threshold, don't build
  anything under a Phase 4+ heading. If you think of something bigger, write
  it in the report instead of building it.
- The full test suite must be green at the end
  (`python3 -m unittest discover -s tests -v`), including any new tests you
  added for bugs you fixed.

## Phase A — Preflight (write `artifacts/runtime/FULL_RUN_AUDIT_PREFLIGHT.md`)

```bash
cd re-tax-leads
source .venv/bin/activate
export PYTHONPATH="$PWD"
python3 --version
python3 -m unittest discover -s tests -v 2>&1 | tail -5
grep -c '^BATCHDATA_API_KEY=.\+' config/secrets.env || true   # presence only
grep '^enabled' config/counties.toml
ls -la artifacts/raw/dallas/clerk/
git log --oneline -10 2>/dev/null || true
```

Record: python version, baseline test count/result, BatchData present/missing,
which counties are enabled, Dallas fixture state, and the last few commits (so
the audit section can tell "recently fixed" from "still broken" claims apart).

## Phase B — Run everything (scratch only)

Work through every subcommand `python3 -m leads --help` lists. Use a fresh
scratch DB path per exercise so runs don't interfere with each other; keep
each command's stdout/stderr in `artifacts/runtime/audit_logs/`.

```bash
SCRATCH=artifacts/runtime/audit_$(date +%Y%m%d_%H%M%S)
mkdir -p "$SCRATCH" artifacts/runtime/audit_logs

# County probing (read-only classification, no writes)
python3 -m leads probe --help
python3 scripts/bootstrap_county.py --county all 2>&1 | tee artifacts/runtime/audit_logs/bootstrap_all.log

# Phase 1 validation harness (its own DB, never leads.db)
python3 -m leads validate -o artifacts/phase1_validation.json 2>&1 | tee artifacts/runtime/audit_logs/validate.log

# Dry runs (in-memory DB, still hit live source for Harris — expected/known)
DALLAS_CLERK_LIVE_ENABLED=0 LEADS_SAVE_RAW=0 python3 -m leads run --county harris --since 7d --dry-run 2>&1 | tee artifacts/runtime/audit_logs/harris_dry_run.log
DALLAS_CLERK_LIVE_ENABLED=0 LEADS_SAVE_RAW=0 python3 -m leads run --county dallas --since 30d --dry-run 2>&1 | tee artifacts/runtime/audit_logs/dallas_dry_run.log

# Real writes, but to a throwaway scratch DB
DALLAS_CLERK_LIVE_ENABLED=0 LEADS_SAVE_RAW=0 python3 -m leads run --county harris --since 7d --max-enrich 5 --db "$SCRATCH/leads.db" 2>&1 | tee artifacts/runtime/audit_logs/harris_scratch_run.log
python3 -m leads run --county dallas --since 30d --max-enrich 5 --db "$SCRATCH/leads.db" --append-only 2>&1 | tee artifacts/runtime/audit_logs/dallas_scratch_run.log

# Inventory / status surfaces
python3 -m leads state --db "$SCRATCH/leads.db"
python3 -m leads readiness --db "$SCRATCH/leads.db"

# Pipeline stage-by-stage
python3 -m leads pipeline --help
python3 -m leads pipeline enrich-pending --county harris --max-enrich 5 --db "$SCRATCH/leads.db" 2>&1 | tee artifacts/runtime/audit_logs/enrich_pending.log

# Review queue surface — list, summary, paste, research-hints, approve/reject on scratch data only
python3 -m leads review list --status pending --db "$SCRATCH/leads.db"
python3 -m leads review list --status dead_letter --db "$SCRATCH/leads.db"
python3 -m leads review summary --status pending --db "$SCRATCH/leads.db"
python3 -m leads review --help   # confirm every listed subcommand (approve/reject/paste/retry/research-hints) actually exists and matches README
# Exercise research-hints and paste against one scratch lead ID from the list above:
python3 -m leads review research-hints <id> --db "$SCRATCH/leads.db"
python3 -m leads review paste <id> --name "Scratch Test" --phone "5555550100" --db "$SCRATCH/leads.db"
python3 -m leads review approve <id> --note "audit scratch approval" --db "$SCRATCH/leads.db"

# Export
python3 -m leads export --status approved -o "$SCRATCH/export.csv" --db "$SCRATCH/leads.db"
cat "$SCRATCH/export.csv"   # scratch/synthetic data only — fine to view

# Ledger
python3 -m leads ledger --help
python3 -m leads ledger log --help
python3 -m leads ledger report --help
python3 -m leads ledger history --db "$SCRATCH/leads.db"

# Contact enrichment / buyer research artifacts already on disk
ls -la artifacts/contact_enrichment artifacts/buyer_research 2>/dev/null
```

For every command above: record the exact invocation, exit code, and a
one-line summary of what it produced. If a command errors, that's a finding
for Phase C, not something to paper over.

If a command needs an argument this list doesn't supply (a lead ID, a status
value), get it from the previous command's own output — don't invent one.

## Phase C — On failure

Same discipline as `FIRST_REAL_RUN_PROMPT.md`:

1. Read the actual error (structured JSON on stderr; results on stdout).
2. Find the real cause in the code, not just the symptom.
3. Write an offline, fixture-based regression test before fixing.
4. Fix it, rerun the full suite, rerun the specific command that failed.
5. Note the bug/fix/test in the final report. If the honest answer is "this
   needs an operator decision," write that up and move on.

## Phase D — Audit (this is the part `FIRST_REAL_RUN_PROMPT.md` doesn't do)

Go through `README.md` and every file in `docs/` and check each concrete,
checkable claim against what Phase B actually observed or against the
current code. For each claim, record one of: **confirmed**, **stale**
(was true, code/data has since moved on), **contradicted** (docs disagree
with each other or with the code), or **unverifiable** (would require a
live/paid call this audit didn't make). Specifically check:

- README's CLI examples: does every command shown still exist with the same
  flags? Do exit codes/behavior match what's described (e.g. `--dry-run`
  truly makes no DB writes; `--append-only` truly leaves existing rows
  untouched; the file lock/Postgres advisory lock behaves as documented)?
- Counts and status claims in `LAUNCH_REVIEW.md`, `OPERATOR_COMPLETION_PROMPT.md`,
  and any `FULL_RUN_REPORT.md`/`OPERATOR_COMPLETION_REPORT.md` under
  `artifacts/runtime/` — these are point-in-time snapshots; flag any that a
  reader could mistake for current state, and say what the current state
  actually is (from your scratch run plus a read-only production count
  check if you have DB access — counts only, never row contents).
- `CONTACT_IMPLEMENTATION_AUDIT.md`'s "remaining findings" — are they still
  present in the code, or did a later change fix one without updating this
  doc?
- `RELIABILITY_REVIEW.md`'s specific behavioral claims (retry caps, lock
  semantics, `Retry-After` handling, dead-letter thresholds) — check them
  against `leads/http.py`, `leads/pipeline.py`, `leads/db.py` directly, since
  most won't be exercisable without a live failing host.
- `PROJECT_PLAN.md`'s phase boundaries — confirm nothing under a later phase
  heading (buyer discovery, matching, outreach) has quietly been implemented
  or partially started, which would mean the plan doc is now inaccurate in
  the other direction.
- Whether the test suite (`tests/`) actually covers every code path Phase B
  exercised, or whether something you just ran by hand has zero test
  coverage — call those out explicitly as a coverage gap, don't just note it
  passed manually.

Also do a lightweight correctness pass (not a full `/code-review`) on any
file you touched during Phase C fixes, and on any command whose *output*
looked suspicious in Phase B even if it didn't error (wrong count, empty
result where one was expected, a field silently null).

## Phase E — Final report (`artifacts/runtime/FULL_RUN_AUDIT_REPORT.md`)

1. **Run log:** every command from Phase B, exit code, one-line result,
   link to its log file under `artifacts/runtime/audit_logs/`.
2. **Bugs found and fixed:** symptom, root cause, fix, new test, suite
   result before/after.
3. **Audit table:** one row per checked claim — source doc + line, the
   claim, verdict (confirmed / stale / contradicted / unverifiable), and the
   evidence for that verdict.
4. **Coverage gaps:** commands/paths exercised in Phase B with no
   corresponding automated test.
5. **Unresolved/blocked items:** anything that needs an operator decision,
   phrased exactly like `FIRST_REAL_RUN_PROMPT.md`'s handoff format — what's
   blocked, why, the exact action, and what would prove it's resolved.
6. **Explicit non-claims:** state plainly that no production data was
   touched, no paid provider calls were made, and no live portal automation
   ran — with the evidence (commands used, flags passed) that backs that up.

# END AGENT PROMPT
