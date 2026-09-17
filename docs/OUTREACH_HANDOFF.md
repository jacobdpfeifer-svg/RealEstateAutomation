# Outreach handoff: queue → Gmail draft → human send

`leads/outreach.py` never sends email and never calls the Gmail API itself.
It only decides *whether* a draft is allowed to exist (compliance gates in
`leads/compliance.py`) and *what it says* (grounded in the lead's own DB
record), then stores it locally with `status='queued'`. Turning a queued
row into a real Gmail draft is a separate step, done by a human or by a
Claude Code session with Gmail access — never a standing auto-send job.

## One-time setup

Add to `config/secrets.env` (never commit real values):

```
OUTREACH_SENDER_NAME=Your Company or Name
OUTREACH_SENDER_ADDRESS=123 Main St, City, ST 00000
OUTREACH_FROM_EMAIL=you@yourdomain.com
```

`leads outreach queue` refuses to draft anything until all three are set —
CAN-SPAM requires truthful sender identification and a physical mailing
address in every message, not just in ones you eventually send.

## Loop

1. **Queue drafts** for approved leads with a verified contact email:

   ```bash
   python3 -m leads outreach queue
   python3 -m leads outreach queue --county-fips 48201   # one county only
   ```

   Skips: no email, an address on the suppression list, a lead already
   drafted, and — unless `--override-high-risk-state` is passed — leads in
   a state on the re-verify list (KY, MD, OK, NC, SC, PA, IL by default;
   extend via `OUTREACH_HIGH_RISK_STATES`). Re-verify that state's
   wholesaling/broker rules before overriding.

2. **Review the digest** of what's queued, locally:

   ```bash
   python3 -m leads outreach list --status queued
   ```

   Each row has `to_email`, `subject`, `body`, `lead_id`, `case_id` — read
   every one before it becomes a real draft. Edit content by rejecting the
   underlying lead and re-approving with a note, or by pasting a corrected
   contact, rather than hand-editing the row.

3. **Create the real Gmail drafts.** In a Claude Code session with Gmail
   MCP access authorized to your account, for each queued row: call
   `Gmail.create_draft` with that `to_email`/`subject`/`body`, then run

   ```bash
   python3 -m leads outreach mark-drafted <draft_id> --gmail-draft-id <gmail id>
   ```

   so a later `queue` run doesn't duplicate it. This is the only place a
   Gmail draft gets created — nothing in this repository holds Gmail
   credentials or calls the Gmail API on its own.

4. **Send from Gmail, per message, yourself.** No tool in this pipeline
   sends email. Opening the draft and clicking send is the explicit
   per-message human action the plan requires.

5. **Honor opt-outs.** When someone replies asking to stop:

   ```bash
   python3 -m leads outreach opt-out someone@example.com --note "asked to stop 2026-09-17"
   ```

   Future `queue` runs skip that address. Suppression is enforced before a
   draft is ever built, not just before a send.

## What this does not do yet

- No reply-drafting (§2's "draft a response on that thread" is not built).
- No TCPA/DNC screening — this loop is email-only; a phone-outreach channel
  needs that screening added before it ships.
- No buyer-side delivery — this is seller-lead outreach only, per the
  plan's phase order (§7.6: ship against sellers before buyer delivery).
