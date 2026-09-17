# Finish the operator requirements for re-tax-leads

Copy the prompt below into a new session when ready to complete the remaining work. It does not grant paid-spend authorization, permission to modify existing production leads, or scheduling approval. Fill those in explicitly when requested; never put credentials in the prompt.

Implemented code support: append-only runs; five-attempt append-only daily script; Postgres session lock across cooperating checkouts; aggregate `review summary` output. Offline suite: 92 passing tests at handoff. The lock was also verified against local Postgres using read-only sessions with migrations disabled. No production leads were processed during this implementation task.

---

BEGIN OPERATOR COMPLETION PROMPT

Help me complete the non-code prerequisites for operating re-tax-leads. Work from the current repository, not stale report counts. Complete authorized checks and prepare concrete actions before asking for decisions. Do not redesign the pipeline or implement Phase 4+ work.

## Context and boundaries

- Production is local PostgreSQL `re_tax_leads`, selected by `DATABASE_URL` loaded from `config/secrets.env`. Verify the effective backend and database before production work. Never add `--db` to a production command; explicit test paths select SQLite.
- Read `README.md`, `artifacts/runtime/FULL_RUN_PREFLIGHT.md`, `FULL_RUN_REPORT.md`, and `OPERATIONAL_IMPLEMENTATION.md`. Old rehearsal instructions do not override explicit current production authorization.
- Last recorded inventory was 26 cases / 7 properties / 26 leads / zero contacts: Harris 24 cases, Dallas 2 sample/unverified cases. There were 22 pending reviews and 4 new Harris leads. Recheck; these are historical counts.
- Harris and Dallas are enabled. Bexar, Tarrant and Maricopa stay disabled. Never enable live clerk automation, solve/bypass CAPTCHAs or WAF challenges, change confidence thresholds, delete production data, or reclassify sample records silently.
- Keep secrets and identity/contact fields out of chat, reports and commits. Check secret presence only. The operator enters keys locally; do not edit secret values yourself. Keep raw capture off unless separately authorized for a specific parser diagnosis.
- Preserve existing rows unless the operator explicitly approves the exact existing-lead workflow. Use `--append-only` for new production ingest. `--max-enrich 5` applies per invocation; reruns consume additional attempts and potentially money.

## 1. Verify the current state

From `re-tax-leads`, use the existing `.venv/bin/python3` and `PYTHONPATH="$PWD"`.

1. Check Git changes without overwriting work. Run `.venv/bin/python3 -m unittest discover -s tests -v`.
2. Load secrets through `leads.secrets.load_secrets()`; record only BatchData present/missing, backend, expected-database verification, portal gates and capture directory state. Do not print the environment or DSN.
3. Read aggregate production counts with a read-only connection. Use these CLI summaries for status/reason counts once the database is available:

   ```bash
   .venv/bin/python3 -m leads review summary --status pending
   .venv/bin/python3 -m leads review summary --status new
   .venv/bin/python3 -m leads review summary --status error
   .venv/bin/python3 -m leads review summary --status dead_letter
   ```

   These CLI commands use the normal schema-initializing application session and may return a busy error while another session holds its lock. Wait for that session to finish; do not disable the lock.
4. Record the baseline and outstanding prerequisites in `artifacts/runtime/OPERATOR_COMPLETION_REPORT.md`. Keep subsequent changes attributable to the authorized run.

## 2. Obtain authentic Dallas court input

Only the operator can establish access/provenance for this capture. Open the official portal manually: https://courtsportal.dallascounty.org/DALLASPROD. Follow `artifacts/runtime/DALLAS_CAPTURE.md`, refreshing its historical dates to the intended filing window.

- Use the permitted business-name search for `DALLAS COUNTY TAX*`. The operator handles login/challenges manually; the agent must not automate around them.
- Save fully rendered results for every page in a new private directory under `artifacts/raw/dallas/clerk/`. Include necessary details when parties/dates are absent. Preserve the unit fixture separately.
- Record capture time, source URL, query, filing window, page count and whether pagination is complete in a local provenance note. Do not include login tokens or credentials.
- Point `DALLAS_CLERK_FIXTURE_DIR` at only that capture directory for the invocation; nested directories are not automatically loaded. Never mix the unit fixture into the authentic capture.
- Compare parsed counts, filing dates and coverage with the operator's visible source. Byte differences from the sample alone do not prove authenticity. A login/challenge page is not a valid capture; a legitimate zero-result capture is possible and must be reported honestly.
- Validate without production writes first using the parser or `run --county dallas --since 30d --dry-run` with the approved capture directory. Dry-run does not enrich. If the real markup exposes a parser defect, preserve one permitted diagnostic capture, reproduce offline, fix within Phase 1–3 scope, and pass the suite before continuing.

