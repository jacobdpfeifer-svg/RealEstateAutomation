# How to use

Paste everything between **BEGIN AGENT PROMPT** and **END AGENT PROMPT** into a new Cursor agent chat. The agent must **rank tools and then design how to wire the winners into `re-tax-leads`**. Do not run live lookups against real owners, do not spend money, and do not merge provider code into production without explicit operator authorization after the report.

---

# BEGIN AGENT PROMPT

You are reviewing OSINT repositories and contact-intelligence tools to recommend the **best practical ways to find emails and other contact information for residential property owners** (homeowners) in the United States, especially Texas tax-suit lead workflows — **and the best ways to wire those tools into `re-tax-leads` so the program can actually use them**.

This is a **research + integration-design** assignment. Ranking without a wiring plan is incomplete. Do not treat this as a live skip-trace run or an unapproved production code merge.

## Mission

1. Inventory the curated OSINT lists and the tool catalog below.
2. Filter for tools that actually help with **person + property → email / phone / mailing contact**, not general cyber-intel theater.
3. Produce a ranked playbook: what to use first, what to use as pivots, what to skip, and what is legally or operationally unfit for this use case.
4. Map the best options to how `re-tax-leads` already works (assessor owner name + situs address + APN → skip trace → human review → CSV), including free/manual paths vs paid APIs.
5. **After** the shortlist is clear, design the **best wiring options** into this codebase: provider modules, waterfall/fallback order, CLI/manual helpers, config/secrets, spend caps, tests, and human-review handoffs.
6. Write a structured report the operator can act on. Do not invent accuracy claims.

## Context: what “homeowner contact” means here

Typical starting inputs (from assessor / tax-suit pipeline):

- Owner name (person or entity / LLC / trust)
- Situs / mailing address, city, state, ZIP
- APN / account number when available
- County and case metadata

Desired outputs (priority order):

1. Verified or strongly corroborated **email**
2. Verified or strongly corroborated **phone**
3. Alternate mailing address / identity pivots that unlock email or phone
4. Confidence notes and corroboration chain for human review

Out of scope for this review:

- Outreach copy, dialer scripts, or sending messages
- Scraping MLS, Zillow, or other private marketplaces
- Merging new provider code into the default production path without operator approval after the report
- Live lookups against real production owners or production `leads.db` / Postgres
- Breach passwords, credential stuffing, social-engineering, or account takeover

In scope (required): a concrete **wiring design** for the winning tools — file paths, interfaces, config keys, fallback order, and how results reach `ContactRecord` / `review paste` / export — even if code is not landed in this session.


## Required reading (repo)

Read enough to design real wiring; do not redesign ingest/assessor stages:

- `README.md` — pipeline stages
- `docs/PROJECT_PLAN.md` — Phase 1 scope
- `docs/CONTACT_ENRICHMENT_REPAIR.md` — current BatchData / contact state
- `adapters/base.py` — `SkipTraceProvider` protocol (`trace(name, address, city, state, apn) → list[ContactRecord]`)
- `providers/skip_trace/batchdata.py`, `providers/skip_trace/base.py`, `providers/skip_trace/stub.py` — current paid + stub + manual paste helpers
- `adapters/registry.py` — how county `skip_trace = [...]` resolves the first provider today
- `config/counties.toml` — per-county provider lists
- `config/secrets.env.example` — existing secret key pattern (do not open real secrets)
- `leads/pipeline.py` — when skip-trace runs, how contacts persist, `--max-enrich` behavior
- `leads/review.py` / CLI `review paste` — manual verified-contact path
- `leads/models.py` — `ContactRecord` fields the wiring must populate
- `tests/test_contact_enrichment.py` — contract expectations for a new provider

Assume BatchData Property Skip Trace remains the default primary automated provider until the operator chooses otherwise. Wiring designs should show how OSINT / secondary tools **complement** it: cheaper pivots, no-key fallbacks, validation passes, waterfall providers, and operator research checklists that feed `review paste`.

