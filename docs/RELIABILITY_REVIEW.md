# Reliability review — 2026-09-15

## Scope and evidence

Read all original source, scripts, configuration examples, tests and fixtures,
README, and project plan before web research. Did not read or modify
`config/secrets.env`, and did not open the production `leads.db`. Baseline was
21 passing unittest tests. Another session introduced probes, property metadata,
and PostgreSQL code during the review; those changes were preserved and integrated,
including fixing explicit scratch-DB selection so tests cannot use DATABASE_URL.
This review adds no buyer, matching, marketplace, or outreach functionality.
Final metadata checks showed production DB/secrets modification times changed
during the concurrent session; this review did not perform those writes or inspect
their contents. The statement above describes this review's actions, not a claim
that other running work left the files unchanged.

## A. Implemented now

### HTTP access, retries, and source changes

Originally every network call had a timeout, but ArcGIS ignored HTTP status/API
errors, Harris could parse an HTML error as an empty TSV, and DCAD retried 403s
then returned no matches. There was no shared pacing. Added `SourceSession` with
per-host pacing across adapter instances, a truthful configurable user agent,
bounded GET/HEAD retries with exponential backoff and jitter, and support for
both forms of Retry-After. Long cooldowns stop the run's access to that host
instead of retrying too early. Denied hosts are blocked for the process.
POST is not replayed, including ASP.NET forms and BatchData charges.
These are deliberately small equivalents of documented
[urllib3 retry controls](https://urllib3.readthedocs.io/en/latest/reference/urllib3.util.html)
and [Scrapy throttling principles](https://docs.scrapy.org/en/2.12/topics/autothrottle.html),
without importing a crawler framework. The explicit loop lets pacing, POST safety,
and long-cooldown handling remain visible together; it adds no dependency.

Added required-field/response checks for Harris TSV and listing, DCAD forms and
results, live Odyssey results, ArcGIS features/errors/truncation, and BatchData
results. Recognized empty results remain valid. Unrecognized HTML, an access
challenge, or a changed JSON envelope now fails visibly. ArcGIS truncated results
are errors rather than being scored as a complete candidate set. DCAD account
fallback applies to 404, not an arbitrary access failure. Escaped APN literals in
ArcGIS queries and URL-encoded DCAD account IDs. Fixed Harris street formatting
and ensured skip-trace state comes from county configuration, never a ZIP token.
Connected Harris's previously unused `clerk_bulk_url` configuration.

Dallas now defaults explicitly to saved HTML: merely adding a CAPTCHA key no
longer turns on live access. Existing out-of-window captures can return zero cases
without incorrectly claiming missing fixtures. Legacy solver code remains behind
explicit live mode; it is not an endorsement of its use. Prefer a county-provided
feed or permitted manual capture. No portal automation permission was established
by this research, and neither robots.txt nor a solved CAPTCHA would establish it.

### Pipeline reliability and SQLite

Kept case/property unique keys and parameterized record SQL. Ingest now reports
actual newly inserted cases and persists before enrichment. Each lead gets a
savepoint; exceptions undo partial property/contact writes before recording a
sanitized failure, and successful work is committed per lead. Re-running completed
leads does not enrich them again. Three failed attempts by default move a lead to
`dead_letter`; `review retry ID` explicitly resets it and records an outcome event.
No match/no contact gets a useful review reason instead of silently becoming an
enriched lead with no way to call it.

Added transactional SQLite migration version 1 using `PRAGMA user_version`,
including adoption of the original unversioned schema and preservation of the
concurrent property metadata columns. Newer schemas are rejected. See SQLite's
[application version field](https://www.sqlite.org/pragma.html#pragma_user_version)
and [savepoint semantics](https://www.sqlite.org/lang_savepoint.html).
Connection lock wait is 30 seconds. Local pipeline commands use a process lock
that releases on exit/crash. CLI county failures no longer exit zero; one county's
failure still permits the other county to run. Dry-run initializes only an
in-memory database. Cron now performs enrichment/scoring once, using its venv.

This is resumable local processing, not exactly-once external billing. A crash
between receiving a paid response and committing can still cause a later paid
repeat. No vendor idempotency guarantee was verified. Investigate ambiguous
failures before requeueing. Long HTTP calls still happen inside a lead's DB
transaction; keep one operator/writer until separate stage checkpoints are needed.

### PII, secrets, and operational visibility

Verified the original `.gitignore` excluded production `leads.db`,
`config/secrets.env`, `artifacts/raw/`, and `exports/`; they were not tracked.
Added SQLite WAL/SHM/journal/backup patterns, local locks, validation DB/reports,
logs, and secret-file backup patterns. Disabled routine raw Harris retention:
`LEADS_SAVE_RAW=1` explicitly opts in, and saves full content rather than an
unreplayable 500 KB truncation. Artifact/CSV writers use mode 0600. CLI and cron
use umask 077. Existing data/files are not purged or relocated.

Operational logs contain counts, timings, internal IDs, and error types/stages,
not names, phone numbers, request URLs/bodies, or credentials. Per-lead failures
also reach the existing fetch log; actual Harris fetch status/artifact metadata
replaces fabricated per-case HTTP-200 entries. Added JSON events using stdlib
logging, consistent with Python's
[structured logging examples](https://docs.python.org/3/howto/logging-cookbook.html#implementing-structured-logging).
No APM service is needed. Operator action remains necessary to monitor nonzero
cron exit, a missing run, unusual zero counts, and sustained backlog growth.

Environment variables now win even when deliberately empty. Secrets are loaded
at CLI startup instead of module import (tests/imports do not read credentials).
Kept the small literal KEY=VALUE loader; supported syntax is intentionally simpler
than a shell. For multiline values, expansion, or standard dotenv syntax, replace
it with [python-dotenv](https://pypi.org/project/python-dotenv/), which likewise
preserves existing variables by default.

### Tests and dependencies

Added offline HTTP/form contracts and adversarial responses, bounded retry/pacing
checks, transaction rollback/replay/requeue tests, migration adoption, local-lock
checks, CLI failure exit tests, and a dry-run sentinel DB test. The full combined
suite has 58 tests. Recorded payload structure plus request-field assertions cover
more useful behavior than the previous mostly-positive parser assertions.

Pinned direct dependencies and urllib3, with compatibility markers for the
existing Python 3.9 environment. Tested a clean Python 3.12/OpenSSL environment
with Requests 2.34.2, RapidFuzz 3.14.6, urllib3 2.7.0, and psycopg2-binary 2.9.13;
`pip check` passes. Current release evidence:
[Requests changelog](https://docs.python-requests.org/en/latest/community/updates/),
[RapidFuzz release](https://pypi.org/project/RapidFuzz/3.14.6/),
[urllib3 release](https://pypi.org/project/urllib3/),
[Psycopg package](https://pypi.org/project/psycopg2-binary/), and
[tomli release](https://pypi.org/project/tomli/2.4.1/).
These are not a complete transitive, hash-locked environment; update and retest
pins deliberately. Python 3.9's legacy pins support existing offline tests, not a
recommendation to deploy that runtime. The system Python/venv here uses LibreSSL,
which emits urllib3's unsupported TLS-library warning; prefer Python 3.12+ OpenSSL.

## B. Larger changes / operator decisions

1. **Replace Dallas portal dependence with official products.** The repo's
   "no bulk civil datasets" statement was incorrect. Dallas advertises
   [civil/family bulk requests and subscriptions](https://www.dallascounty.org/services/);
   its [District Clerk report subscription](https://www.dallascounty.org/dcSubServicePaymentus/)
   sends civil index reports on Mondays. DCAD offers
   [current and certified appraisal downloads](https://www.dallascad.org/DataProducts.aspx).
   Decide on access, cost, cadence, tax-case coverage, licensing, and obtain sample
   schemas before building importers. A weekly report is not a daily feed. This
   improves phase 1 reliability without advancing marketplace phases.
2. **Define retention and storage policy.** Choose raw-capture lifetime, contact
   refresh/expiry, export lifetime, deletion handling, and backup retention. A
   provisional 30-day debug-capture lifetime could be considered, but it is not
   enforced or represented as a legal requirement. Follow the FTC's guidance to
   inventory PII, minimize collection, restrict access, and document retention in
   [Protecting Personal Information](https://www.ftc.gov/business-guidance/resources/protecting-personal-information-guide-business).
   This repo is inside iCloud Drive; Git exclusions do not disable sync, backups,
   sharing, or account access. Consider moving runtime data outside synced source
   folders and using encrypted storage. This review did not change that placement.
3. **Sanitize historical fixtures before public distribution.** Existing tracked
   county fixtures contain real-looking owner names, addresses, APNs and, in the
   full DCAD detail, exemption information irrelevant to matching. New tests use
   synthetic identities except when exercising existing recorded markup. Minimize
   and consistently pseudonymize the recorded fixtures; decide whether published
   Git history also needs remediation. No history rewrite or deletion was done.
4. **Resolve completeness and provenance limitations.** DCAD only searches the
   residential checkbox, tries name variants, and stops at the first nonempty
   page; it does not enumerate all pages. Odyssey's live parser uses nearby text
   windows and can miss fields or mis-associate adjacent cases. Mixed/unrecognized
   saved HTML can still be skipped by the permissive fixture loader. Add real,
   sanitized captures of pagination, legitimate zero results, and live Odyssey
   rows before relying on those paths for completeness. Bulk ingestion is the
   stronger fix. Harris daily *modification* files are not an unlimited filing
   archive; the filed-date filter and available listing bound historical coverage.
   A validated per-source watermark/replay manifest is a next step, not something
   to infer from MAX(retrieved_at).
5. **Separate paid enrichment checkpoints if volume grows.** Persist matching
   outcomes and a provider request identifier before/after enrichment, add an
   explicit uncertain-billing state, and confirm vendor idempotency semantics.
   This needs schema and operational design beyond a retry helper. Configure a
   spending ceiling with the provider before larger runs.
6. **PostgreSQL / multiple writers.** Preserved the concurrent session's backend;
   did not migrate or connect the production database. SQLite tests and mocked
   contracts do not establish PostgreSQL correctness. Test actual migrations,
   transaction recovery, placeholder translation, cursors, and backup/restore on
   a scratch PostgreSQL server before enabling it. A filesystem lock only covers
   processes sharing that file; remote writers require database locking/claims.
   Preserve project-plan phasing for buyer, matching, delivery, and outreach work.

## C. Checked and intentionally retained

- **Requests:** sensible for a sequential, deliberately slow batch job. Current
  releases remain maintained. HTTPX adds useful
  [async support](https://www.python-httpx.org/async/),
  [HTTP/2](https://www.python-httpx.org/http2/), and
  [default timeouts](https://www.python-httpx.org/advanced/timeouts/), but this repo
  already supplies timeouts and needs correctness/pacing rather than concurrency.
  No evidence supports a wholesale client migration here.
- **Parser libraries:** neither BeautifulSoup nor lxml was originally installed;
  adapters use regex plus stdlib CSV/JSON. Do not pretend a library swap validates
  site semantics. For a future Odyssey rewrite, an HTML DOM parser is justified;
  [Beautiful Soup documents parser differences](https://www.crummy.com/software/BeautifulSoup/bs4/doc/#installing-a-parser)
  and lxml's speed. Current fixes add contracts without a parser dependency.
- **unittest:** retained. [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
  is convenient, but migration alone does not add missing negative contracts.
  Subtests and mocks cover this suite's current size. VCR is useful once permitted
  captures exist; its [filtering hooks](https://vcrpy.readthedocs.io/en/latest/advanced.html)
  must remove Authorization, cookies, CAPTCHA tokens, query/body PII, and response
  PII. CI should replay with recording disabled; it should never pay for skip trace.
- **SQL and adapter architecture:** record values remain parameterized; dynamic
  query clauses and migration identifiers are internal code constants. `base.py`
  is a Protocol contract, so explicit inheritance is unnecessary. Shared the
  duplicated owner-name scorer and removed unused threshold attributes rather
  than adding an inheritance hierarchy. Most adapter/model APIs are annotated;
  connection/cursor typing and CLI dictionaries remain loose, especially in the
  concurrent PostgreSQL wrapper. No speculative global typing rewrite.
- **Config:** TOML for county settings and local environment credentials fit a
  solo operator. [Pydantic settings](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/)
  provides validation and multiple sources but is unnecessary for a few scalar
  settings. Adopt a secret manager when shared/cloud execution actually exists.
- **SQLite and ledger:** keep local SQLite, simple review, CSV delivery, and the
  arithmetic outcomes ledger as the normal workflow. Neither a queue service,
  web framework, automatic outreach, nor model-driven decisions solves the
  observed failures. No roadmap phases were advanced by this review.
