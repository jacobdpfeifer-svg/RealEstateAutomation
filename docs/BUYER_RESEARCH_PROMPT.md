# Build Prompt: Deep Buyer-Side Research + Contact-Enrichment Repair

## Context for the agent picking this up

You're working in `re-tax-leads` (this repo). It finds property tax-lien /
tax-suit leads in Harris County (48201) and Dallas County (48113), Texas —
see `README.md` and `docs/PROJECT_PLAN.md` for the full architecture and the
phased marketplace roadmap. Two things are broken or missing right now, and
both are load-bearing for the business:

1. **Contact enrichment is not actually returning contacts.** The pipeline
   ingests suits and matches owners, but skip-trace enrichment
   (`providers/skip_trace/batchdata.py`, wired through
   `providers/skip_trace/__init__.py`) is not producing phone/email data end
   to end. Per `README.md`'s last validation note: "stub skip-trace, no
   contacts. Set `BATCHDATA_API_KEY` before treating this as daily-ready."
   Without verified owner contact info, the seller side of the pipeline is
   not sellable or actionable.
2. **The buyer side does not exist yet.** `docs/PROJECT_PLAN.md` §1 and the
   phased plan (§ "Phases") call for a buyer pipeline — recorder/deed
   adapters that surface all-cash and repeat/LLC buyers — plus a matching
   layer and delivery mechanism. None of `leads/buyers.py`,
   `leads/matching.py`, or `leads/delivery.py` exist yet (check with
   `ls leads/` before you start — this may have changed).

This prompt covers **both**: (A) get real owner contact data flowing, and
(B) do exhaustive, verified research to identify actual buyer-side demand —
investment firms, funds, iBuyers, wholesalers, private cash buyers — for
distressed/tax-delinquent residential property in Harris and Dallas
Counties, TX, and turn that research into a structured, reviewable dataset
plus an integration plan. **You are not authorized to contact any buyer,
sign up for any service, spend money, or represent this project to a third
party as part of this task** — deliverables are research artifacts and code
scaffolding for a human to review before anything goes out the door.

---

## Part A — Fix contact/email enrichment (do this first; it blocks everything downstream)

The seller-lead pipeline is worthless without verified contact info. Before
spending time on buyer research, diagnose and fix why `BatchDataSkipTraceProvider`
isn't returning usable contacts.

1. Read `providers/skip_trace/batchdata.py`, `providers/skip_trace/stub.py`,
   `providers/skip_trace/base.py` (adapters/base.py `SkipTraceProvider`), and
   `leads/pipeline.py` for how enrichment is invoked and how `ContactRecord`
   is persisted (`leads/models.py`, `leads/db.py`).
2. Confirm `BATCHDATA_API_KEY` is actually set in the environment used for
   runs (`config/secrets.env` vs `config/secrets.env.example`) — a missing
   key silently falls back to the stub provider per
   `providers/skip_trace/__init__.py`, which explains "no contacts" without
   raising an error. Check `leads/secrets.py` for how the key is loaded.
3. Run `python3 -m leads validate -o artifacts/phase1_validation.json` (or
   the narrower `pipeline enrich-pending` command) with the real key set and
   inspect the raw BatchData v3 response shape against
   `BatchDataSkipTraceProvider._extract_persons` / whatever normalizes
   emails and phones — confirm the current parsing logic actually reads
   email fields out of the v3 payload (`persons[].emails`, or similar) and
   not just phone fields. This is the most likely bug: the code may only be
   wired for phone extraction.
4. Check BatchData's actual v3 API docs (their developer portal — fetch it
   live, don't guess field names) for the correct request/response schema
   for email results, since email may require a different request flag or
   a separate endpoint/tier than phone skip-trace.
5. If BatchData genuinely doesn't return usable emails for this data set,
   evaluate 2–3 alternative/supplementary skip-trace or contact-append
   vendors (e.g. TLOxp, IDI/TransUnion, Melissa Data, Reonomy, PropStream,
   TruePeopleSearch API-equivalents used by other RE lead shops) — compare
   coverage, cost per hit, TOS/permissible-purpose (FCRA/GLBA) constraints,
   and whether they're usable for skip-tracing property owners for
   direct-marketing purposes. Do not integrate a new vendor without
   flagging it to the user for approval — this task is diagnose + propose,
   with a small, safe fix applied if the bug is a clear parsing/config
   error in existing code.
6. Add/extend a test in `tests/` that exercises contact extraction against a
   realistic fixture BatchData response (mask/synthesize any real PII) so
   this regression is caught going forward.
7. Report clearly: was this a config issue (missing key), a parsing bug, or
   a genuine vendor coverage gap? What's the fix, and what's still open?

## Part B — Deep buyer-side research

### Objective

Build a vetted, structured list of real, currently-active buyers of
distressed/tax-delinquent residential property in the **Harris County (Houston)**
and **Dallas County (Dallas–Fort Worth)** Texas markets — the two counties
this pipeline currently covers (per `README.md` "Phase 1 geography"). Cover
the full spectrum of buyer types described by the project owner:

