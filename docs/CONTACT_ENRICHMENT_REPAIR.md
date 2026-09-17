# Contact enrichment diagnosis — 2026-09-17

**Root cause confirmed: no BatchData key is configured. Real owner contacts remain unvalidated.** The shell has no nonempty `BATCHDATA_API_KEY`, and the actual `config/secrets.env` value is empty. The example file is not loaded. No credentials, owner records, or raw provider responses were exported or committed.

`leads.cli.main()` loads secrets before running commands. `leads.secrets.load_secrets()` reads `config/secrets.env` without replacing existing shell variables; an explicitly empty shell variable can mask a populated file. That masking was not the cause in this session. `providers/skip_trace/__init__.py` is empty: the factory is in `providers/skip_trace/base.py`, called through `adapters.registry`. Missing keys produce a stderr warning and a stub provider, whose `trace()` deliberately returns an empty list. Ingestion success is therefore not contact-enrichment success.

## Repairs and evidence

- The pipeline stripped the full situs address to the street before tracing, losing ZIP information. It now passes the full address. The provider extracts a five-digit ZIP from a state/ZIP suffix, including ZIP+4, without treating a five-digit house number as a ZIP.
- `_best_email()` was already present and already read `results.persons[].emails[].email` (plus legacy aliases). The suspected complete absence of email extraction was not reproduced.
- Phone/email selection formerly inspected only array element zero and could turn `None` into the string `None`. It now skips empty/non-string entries and selects a later nonempty value. This is normalization, not email deliverability or right-party verification.
- The provider returned up to three people, but the pipeline persisted only the first. All returned contact candidates now persist; the lead still points at the first candidate. The current review/export surface uses that pointer, so alternate candidates need local human review before selection. This change does not merge one person's phone with another person's email.
- Whitespace-only credentials now count as missing.

`tests/test_contact_enrichment.py` exercises a synthetic BatchData response through the real provider, pipeline, SQLite persistence, lead pointer, human approval, and export. It verifies email-only results, later nonempty phones/emails, multiple people, ZIP+4, no repeated call on an already enriched lead, secrets precedence, and whitespace credentials. These are synthetic contract/flow tests, **not a captured v3 response or live vendor certification**. All 101 tests in the shared working tree passed on 2026-09-17. An additional isolated HEAD snapshot containing only this task's code/test changes passed all 95 tests; unrelated working-tree changes are not part of this commit. Production databases were not opened for enrichment or changed.

## Live documentation check and unresolved schema

Fetched the [BatchData developer portal](https://developer.batchdata.com/) and its [Property Skip Trace operation](https://developer.batchdata.com/docs/batchdata/batchdata-v1/operations/create-a-property-skip-trace) live using unauthenticated GETs. Embedded public documentation metadata resolves the operation to “Property Skip Trace” and says it returns emails and phones, with one person for v1 and up to three for v3, and up to 100 properties per request. The portal landing page also describes email discovery. [BatchData's official integration repository](https://github.com/batchdataco/batchdata-mcp-server) confirms that v3 requires team enablement and token permissions; a 403 may therefore indicate entitlement, not a parser defect.

The field-level operation body did not render in the available web reader. Public documentation API attempts returned 400/404; no interactive browser is available. Therefore the exact v3 input schema (`ownerName`, `apn`, `state`), email options/projection flags, result envelope and per-request error fields are **not verified**. No new request flags or guessed schema substitutions were introduced. The existing v3 endpoint remains in place. Vendor marketing examples are inconsistent about endpoint versions and are not a schema authority.

There was no real key, so there was no authenticated v3 request and no raw response to inspect. The explicit no-spend instruction takes precedence over the earlier suggestion to run a real skip trace. This is a documented escalation, not a claim that the live integration is repaired end to end.

## Validation run

Ran `.venv/bin/python3 -m leads validate --max-enrich 0 --db artifacts/contact_repair_validation.db -o artifacts/phase1_validation_contact_repair.json`.

The command exited 1 and reported `ready_for_daily: false`: missing BatchData key, no authentic Dallas captures, and a Harris connection error in the network-restricted shell. `--max-enrich 0` guarantees zero enrichment attempts. The ignored raw validation report and scratch SQLite database remain local; a redacted summary is committed at `artifacts/contact_enrichment/validation_summary.json`. The connection failure is not evidence that Harris itself is down. An additional ingest-only network run would not resolve the contact blocker.

## Options for the owner

1. **No-spend path:** continue manual ownership verification and use existing `review paste` for independently verified contacts. A populated contact field does not establish owner identity, permission, or outreach readiness.
2. **Existing BatchData account:** obtain its official v3 OpenAPI/specification and a vendor-provided redacted successful response, check email inclusion and account entitlements, and place the key locally in `config/secrets.env` (never in chat or Git). Confirm the run environment has no empty shell override. Confirm vendor pricing, permissible use and a dollar ceiling before authorizing a single live request. No signup or support message was sent in this task.
3. **Vendor decision:** if the current account cannot supply usable email data or enable v3, compare entitlement upgrade or alternative vendors using owner-approved pricing and sample accuracy requirements. There is no evidence yet that replacing BatchData is necessary.

For a later authorized pilot, use one independently confirmed owner/property in a fresh scratch database and `--max-enrich 1`. Inspect response keys and per-request statuses, save any raw response only to private ignored storage, and check that the same person's email/phone persists and appears in review/export. Distinguish no-match, provider rejection, parser failure, and unverified contact. Do not retry ambiguous paid POST timeouts until billing is checked.

Existing `needs_review` leads are excluded by `enrich_pending` (which selects `new`/`error`); simply adding a key will not repair the backlog. Keep it intact. Selective audited re-enrichment with an attempt/spend limit is a separate follow-up; do not mass-reset statuses. Buyer research can proceed independently, but neither contacts nor buyers are approved for delivery.
