# Contact implementation audit — 2026-09-17

The implementation does not establish that homeowner contact discovery works.
`research-hints` builds URLs locally; it neither retrieves contacts nor verifies
identity, deliverability, or consent. Existing synthetic provider tests prove
local parsing/persistence behavior, not live provider accuracy or yield.

This review covers the supplied research, research-hints wiring, manual contact
storage, provider selection/parsing, and CSV export. No real-person lookups,
paid calls, production database access, or outreach were performed. Existing
uncommitted work from other agents was preserved.

## Fixed

1. **Read-only contract violation:** `cmd_review` previously used `db_session`
   for research hints, causing migrations even though the feature promised no
   writes. It now uses `read_only_session`, which does not create databases or
   migrate schemas. Both text and JSON CLI tests reject migration calls and
   socket connections.
2. **CSV formula injection:** untrusted names, contact fields, and review notes
   could become spreadsheet formulas. Export now quotes every field and prefixes
   dangerous text values with an apostrophe, including leading whitespace and
   full-width formula prefixes. Stored values remain unchanged. Tests cover all
   exported text columns, separator/newline breakout, and ordinary values.

CSV compatibility limitation: an international phone beginning with `+` is
exported with an apostrophe prefix. Downstream importers must account for this
text representation. Spreadsheet software may remove escaping on subsequent
save/reopen; this is an initial-export mitigation, not a universal guarantee.
See [OWASP CSV Injection](https://community.owasp.org/attacks/CSV_Injection).

## Remaining findings

- **High — contact confidence is not verification.** Manual input defaults to
  confidence 1.0 without validation. The provider defaults missing scores to
  0.8 and treats rank/score/confidence as interchangeable. These numbers do not
  establish a correct identity or a working contact channel.
- **High — provenance is incomplete.** `ContactRecord` records provider and
  retrieval time, but has no per-channel source, consent, suppression, or
  verification state. Manual paste does not record the original source. Approval
  is an operator status change, not proof of identity or permission to contact.
- **High — address association can be misleading.** The provider stores the
  submitted situs as the contact address and can substitute the requested name
  when the response lacks one. These are request values, not independently
  corroborated facts about the returned person.
- **Medium — selection loses information.** The provider keeps at most three
  candidates and one phone/email per person; the lead points to the first
  returned contact. No live accuracy or candidate-selection trial was performed.
- **Medium — address parsing is heuristic.** Comma layout and two-letter suffix
  assumptions can misinterpret partial addresses. The existing house-number
  regression covers a narrow case, not comprehensive address validation.
- **Scope mismatch:** configured provider lists still resolve only their first
  entry. The new hints feature adds no fallback provider, discovery API, or
  contact validator. Generated third-party deep links are unverified.

These findings are documented rather than extending personal-contact discovery.
A supported next direction is consent-based contact collection or handling
explicitly published business contact channels, with source and verification
states stored separately from identity confidence.

## Independent research corrections

- [Twilio Lookup v2](https://www.twilio.com/docs/lookup/v2-api) distinguishes
  validation, line type, caller name, identity match, and reassignment packages.
  Basic number validity or a line type is not equivalent to verified ownership.
  A VoIP classification alone does not establish an identity mismatch.
- [Hunter verification statuses](https://help.hunter.io/en/articles/6103281-what-are-the-possible-verification-statuses-for-an-email)
  distinguish valid, invalid, accept-all, and other outcomes. Accept-all results
  do not establish that a particular mailbox is deliverable. Domain/mail-server
  evidence should not be represented as homeowner identity verification.
- [OWASP](https://community.owasp.org/attacks/CSV_Injection) documents formula
  injection and the limitations of CSV escaping across spreadsheet consumers.
  Ordinary CSV quoting alone does not prevent formula evaluation.

Vendor prices and claimed residential yield in the supplied research were not
validated and should not be treated as current procurement evidence.

## Validation

Baseline: 106 tests passed. After changes: **109 tests passed** using:

```text
.venv/bin/python3 -W ignore::DeprecationWarning -m unittest discover -s tests
```

New security tests use fictional values and mocked rows. Research-hints CLI
tests use temporary SQLite databases. No live database, PostgreSQL integration,
spreadsheet application behavior, or homeowner contact accuracy was certified.
Existing `datetime.utcnow()` deprecation warnings remain.
