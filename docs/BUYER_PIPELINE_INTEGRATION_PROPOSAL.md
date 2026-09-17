# Buyer pipeline integration proposal — review only

This proposal supports Project Plan phases 4–5 using the [buyer research dataset](../artifacts/buyer_research/tx_buyer_candidates.json). It does not change the current phase order or authorize implementation, acquisition of data, buyer outreach or delivery. `leads/buyers.py`, `leads/matching.py` and `leads/delivery.py` remain unbuilt. Seller contact readiness is separately documented in [the enrichment repair report](CONTACT_ENRICHMENT_REPAIR.md).

## Hand-seeded profiles and future deed discovery

After human approval, the eight shortlisted business identities could seed a **research-stage** buyer-profile table by hand. The remaining records should be retained as holds, channels, competitors or institutional watchlist entries. Research-stage presence must not make a record matchable or deliverable. HomeVestors and WeBuyHouses.com need a specific local operator; institutional brands need an exact purchasing entity and mandate. Do not create one principal cash-buyer profile for an entire franchise network, brokerage or property manager.

Web research can establish business identity, a public business contact path, advertised territory and some criteria. Recorder data is needed to discover less visible private/LLC purchasers systematically and measure observed repeat activity. Recent deeds could later corroborate named firms through documented aliases, but a similar name, common registered agent or shared mailing address is insufficient to merge entities. A holding LLC and a management/adviser/brand company should be linked with dated evidence, not collapsed.

## Proposed data contract

Keep the repository's snake_case and `county_fips`, `source_url`, `retrieved_at` conventions from `CaseRecord` and `PropertyRecord`. The research JSON is an input artifact, not a migration or a finished ORM shape. A later importer should validate and translate it explicitly.

| Proposed record | Core information and behavior |
| --- | --- |
| `buyer_record` | Internal `id`, stable research `buyer_id`, legal name, display name, buyer type, principal/broker/channel role, entity aliases with evidence, human review status and approval metadata. |
| `buyer_criterion` | Verified county/ZIP coverage, property types, condition and title-stage acceptance, price bounds with currency and **price basis**, evidence source and effective date. Unknown values stay null; research target counties must not become approved coverage. |
| `buyer_contact` | Public business channel, source, role, identity confidence, permission/suppression/review status. Vendor procurement authority is distinct from seller intake, investor relations or media relations. |
| `buyer_evidence` | Claim, source URL, retrieval/publication/effective dates where known, company statement versus independent record versus inference, verification scope, reviewer and expiry/recheck date. Keep licensing records tied to their exact entities. |
| `buyer_transaction` | County and instrument ID, recording and execution dates, grantee/grantor raw and normalized names, linked parcels, deed type, consideration if actually disclosed, related financing instruments and evidence completeness. |
| `buyer_match` | Seller lead ID and buyer ID, criterion/evidence version, matching version, reasons, missing fields, exclusion reasons and review decision. Store a snapshot sufficient to reproduce the recommendation. |