## Source lists to review

### GitHub aggregators (start here)

Fetch and synthesize these lists. Prefer tools that are maintained, documented, and usable for person/property contact discovery:

1. https://github.com/Cybersight-Security/OSINT-Toolkit
2. https://github.com/frangelbarrera/OSINT-BIBLE
3. https://github.com/Astrosp/Awesome-OSINT-List

Extract candidates for: people search, email discovery/verification, phone lookup, username pivoting, public records, reverse address, and domain/MX checks that support contact validation. Ignore pure CTF / malware / red-team noise unless it directly yields contact pivots.

### Curated catalog (evaluate every item)

For each tool below, classify:

- **Fit for homeowner contact?** (high / medium / low / none)
- **Input it needs** (email, phone, name, address, username, image, wallet, etc.)
- **Output it gives** (email, phone, profiles, breach hits, metadata, etc.)
- **Cost model** (free / freemium / paid / unknown)
- **Automation readiness** (manual browser / CLI / API / browser extension)
- **Compliance note** (public records OK, breach risk, ToS risk, face-search risk, etc.)
- **Verdict** (use in playbook / secondary pivot / research-only / avoid for this use case)

#### Email intelligence

- Hunter.io
- Epieos
- IntelX
- Have I Been Pwned
- DeHashed
- EmailRep
- VoilaNorbert
- Snov.io
- RocketReach
- Apollo.io
- MXToolbox
- Skymem
- Anymail Finder
- MailTester
- FullContact
- LeakCheck
- BreachDirectory
- That's Them
- PeopleDataLabs
- Gravatar

#### Username investigation

- Sherlock
- Maigret
- WhatsMyName
- Namechk
- KnowEm
- Blackbird
- Snoop
- Namecheckr
- Instant Username
- UserSearch
- Social Searcher
- GitHub Search
- GitLab Search
- Reddit Search
- Keybase
- Disqus
- Steam Community Search
- About.me
- Flickr Search
- Medium Search

#### Phone intelligence

- PhoneInfoga
- Truecaller
- NumVerify
- Twilio Lookup
- CarrierLookup
- IPQualityScore Phone Validator
- Whitepages
- FastPeopleSearch
- USPhoneBook
- That's Them
- Spokeo
- Intelius
- ZabaSearch
- NumLookup
- Nomorobo Lookup
- Telnyx Number Lookup
- Veriphone
- National Cellular Directory
- Sync.ME
- TruePeopleSearch

#### Image / visual verification

- Google Images
- Bing Visual Search
- Yandex Images
- TinEye
- PimEyes
- FotoForensics
- ExifTool
- Jeffrey's Image Metadata Viewer
- InVID Verification Plugin
- ImgOps
- Forensically
- Jimpl
- Reverse Image Search (Labnol)
- PhotoDNA (concept reference only)
- Karma Decay
- Search by Image (browser extension)
- Flickr API
- Wikimedia Commons
- OpenStreetMap Image Layers
- Mapillary

#### Blockchain

- Etherscan
- Blockchain.com Explorer
- Blockchair
- BTC.com Explorer
- Blockstream Explorer
- Mempool.space
- Ethplorer
- BitInfoCharts
- Tokenview
- BSCScan
- Polygonscan
- Arbiscan
- Tronscan
- Solscan
- Solana Explorer
- CardanoScan
- Litecoin Explorer
- XRPSCAN
- Bitcoin Visuals
- WalletExplorer

Also surface **additional high-value tools** found in the three GitHub lists that are missing above if they are better for name+address → contact (examples of categories to hunt: reverse address people-search, county appraisal extras, secretary-of-state business filings for LLC owners, probate/voter/public-record aggregators that are lawful to use). Cap “extras” at the top 15 by fitness.

## Evaluation criteria (use consistently)

Score each serious candidate 1–5 on:

