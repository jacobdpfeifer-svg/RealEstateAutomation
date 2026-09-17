# How to use

Paste everything between **BEGIN AGENT PROMPT** and **END AGENT PROMPT** into
a new agent chat in this repo. This is the first run against **real**
production storage — every prior phase in this repo deliberately used scratch
databases only. Read the whole prompt before starting; the preflight step
matters more than usual here because it's checking things that change the
shape of what "a full run" even means today.

---

# BEGIN AGENT PROMPT

You are running `re-tax-leads` for real: ingesting from whatever live sources
are actually available right now, into the actual production database, then
fixing anything that breaks and continuing. This is not a scratch rehearsal —
say so plainly anywhere you'd normally write "scratch DB."

## Mission

1. Preflight: find out exactly what's available today (don't assume it
   matches an old doc) and write it down before touching anything.
2. Run every enabled county against real sources into the real production
   database.
3. When something fails, stop, diagnose, fix the actual cause, add a test
   that would have caught it, re-run, and only then continue. Don't route
   around a failure and don't silently swallow it.
4. While you're in here anyway, keep noticing things that could be more
   reliable, clearer, or less brittle, and fix the small ones as you go —
   but see the boundary on this below. It is not license to redesign things.
5. Write one final report a human can read in two minutes and know exactly
   what's in the database now and what still needs a human.

## What's actually true right now (verify, don't trust this list blindly — it will be stale by the time you run)

- `config/counties.toml`: Harris and Dallas are `enabled = true`. Bexar,
  Tarrant, Maricopa are `enabled = false` — Phase 3 discovery stopped them
  for real reasons (wrong court, wrong legal model, WAF wall with no
  registration path); do not flip any of them on. See
  `docs/WIRE_IN_NEXT_COUNTIES_PROMPT.md` if you want the detail, but this
  is a closed decision, not an open TODO.
- `config/secrets.env`: `DATABASE_URL` is set to local Homebrew Postgres 16
  (`re_tax_leads`) — that is the confirmed production target, not SQLite
  `leads.db`. Do not pass `--db` (that forces SQLite and would silently miss
  the real store). `BATCHDATA_API_KEY` is unset as of this writing, meaning
  skip-trace will use the stub and **no real contacts will be produced** —
  confirm this hasn't changed before you start, and if a key has been added,
  stop and get an explicit dollar ceiling from the operator before enriching
  even one lead (never infer a ceiling; never guess one).
- `artifacts/raw/dallas/clerk/` contains only the original unit-test sample
  HTML (3 cases), not an authentic capture. A Dallas ingest today will find
  those same 3 sample cases again, not real filings — report it as exactly
  that, not as production Dallas data.
- The production Postgres database (`re_tax_leads`) already has real rows in
  it from an earlier session's live Harris run — check current counts
  yourself (read-only: `SELECT count(*) FROM case_record`, etc. — do not
  read owner names/contacts/addresses out of it into chat or a report).
  **Do not delete, truncate, or reset anything in it.** You are appending to
  existing production inventory, not starting from a clean slate.

## Hard rules

- Target is production Postgres via `DATABASE_URL` (no `--db` override) —
  confirm this resolves correctly before your first write; don't assume.
- Never delete or modify existing rows you didn't just create. If you find
  something that looks wrong in existing data, report it, don't "fix" it by
  editing production rows directly.
- Cap enrichment: `--max-enrich 5` per invocation unless the operator has
  given you a higher number in this conversation. If `BATCHDATA_API_KEY` is
  present and no dollar ceiling has been given to you explicitly, stop and
  ask before enriching anything — a missing ceiling is a blocker, not a
  default-to-zero situation you should quietly work around.
- Never set `DALLAS_CLERK_LIVE_ENABLED=1`, `BEXAR_CLERK_LIVE_ENABLED=1`, or
  enable any county's live portal automation. Never solve, bypass, or pay to
  bypass a CAPTCHA or bot-detection challenge (this includes AWS WAF
  "human verification" screens) — this has already been tried for Bexar and
  the wall is real and permanent for automation; it's not a puzzle to solve
  today.
- Never touch `config/secrets.env` values, never print them, never paste any
  key/token/password into a report or commit.
- "Stop and fix" means: fix bugs in this codebase (a parser edge case, an
  off-by-one, a bad retry, a misleading error, a missing test). It does
  **not** mean: weakening a safety check to make a run succeed, adding
  CAPTCHA-solving, fabricating credentials, flipping a disabled county on to
  get more data, or lowering the owner-match confidence threshold just to
  produce more "matches." If the honest fix is "this source needs an
  operator decision," write that up and move to the next thing instead of
  forcing it.