- **Institutional / SFR investment funds** buying at scale (e.g. firms that
  acquire single-family rentals in bulk — the "iBuyer" and SFR-fund
  category; DFW and Houston are both known target metros for this category
  historically — verify current activity, don't assume).
- **Private equity / real estate investment firms** with a distressed-asset
  or tax-lien-adjacent acquisition thesis.
- **Tax lien / tax deed investment funds** specifically (entities that buy
  tax liens/certificates or acquire at tax-sale, distinct from buying
  directly from a delinquent owner pre-sale).
- **Local and regional real estate wholesalers / cash-buyer networks** —
  the "we buy houses" operators and their buyer lists, and wholesaling
  meetup/association buyer pools in Houston and DFW.
- **iBuyers and instant-offer platforms** currently operating in these two
  metros (verify who is still active — this category has consolidated;
  don't assume 2021-era names are still buying).
- **High-volume individual/LLC repeat buyers** — the kind of signal the
  planned recorder/deed adapter (`docs/PROJECT_PLAN.md` §4) is meant to
  surface systematically later; for now, identify a few credible
  *sources* (county deed records, court-records aggregators, investor
  association member directories) that could feed that adapter, not just
  a handful of names by hand.

### Method

- Use live web research (search + fetch), not memory — buyer rosters,
  fund activity, and iBuyer market presence change fast and your training
  data is stale on this.
- For every candidate buyer/firm, capture:
  - Legal/trade name, and parent entity if a subsidiary
  - What they buy (property type, price band, condition — as-is/distressed
    vs. retail-ready), and geography within Harris/Dallas counties
  - Evidence of *current* activity in these markets (recent acquisitions,
    active licensing, a live buy-box page, recent press/filings) — flag
    and deprioritize anything you can't confirm is still active in 2026
  - How they source deals today (direct-to-seller marketing, wholesaler
    network, MLS, tax-sale, institutional data feeds) — this tells you
    whether a data/lead-match product is even relevant to them
  - Public contact path (business development / acquisitions email or
    form — never a personal cell scraped from somewhere sketchy) and a
    source URL/citation for every claim
  - A legitimacy read: real, licensed/registered where applicable
    (Texas Real Estate Commission for brokerages; SEC/state registration
    for funds where relevant), reviews/complaints (BBB, Reddit, RealBiz),
    signs of a bait-and-switch/education-upsell operation (a common scam
    pattern among "we buy houses" franchises — flag these explicitly).
- Deliberately include and label a spread across firm size/type so the
  human reviewer can pick a tier to start with, rather than only surfacing
  the biggest names.
- **Do not fabricate or infer contact details or buy-box terms** you
  can't source — an unverifiable entry is worse than a shorter, accurate
  list. Mark confidence per entry (verified / probable / unverified).

### Deliverables

1. **`artifacts/buyer_research/tx_buyer_candidates.csv`** (or `.json`,
   matching the shape `leads/buyers.py` will eventually want — check
   `leads/models.py` field-naming conventions used for `case_record` /
   `property_record` and mirror that style) with one row per candidate
   buyer and the fields above.
2. **`docs/BUYER_RESEARCH_FINDINGS.md`** — narrative summary: market
   overview per county, which buyer categories are actually reachable via
   a data/lead-match product (per `docs/PROJECT_PLAN.md`'s constraint that
   this business sells *matches*, never brokers deals — flag any buyer
   type where that distinction gets legally murky, e.g. a buyer that
   expects you to also handle assignment/contract work), red flags found,
   and a shortlist (5–10) the user should personally vet and approve
   before any outreach happens.
3. **A short proposal**, appended to `docs/PROJECT_PLAN.md` or as its own
   doc, for how this research feeds the still-unbuilt `leads/buyers.py` +
   `leads/matching.py` (§ Phase items 4–5 in the plan) — i.e. which of
   these buyers/sources could seed the buyer-profile table now by hand
   versus which require the recorder/deed adapter to detect
   systematically later. Do not start writing `leads/buyers.py` itself
   unless the research is done first and the user asks you to proceed to
   code.

### Hard constraints (do not cross these without the user explicitly signing off first)

- No outreach, signup, application, or payment to any third party.
- No scraping of private marketplaces/MLS or paywalled data — public
  records, public company/fund materials, and public web content only,
  consistent with `docs/PROJECT_PLAN.md` §"Legal/compliance guardrails".
- Flag, don't resolve, any state wholesaling/brokerage licensing question —
  `docs/PROJECT_PLAN.md` already calls out re-verifying per-state rules
  before outreach goes live.
- Treat every "legit-looking" buyer with real skepticism: the distressed
  real-estate space has a lot of lead-gen/franchise operations designed to
  extract fees from other investors, not from buying a property. Surface
  that risk in the findings doc rather than filtering it out silently.

### Definition of done

- Part A: root cause identified for missing contacts, fix applied or a
  clear escalation with options if it needs a new vendor/spend decision,
  and a regression test added.
- Part B: the CSV/JSON candidate list, the findings doc, and the
  buyer-pipeline integration proposal are written, committed, and pushed
  to this branch. Nothing is emailed, called, or signed up for. The user
  reviews the list before you (or anyone) proceeds to Phase B code or any
  outreach.