Do not turn an assessor's `total_value` into a sale price or a buyer's spending limit. Texas is a non-disclosure state; public instruments may not disclose usable consideration. Preserve price provenance and leave it unknown when necessary. An advertised resale/inventory price and an iBuyer valuation ceiling are also different from a purchase budget. [Texas Comptroller appraisal review discussing non-disclosure](https://comptroller.texas.gov/taxes/property-tax/docs/appraisal/irion.pdf).

## First recorder adapter: Harris, subject to a source-access probe

Harris is the proposed first implementation because the seller pilot already centers there and its Clerk identifies search and bulk/FTP channels. This is a sequencing judgment, not a claim that bulk access is free or technically proven. Prefer a documented bulk/API contract over automated portal interaction, as required by the Project Plan.

| County | Verified public entry points | What remains to establish before code |
| --- | --- | --- |
| Harris 48201 | [Clerk real-property search](https://www.cclerk.hctx.net/Applications/WebSearch/RP.aspx), [public records directory](https://www.cclerk.hctx.net/PublicRecords.aspx). Search supports instrument/party/date/legal-description fields; Clerk advertises bulk/FTP data sales. | Access terms, cost, public-use rights, schema/sample, historical depth, incremental delivery, corrections, parcel linkage and financing coverage. Public bulk contact is a future approval-dependent path; no request was sent. |
| Dallas 48113 | [Recording office](https://www.dallascounty.org/government/county-clerk/recording/), [county search directory](https://dallascounty.org/services/record-search/) linking [PublicSearch](https://dallas.tx.publicsearch.us/), [County Clerk bulk request form](https://www.dallascounty.org/Assets/uploads/docs/county-clerk/County%20Clerk%20PIA%20Request%20Form.pdf). | Same checks; do not substitute the district-court civil feed or DCAD ownership records for recorded deeds/financing. No private platform scraping or application is authorized. |

The public Harris search warns of a recording-to-search lag. A future probe must measure completeness and late corrections before declaring daily coverage. If the authorized public data is insufficient, document the gap and present a costed source decision rather than guessing fields or bypassing access controls.

## Cash and repeat-buyer evidence

The Project Plan's shorthand “no mortgage instrument recorded” needs a conservative implementation. It should produce **possible cash acquisition**, not verified cash. An unmatched mortgage can result from recording delay, incomplete access, another grantee name, parcel-link failures, blanket financing or financing recorded elsewhere. An LLC name alone proves neither cash nor investment intent.

Proposed initial rules for later evaluation:

1. Deduplicate by `(county_fips, instrument_id)`; preserve revisions and execution/recording dates. A multi-parcel deed is one transaction with several linked parcels, not several repeat purchases.
2. Classify deed types and flag corrections, family/related-party transfers, trust changes, quitclaims and foreclosure/tax deeds for separate review. Do not label all ownership transfers arm's-length purchases.
3. Link financing by documented party aliases, parcel/legal description, instrument references and time. Trial a closing-date window through 30 days after recording, with historical context for existing/blanket liens. This is a proposed research parameter to calibrate, not an established legal or statistical threshold.
4. Keep financing state explicit: `observed`, `not_observed_in_complete_window`, or `unknown_incomplete_coverage`. The second state is only a cash proxy; missing data must remain the third. Retain source/window/cutoff and allow later records to revise the inference.
5. Compute distinct eligible purchases over proposed 90/180/365-day windows. Two purchases could nominate a repeat-buyer candidate; the threshold is tunable and does not establish current appetite. Present last purchase date, transaction count, observed property types and identity uncertainty alongside it.
6. Verify a sample against the full official instruments before accepting either classification. No verified cash or repeat-purchase classifications were produced during this task.

## Matching and delivery gates

First implement explainable filtering after separate authorization; do not train weights on these research labels. Require a human-reviewed buyer role and current geographic criteria, normalize county/APN and property type, and evaluate condition/title stage separately from the existence of a tax suit. An unavailable criterion is a review gap, not a positive match. Reject known exclusions; route unknown price, condition or coverage to review. A tax delinquency balance is neither asking price nor equity.

Keep seller ownership/right-party checks separate from email/phone availability. The repaired pipeline stores contact candidates; it does not prove contact identity, deliverability or permission. A match recommendation can be evaluated internally with a property ID and redacted reasons, but seller contact data must not enter a buyer-facing export merely because a provider returned it.

For the first later delivery milestone, generate a **local review package** with evidence, missing fields, proposed recipient and exclusions. Define the actual product/compensation terms and have the state brokerage/wholesaling questions reviewed before offering it. Require a human approval tied to the destination and package version before any external delivery; no remote draft or message is created by this research task. Changes to recipient, included fields or evidence should invalidate the approval. Keep rights/terms review and suppression checks explicit rather than claiming public availability authorizes every resale/use.

Use the existing append-only outcomes ledger and its `matching_version`: `match_accepted` and `match_declined` already exist. Add a reviewed vocabulary for research approval and actual data-product purchases later if needed; do not reuse `deal_closed_won` to imply this project brokered a house sale. Sending, opening, approving research and paying for data are distinct outcomes. No ledger events or buyer database records were created here.

## Acceptance criteria for a separately authorized build

- A reviewed import preserves all source links, null semantics and false outreach approval, and rejects duplicate IDs or unsupported confidence labels. Institutional/channel profiles cannot silently become principal buyers.
- Labeled official-instrument samples in each implemented county measure identity, deed/financing linkage and repeat-count errors. Report sample size and observed precision; incomplete recording windows must produce unknown, not cash.
- Matching examples demonstrate county versus metro differences, property exclusions, unknown price and unresolved title/contact data. Every result has reproducible reasons and no automatic contact delivery.
- Human review selects the initial buyer category and approves any future discovery. Only that step can establish procurement authority, existing data sources, desired freshness/exclusivity, actual criteria and willingness to pay. No assumptions about subscription revenue should be encoded now.
- A later pilot records accepted/declined matches and actual data-product payment separately. Expand recorder coverage only after evidence shows usable detections and customer value; never treat a buyer's public form, BBB rating, AUM or historical holdings as that evidence.

The next authorized deliverable is the owner's review of the research and contact-enrichment options. Buyer implementation and outreach require a subsequent instruction.