- The "keep improving it" mandate is scoped to reliability/correctness
  within Phase 1–3 territory: better error messages, closing a gap you
  actually hit during this run, a test for a bug you just fixed, small
  refactors that reduce duplication you're already touching. It is **not**
  license to start Phase 4+ work (`leads/buyers.py`, `leads/matching.py`,
  `leads/outreach.py`, any recorder adapter) — that boundary has held
  through every prior phase in this repo and holds here too. If you think of
  something bigger than a small fix, write it down in the final report
  instead of building it.
- Every code change still needs the existing test suite green
  (`python3 -m unittest discover -s tests -v`) before you consider it done,
  plus a test for whatever you just fixed.

## Phase A — Preflight (write `artifacts/runtime/FULL_RUN_PREFLIGHT.md`)

```bash
cd re-tax-leads
source .venv/bin/activate
export PYTHONPATH="$PWD"
python3 --version
python3 -m unittest discover -s tests -v 2>&1 | tail -5
grep -c '^BATCHDATA_API_KEY=.\+' config/secrets.env || true   # presence only, never the value
ls -la artifacts/raw/dallas/clerk/
grep '^enabled' config/counties.toml
```

Also confirm Postgres is actually reachable at `DATABASE_URL` and record
current row counts (counts only, no field contents):

```bash
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/postgresql@16/bin:$PATH"
psql -d re_tax_leads -c "SELECT 'case_record',count(*) FROM case_record UNION ALL SELECT 'property_record',count(*) FROM property_record UNION ALL SELECT 'lead',count(*) FROM lead UNION ALL SELECT 'contact_record',count(*) FROM contact_record;"
```

Write the preflight file with: python version, test result, BatchData
present/missing, Dallas fixture state, which counties are enabled, and the
starting row counts. This is your baseline — the final report diffs against
it.

## Phase B — Run

Start with Harris only, since it's the only county with both a proven live
clerk source and real (non-sample) data:

```bash
python3 -m leads run --county harris --since 7d --max-enrich 5
python3 -m leads state
```

If that's clean, run Dallas too (expect only the 3 sample cases — that's
correct behavior, not a bug):

```bash
python3 -m leads run --county dallas --since 30d --max-enrich 5
python3 -m leads state
```

Or run both in one invocation once you trust the individual runs:

```bash
python3 -m leads run --all-enabled --since 7d --max-enrich 5
```

Check the review queue so the run's output is actually visible, not just a
row count:

```bash
python3 -m leads review list --status pending
python3 -m leads review list --status dead_letter
```

## Phase C — On failure

1. Read the actual error (stderr has structured JSON events; stdout has
   command results). Don't guess from the exit code alone.
2. Find the real cause in the adapter/pipeline code, not just the symptom.
3. Write a test that reproduces it offline (fixture-based, not live) before
   you fix it, so it can't silently regress.
4. Fix it. Run the full suite. Re-run the specific command that failed.
5. Only then continue to the next county/step. Note the bug, the fix, and
   the new test in the final report — don't fix things silently.

If a failure is a real blocker (needs a decision only the operator can make —
a legal question, a spend ceiling, a missing credential), stop that thread,
write it up the same way `docs/MAKE_IT_RUN_PROMPT.md`'s handoff format does,
and continue with whatever doesn't depend on it.

## Phase D — Improve as you go (bounded — see Hard rules)

Legitimate examples: a confusing CLI error message you had to dig through
code to understand: fix it. A retry that doesn't log enough to debug next
time: improve the log line. A field mapping you notice is subtly wrong while
reading adapter code for an unrelated reason: fix it with a test. Duplicated
logic between two files you're already both touching: share it, the way the
ArcGIS config sharing was done in Phase 3.

Not legitimate: adding a new data source, changing the matching/scoring
model, touching anything under a Phase 4+ heading, or any change you can't
justify by pointing at something that actually happened during this run.

## Phase E — Final report (`artifacts/runtime/FULL_RUN_REPORT.md`)

- Preflight baseline vs. final row counts, per table, per county.
- Every command actually run, with exit codes.
- Every bug hit: symptom, root cause, fix, new test.
- Every improvement made and why it was in scope.
- Whether BatchData was ever called (should be "no" unless the operator gave
  you a key and a ceiling mid-run — say so explicitly either way).
- Explicit statement of what's still stub/non-production-grade (contacts, if
  no BatchData key; Dallas, since only sample data exists) so nobody mistakes
  this run's output for dialer-ready inventory.
- What you'd do next if allowed to keep going, without doing it.

# END AGENT PROMPT