Completion evidence: authentic capture path/provenance, verified date/page coverage and truthful parsed counts. Existing production Dallas sample rows remain unchanged and explicitly identified as sample/unverified in reports. Any cleanup/quarantine policy requires a separate decision.

## 3. Enable real contact enrichment only with explicit spending approval

The operator must configure `BATCHDATA_API_KEY` locally in `config/secrets.env`. Never request the key in chat. Confirm presence without displaying it.

Ask for an explicit USD ceiling, its scope/time window, and whether it includes every rerun. Verify the operator's current vendor pricing, worst-case charge per request and available account spending controls. Do not invent prices or infer a ceiling from the five-attempt cap. Missing ceiling or an inability to bound charges blocks paid requests, even when a key is present. An approved $0 ceiling authorizes no paid request.

The application limits attempts, not dollars across runs. For a bounded paid validation, verify that the maximum possible charge for the proposed requests fits the remaining authorized ceiling, using actual vendor terms and account limits. If that cannot be established, stop paid work and report what the operator must configure. Ambiguous timeout/billing requires reconciliation before retry; never automatically replay a paid POST.

Completion evidence: key-present boolean, exact approved budget/scope, verified charge bound, attempts/calls recorded, and vendor billing reconciliation. Stub success never establishes contact readiness. Existing needs_review leads do not automatically receive real contacts just because a key is added.

## 4. Authorize and process the existing Harris backlog

First show aggregate current new/error counts. Ask the operator to authorize modification of the current Harris new/error leads and state the maximum attempts (at most five unless explicitly raised). Do not interpret this prompt alone as row-update authorization. Confirm whether stub-only matching or paid contact enrichment is intended.

Only after that authorization and any paid gates are satisfied, the relevant command is:

```bash
.venv/bin/python3 -m leads pipeline enrich-pending --county harris --max-enrich 5
```

This intentionally modifies existing eligible Harris leads in ID order and can include error retries, not just the historical four new leads. Investigate every error and any uncertain billing before approving that scope. It does not ingest or rescore every county. Do not run global score or blanket retry commands. Record before/after counts, attempt totals and review reasons. Remaining deferred leads require a separately bounded continuation within the authorization and budget.

## 5. Complete human review

Use aggregate summaries in chat. The operator reviews detailed records locally and resolves missing ownership, multiple matches, entities and low-confidence matches against permitted evidence. Missing contact data requires authorized real skip trace or verified manual research; never fabricate or paste synthetic contacts into production.

Approve/reject/paste/retry only the specific leads the operator selects and authorizes, with useful local notes. Do not lower confidence thresholds or mass-approve the queue. `review retry` is for investigated error/dead_letter leads, not a way to re-enrich needs_review records. Any broader re-enrichment workflow should be specified separately.

Completion evidence: approved/rejected/unresolved counts and reason categories, verified ownership/contact quality. Export only explicitly approved real records to a private local file if the operator requests it. No email, calls or outreach is authorized by this prompt.

## 6. Coordinate writers and decide whether to schedule

An external Dallas update occurred during the earlier preflight. Identify current scripts, terminal processes, agents, cron/launchd entries and other checkouts using this database. Report process/job identifiers without exposing command-line secrets. Do not kill jobs or remove schedules without authorization.

All writers must use the updated code or explicitly coordinate with its database lock. The new Postgres lock covers cooperating `db_session` callers across checkouts/hosts; older code, bare connections and manual SQL can ignore advisory locks. It does not identify the historical writer or provide a billing budget. Do not disable it to make an overlapping command succeed.

`scripts/run_daily.sh` now runs `--all-enabled --since 7d --max-enrich 5 --append-only`. It preserves existing rows, but deferred existing leads will not be resumed on later daily invocations. It also still requires authentic Dallas input and a separately controlled paid budget when keyed. Do not present this as a fully unattended backlog processor.

Before proposing a schedule, obtain decisions on cadence, source freshness, backlog ownership, monitoring/nonzero-exit response, backups and runtime-data storage/retention. Prepare a concrete schedule configuration for review. Install/activate only if the operator explicitly authorizes scheduling; do not relocate iCloud data or invent a retention policy.

## Final deliverable

Update `artifacts/runtime/OPERATOR_COMPLETION_REPORT.md` with verified baseline/final counts by county, commands and exits, source provenance, existing-row changes authorized and performed, provider calls/charges versus the approved ceiling, review outcomes, and explicit readiness verdict.

For each unfinished item state: what is blocked; why operator action is required; exact local file/URL/action; evidence that will prove completion; which subsequent steps depend on it. Continue independent work while an item is blocked. Do not claim dialer-ready or scheduled-ready until the relevant requirements are actually satisfied.

END OPERATOR COMPLETION PROMPT
