# Launch review — September 17, 2026

**Verdict: usable for Harris filing research and manual review; not ready for owner outreach or buyer acquisition.** Passing tests and successful ingest do not establish that a prospect has a verified owner/contact. The immediate target is a small owner-first Harris pilot, followed by buyer outreach.

## Verified current inventory

Read directly from production PostgreSQL `re_tax_leads` in a read-only transaction. No production records were changed during this review.

| County | Leads | With property match | With contact | Approved with contact | Resumable new/error |
|---|---:|---:|---:|---:|---:|
| Harris | 24 | 8 | 0 | 0 | 0 |
| Dallas | 2 | 2 | 0 | 0 | 0 |

All 26 leads are `needs_review`. There are 26 cases, 10 properties and zero contact records. The two Dallas source paths identify bundled sample HTML; they are not prospects. Harris has 16 no-owner-match reviews and eight no-contact reviews. Some matches also have ambiguity, entity or low-confidence flags.

`BATCHDATA_API_KEY` is absent. Dallas live automation is disabled. These are presence/gate checks; no credentials are included in the report. Read-only snapshot: `artifacts/runtime/LAUNCH_READINESS.json`. Private research sheet: `artifacts/runtime/OWNER_REVIEW.md` (eight Harris property-matched candidates, source links and verification notes; mode 0600).

## What the overnight work actually added

| Change group | Practical effect | What it does not prove |
|---|---|---|
| Postgres support and session locks (`7284304`, `9d31fca`) | A shared store and coordination between cooperating application sessions | Other SQL clients/older checkouts can still ignore the lock |
| Reliability and privacy (`85726a5`, `2ee95d2`) | Incremental persistence, error handling, private output files, safer default Dallas access | Court input authenticity or contact accuracy |
| Bounded enrichment and review fixes (`9bc6d4e`) | Caps attempts, reports no-match results, prevents missing-ID review success | A dollar spending limit or validated paid contact enrichment |
| Expansion probes (`df9044a`) | Better field mapping and portal diagnostics for three disabled counties | Working court pipelines in those counties |
| Append-only runs and aggregate review (`9d31fca`) | Preserves existing rows; exposes review reasons without identity fields | Progress through the existing review backlog |

The historical full-run report's opening says both counties ran against real sources, but its detailed Dallas section explicitly describes sample input. The detailed evidence governs. That run processed zero enrichment attempts, so it did not test the contact provider at all. Older reports contain historical counts and script descriptions; use the current code and a fresh `leads readiness` report.

## Defects fixed in this review

1. **Sample Dallas input could be ingested by normal CLI runs.** The default is now `artifacts/raw/dallas/clerk/captures/`, separate from the old sample. CLI adapter construction rejects bundled sample content markers even after renaming or when an environment override selects the sample directory. Explicit constructor fixtures remain available to offline adapter tests. Existing production sample rows were preserved and remain excluded from the Harris shortlist.
2. **One valid saved page could hide another failed page.** Every saved HTML page now has to parse as results or a legitimate empty-result message. An access-challenge page fails even if it includes an empty-result phrase. Court and assessor HTML must be kept in separate input directories.
3. **Adjacent cases could contaminate each other's fields.** The Odyssey parser previously searched a fixed 2,500-character window that could include the next case's date/defendant. It now stops at the next case link. This fixes the reproduced error; authentic multi-page captures are still needed to establish real parser coverage.
4. **Readiness required reconciling stale reports.** New `.venv/bin/python3 -m leads readiness` returns current county/property/contact/review counts and sample-path indicators, without migrations, provider calls or database writes. A missing SQLite database is not created. Exit 0 means inspection succeeded; this command does not grant outreach clearance or certify source freshness.

README and PROJECT_PLAN now distinguish implemented research functions from future buyer/outreach work. The plan also removes an unsupported blanket assertion that selling matches automatically establishes the business's legal classification.

## Today's execution sequence

1. **Work the eight Harris property matches first.** Open the private owner review sheet. Check the court source and exact parcel/owner match. Resolve multiple matches, entity ownership and weak matches before adding a contact. The other 16 Harris records need owner/parcel research first.
2. **Obtain verified contacts.** Manual contact entry already works using `leads review paste`. For paid enrichment, configure the key locally in `config/secrets.env` and establish vendor pricing and a spending ceiling. Do not put keys in chat. The paid provider integration has only mocked validation here.
3. **Do not expect another run to clear this backlog.** All current leads are `needs_review`; `enrich-pending` selects only `new`/`error`, and `review retry` only accepts `error`/`dead_letter`. A selective paid contact workflow for reviewed ownership has not been built. Avoid blanket status resets or paying to trace unresolved owner matches. The manual verified-contact route can advance individual leads today.
4. **Review specific completed records, then export the approved pilot.** Approval and CSV export exist; approval currently does not enforce a verified ownership/contact check. The operator must inspect those fields. Approved status alone is not evidence of permission to call or email.
5. **Build owner outreach, then buyer outreach.** There is no `leads/outreach.py`, suppression list, draft queue or reply processor, and no buyer discovery/matching/delivery implementation. A production owner drafting workflow needs sender details, a selected channel, suppression/opt-out handling, source-grounded messages and human review. Buyer pipeline work follows the owner pilot. Those missing systems cannot be replaced by simply adding a Gmail connection.

Dallas can wait while Harris is worked. Before using Dallas, obtain authentic rendered results with query/window/time/page provenance in the new capture directory or a dedicated `DALLAS_CLERK_FIXTURE_DIR`, then validate against the visible source. The daily script still selects all enabled counties and will fail its Dallas stage until this input exists; use an explicit Harris command for Harris-only work. Append-only daily mode also does not resume deferred existing leads. No scheduler was installed or changed, and scheduling/monitoring readiness was not certified.

## Validation and limits

- Baseline: 92 tests passed. After fixes: **98 tests passed**, including cross-case contamination, mixed valid/failed pages, renamed sample rejection, private aggregate reporting and read-only/missing-database behavior. Log: `artifacts/runtime/launch_tests.log`.
- `pip check`: no broken requirements. `git diff --check`: clean.
- Fresh Harris 7-day dry run: **exit 0, 49 source appearances**, zero persisted rows, no enrichment. These are appearances across source files, not 49 unique new prospects. Logs: `artifacts/runtime/launch_harris_dry_run.*`.
- Dallas dry run without authentic captures: **exit 1**, as intended. No portal requests or sample ingest. Logs: `artifacts/runtime/launch_dallas_dry_run.*`.
- The new readiness query also ran successfully against the actual Postgres backend using a read-only snapshot. Its SQL was exercised live; the complete test suite is offline, not a full live Postgres integration suite.
- No paid provider requests, contact additions, lead approvals, sends or production writes were made. Changes are local and uncommitted. Full live contact enrichment, authentic Dallas parsing, message delivery, backups and unattended operation remain unverified.