1. **Residential fitness** — works from name/address/phone, not only corporate domains
2. **Email yield potential** — realistic path to an email, not just “email exists somewhere”
3. **Phone yield potential**
4. **Corroboration quality** — multi-source validation possible
5. **Cost / complexity** — cheap and operable by one operator
6. **Automation / API potential** — for a later `providers/skip_trace` candidate
7. **Compliance risk** — lower is better; call out breach, face biometrics, ToS scraping

Compute a short rationale, not a fake precision scorecard. Prefer qualitative ranking with evidence links.

## Required workflow

### Phase A — Inventory

1. Fetch the three GitHub aggregators and summarize what categories they cover that matter here.
2. Build a master candidate table from the curated catalog + top extras from the repos.
3. Drop or demote tools that cannot start from homeowner inputs without already knowing the email/username/wallet.

### Phase B — Ranked homeowner playbooks

Write practical sequences for these scenarios:

1. **Person owner, full situs address known, no email/phone yet**
2. **Person owner, phone found first, need email**
3. **Email found first, need validation + more phones**
4. **LLC / trust / estate on title** (business filings + registered agent pivots)
5. **Common name / multi-match** (how to corroborate before trusting a contact)
6. **No-spend / manual-only path** (browser + free tools + `review paste`)
7. **Paid enrichment path** (where BatchData sits vs alternatives like people-data / email APIs)

Each playbook must list ordered steps, expected artifacts, and stop conditions (“do not merge identities without X”).

### Phase C — Vendor shortlist for possible automation

Recommend at most **5** tools/vendors worth considering as:

- Primary automated skip-trace (compare to BatchData; do not claim replacement without evidence)
- Secondary enrichment (email-only or phone-only)
- Validation / reputation pass after a contact is found
- Manual research accelerators (no API required)

For each: inputs, outputs, pricing unknowns, integration difficulty, and what trial would prove value — without running a paid trial in this session.

### Phase D — Explicit avoid / caution list

Call out tools or categories that are a bad fit or high risk for this product, including:

- Breach databases used as “current email truth”
- Face-search / biometric lookup for marketing contacts
- Username tools with no property corroboration
- Blockchain explorers with no off-chain attribution
- Image forensics that do not yield contact data
- B2B SDR stacks that only guess `first.last@company.com`

### Phase E — Wire into `re-tax-leads` (required; do this after ranking)

Design how the **winning tools** should plug into this program so contact yield improves without breaking review, spend caps, or the stub/manual path. Prefer the smallest change that delivers value.

For each shortlisted tool, pick exactly one primary integration mode (and note secondary modes if useful):

