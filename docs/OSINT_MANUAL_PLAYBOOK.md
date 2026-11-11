# Manual OSINT playbook (Ship 1)

Operator runbook for no-spend homeowner contact research. This does **not** scrape people-search sites, call BatchData, or write contacts. Paste only after a human corroborates identity.

Full playbooks, stop conditions, and the avoid list live in [`artifacts/runtime/OSINT_HOMEOWNER_CONTACT_REVIEW.md`](../artifacts/runtime/OSINT_HOMEOWNER_CONTACT_REVIEW.md).

## Generate search URLs

```bash
python3 -m leads review research-hints LEAD_ID
python3 -m leads review research-hints LEAD_ID --json
```

Requires a lead with a `property_id` (same as `review paste`). The command prints labeled deep links from the assessor owner name + situs. It does not fetch those URLs.

Person owners get TruePeopleSearch, FastPeopleSearch, That’s Them, Whitepages (name), and a quoted Google query. Entity-ish names (LLC, LP, TRUST, INC, CORP, LTD, NA, HOA) also get OpenCorporates, Texas Comptroller, and SOSDirect landing pages.

## Research order

1. Confirm the assessor owner matches the defendant. Stop if match confidence is low or parcels collide.
2. Open the assessor page and copy **mailing address** if it differs from situs.
3. Reverse **address** on TruePeopleSearch, then FastPeopleSearch and That’s Them. Prefer a contact that appears on two independent sources with this situs or the mailing address.
4. LLC / trust / estate: do not skip-trace the entity string as a person. Use OpenCorporates / Comptroller first; SOSDirect is $1/search. Restart person research on the named officer/manager.
5. Paste the verified person:

```bash
python3 -m leads review paste LEAD_ID --name "Jane Q Public" --phone "..." --email "..." --address "123 Main St, Houston, TX 77002"
```

6. Approve only after reading the full lead. Export is later.

## Stop

Captcha walls mean try the next site, not automation. Do not merge two people into one contact. Do not use registered-agent mill phones as the homeowner. Do not guess `first.last@gmail.com`. Breach dumps are not current contactability.