| Mode | When to use | Where it lands in this repo |
|---|---|---|
| **A. Skip-trace provider** | Tool has a stable API and can start from name/address/APN | New module under `providers/skip_trace/`, register in `get_skip_trace_provider`, list in `config/counties.toml` |
| **B. Provider waterfall** | Multiple APIs; try BatchData then cheaper/fallback | Extend registry/pipeline so `skip_trace = ["batchdata", "other"]` means ordered attempts until contacts or exhaustion; respect `--max-enrich` / spend ceiling per attempt |
| **C. Post-contact validator** | Tool needs an email/phone already (HIBP-style, EmailRep, Twilio Lookup, MailTester) | Separate optional validate step after `trace()`, or flags on `ContactRecord` / review UI — do not pretend it discovers owners |
| **D. Manual research CLI / checklist** | Browser-only or ToS-fragile tools (FastPeopleSearch, That's Them, TruePeopleSearch, Sherlock) | Operator runbook + optional `leads review research-hints` (or docs) that emits search URLs from name/address; results enter via existing `review paste` |
| **E. Offline / local CLI helper** | Open-source CLIs (PhoneInfoga, Maigret) that should not hit production automatically | Optional script under `scripts/` writing to ignored artifacts; human pastes verified contacts |
| **F. Do not wire** | High risk, low residential fit, or no API | Leave in avoid list; explain why automation would hurt |

#### Wiring design requirements

For every tool in modes A–E, specify:

1. **Inputs mapping** — how pipeline fields (`owner name`, full situs address, city, state, ZIP, APN) map to the tool’s request
2. **Outputs mapping** — how tool fields map onto `ContactRecord` (name, phone, email, mailing address, source, confidence/notes)
3. **Secrets** — exact env var names to add to `config/secrets.env.example` (never invent real values)
4. **Config** — `counties.toml` / feature flags / enable-disable without code edits
5. **Spend & safety** — how `--max-enrich`, missing-key → stub fallback, no automatic paid POST replay, and dry-run behave
6. **Failure modes** — no-match vs auth error vs parse error vs rate limit; what the pipeline should record
7. **Tests** — synthetic contract tests modeled on `tests/test_contact_enrichment.py` (no live paid calls)
8. **Human review** — when the operator must confirm before export; how multi-person results are handled (pipeline already can persist multiple candidates)
9. **Implementation order** — smallest shippable slice first (e.g. manual URL hints before a second paid API)

#### Recommended target architecture (adapt if evidence says otherwise)

Propose one concrete end-state architecture, for example:

1. Keep **BatchData** as default primary `SkipTraceProvider` when key + spend ceiling exist.
2. Add **0–2 secondary providers** only if they clearly improve email/phone yield or cost.
3. Add an optional **validate** pass for phones/emails already found (carrier / deliverability / reputation) that never invents contacts.
4. Add a **manual OSINT assist** path: generate research URLs / checklist from lead fields → operator verifies → `review paste`.
5. Leave breach, face-search, username-blast, and blockchain tools out of automated wiring unless the operator explicitly opts in later.

If the current registry only uses `skip_trace[0]`, call that out and specify the minimal code change to support ordered fallbacks — or justify staying single-provider with manual paste for secondaries.

#### Optional code in this session

Default: **design only** in the report.

If — and only if — the operator’s chat message also says `IMPLEMENT WIRING NOW`, you may implement the **smallest** approved slice from your plan (prefer mode D checklist/docs or a stubbed provider skeleton with tests and disabled-by-default config). Still no live paid calls, no production DB writes, no real owner PII in tests.

## Deliverable

Write `artifacts/runtime/OSINT_HOMEOWNER_CONTACT_REVIEW.md` with:

1. **Executive verdict** (10–15 lines): best overall approach for homeowner email/phone discovery **and** how it should be wired into this program
2. **Top 10 tools** for this use case, ranked, with one-line why
3. **Playbooks** for the seven scenarios above
4. **Full evaluation table** for the curated catalog (can be compact: tool | fit | best input | best output | cost | verdict)
5. **GitHub-list extras** worth adding
6. **Automation shortlist** (≤5) vs keep-manual
7. **Wiring blueprint** (Phase E) — required:
   - Target architecture diagram or ordered bullet flow (ingest → assessor → enrich waterfall → validate → review → export)
   - Per-tool integration mode (A–F) table
   - Proposed files/modules/config/secrets keys
   - Fallback / spend-cap behavior
   - Test plan
   - Phased build order (Ship 1 / Ship 2 / Ship 3) with effort and dependency notes
8. **Avoid / caution**
9. **Open questions for the operator** (budget, ToS comfort, LLC research, breach pivots, whether to authorize `IMPLEMENT WIRING NOW`)
10. **Suggested next session** — copy-paste implementation prompt for Ship 1 only, ready to run after approval. Do not implement Ship 1 unless explicitly authorized.

Keep owner PII out of the report. Use fictional examples like `Jane Q Public / 123 Main St, Houston, TX 77002` if you need illustrations.

## Done means

- The markdown report exists and is specific enough to choose tools **and wire them** without re-reading the entire OSINT internet.
- Every curated catalog item has a verdict.
- Playbooks are ordered and actionable for a human researcher.
- Phase E wiring blueprint names concrete repo files, config keys, integration modes, fallback order, and a Ship 1 build plan.
- No live enrichment, no secrets, no production writes, and no new provider code unless the operator explicitly authorized implementation in this chat.

# END AGENT PROMPT
