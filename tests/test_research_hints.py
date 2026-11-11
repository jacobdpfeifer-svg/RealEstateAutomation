"""Offline research-hints tests. Fictional Jane Q Public only; no HTTP."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from leads import db
from leads.cli import build_parser, cmd_review
from leads.research_hints import build_research_hints, looks_like_entity, parse_situs
from leads.review import paste_contact, research_hints_for_lead


JANE = "Jane Q Public"
SITUS = "123 Main St, Houston, TX 77002"
LLC = "JQP HOLDINGS LLC"


def _insert_lead(conn, *, name: str, situs: str, case_number: str = "SYNTH-HINTS-1") -> int:
    case_id = db.upsert_case(
        conn,
        {
            "county_fips": "48201",
            "case_number": case_number,
            "plaintiff": "HARRIS COUNTY TAX ASSESSOR-COLLECTOR",
            "defendant_raw": name,
            "defendant_normalized": name.upper(),
            "case_type": "OCV",
            "filed_date": "2026-01-01",
            "status": "OPEN",
            "source_url": "fixture",
            "retrieved_at": "2026-09-17T00:00:00",
            "dedupe_key": case_number,
        },
    )
    lead_id = db.ensure_lead(conn, case_id, "needs_review")
    conn.execute(
        """
        INSERT INTO property_record (
            case_id, apn, situs_address, owner_of_record, assessor_url,
            match_confidence, dedupe_key, property_type, total_value
        ) VALUES (?, 'FIXTURE-APN', ?, ?, '', 0.9, ?, 'RESIDENTIAL', 250000)
        """,
        (case_id, situs, name, case_number),
    )
    prop_id = conn.execute(
        "SELECT id FROM property_record WHERE case_id = ?", (case_id,)
    ).fetchone()["id"]
    conn.execute("UPDATE lead SET property_id = ? WHERE id = ?", (prop_id, lead_id))
    return lead_id


class ResearchHintUrlTests(unittest.TestCase):
    def test_jane_q_public_houston_urls(self):
        urls = build_research_hints(name=JANE, situs=SITUS, city="Houston", state="TX")
        self.assertEqual(
            urls["TruePeopleSearch name"],
            "https://www.truepeoplesearch.com/results?name=Jane+Q+Public&citystatezip=Houston%2C+TX",
        )
        self.assertEqual(
            urls["TruePeopleSearch address"],
            "https://www.truepeoplesearch.com/results?streetaddress=123+Main+St&citystatezip=Houston%2C+TX+77002",
        )
        self.assertEqual(
            urls["FastPeopleSearch name"],
            "https://www.fastpeoplesearch.com/name/jane-q-public_houston-tx",
        )
        self.assertEqual(
            urls["FastPeopleSearch address"],
            "https://www.fastpeoplesearch.com/address/123-main-st_houston-tx-77002",
        )
        self.assertEqual(
            urls["That's Them name"],
            "https://thatsthem.com/name/Jane-Q-Public/Houston-TX",
        )
        self.assertEqual(
            urls["That's Them address"],
            "https://thatsthem.com/address/123-Main-St/Houston-TX-77002",
        )
        self.assertEqual(
            urls["Whitepages"],
            "https://www.whitepages.com/name/Jane-Q-Public/Houston-TX",
        )
        self.assertEqual(
            urls["Google"],
            "https://www.google.com/search?q=%22Jane+Q+Public%22+%22123+Main+St%22+Houston+TX",
        )
        self.assertNotIn("OpenCorporates", urls)
        self.assertNotIn("SOSDirect", urls)
        self.assertNotIn("Texas Comptroller", urls)
        self.assertNotEqual(next(iter(urls)), "SOSDirect")

    def test_llc_includes_opencorporates_and_texas_entity_pages(self):
        urls = build_research_hints(name=LLC, situs=SITUS, city="Houston", state="TX")
        self.assertEqual(
            urls["OpenCorporates"],
            "https://opencorporates.com/companies?q=JQP+HOLDINGS+LLC&jurisdiction_code=us_tx",
        )
        self.assertEqual(urls["Texas Comptroller"], "https://mycpa.cpa.state.tx.us/coa/")
        self.assertEqual(urls["SOSDirect"], "https://www.sos.state.tx.us/corp/sosda/index.shtml")
        self.assertTrue(looks_like_entity(LLC))
        self.assertFalse(looks_like_entity(JANE))
        self.assertFalse(looks_like_entity("Jane L Public"))

    def test_zip_is_not_taken_from_five_digit_house_number(self):
        street, city, state, zip_code = parse_situs("12345", city="Dallas", state="TX")
        self.assertEqual(street, "12345")
        self.assertEqual((city, state, zip_code), ("Dallas", "TX", ""))
        urls = build_research_hints(
            name=JANE, situs="12345", city="Dallas", state="TX", zip_code="12345"
        )
        blob = "\n".join(urls.values())
        self.assertNotIn("Dallas%2C+TX+12345", blob)
        self.assertNotIn("dallas-tx-12345", blob)
        self.assertNotIn("Dallas-TX-12345", blob)
        self.assertIn("streetaddress=12345", urls["TruePeopleSearch address"])
        self.assertIn("citystatezip=Dallas%2C+TX", urls["TruePeopleSearch address"])
        self.assertTrue(urls["TruePeopleSearch address"].endswith("Dallas%2C+TX"))

        no_zip_situs = "12345 Main St, Dallas, TX"
        urls = build_research_hints(name=JANE, situs=no_zip_situs, city="Dallas", state="TX")
        self.assertEqual(parse_situs(no_zip_situs, city="Dallas", state="TX")[3], "")
        self.assertEqual(
            urls["FastPeopleSearch address"],
            "https://www.fastpeoplesearch.com/address/12345-main-st_dallas-tx",
        )
        self.assertEqual(
            urls["That's Them address"],
            "https://thatsthem.com/address/12345-Main-St/Dallas-TX",
        )


class ResearchHintLeadTests(unittest.TestCase):
    def test_research_hints_for_lead_and_paste_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hints.db"
            with db.db_session(path) as conn:
                lead_id = _insert_lead(conn, name=JANE, situs=SITUS)
                before = conn.execute("SELECT COUNT(*) AS n FROM contact_record").fetchone()["n"]
                payload = research_hints_for_lead(conn, lead_id)
                after = conn.execute("SELECT COUNT(*) AS n FROM contact_record").fetchone()["n"]
                self.assertEqual(before, after)
                self.assertEqual(payload["zip"], "77002")
                self.assertEqual(payload["city"], "Houston")
                self.assertEqual(payload["state"], "TX")
                self.assertFalse(payload["entity"])
                self.assertEqual(
                    payload["urls"]["TruePeopleSearch address"],
                    "https://www.truepeoplesearch.com/results?streetaddress=123+Main+St&citystatezip=Houston%2C+TX+77002",
                )
                paste_contact(
                    conn,
                    lead_id,
                    name=JANE,
                    phone="7135550100",
                    email="jane@example.com",
                    address=SITUS,
                )
                row = conn.execute("SELECT * FROM contact_record").fetchone()
                self.assertEqual(row["provider"], "manual_paste")
                self.assertEqual(row["manual_paste"], 1)
                self.assertEqual(row["name"], JANE)

            with db.db_session(path) as conn:
                llc_id = _insert_lead(conn, name=LLC, situs=SITUS, case_number="SYNTH-HINTS-LLC")
                llc_payload = research_hints_for_lead(conn, llc_id)
                self.assertTrue(llc_payload["entity"])
                self.assertIn("OpenCorporates", llc_payload["urls"])
                self.assertIn("us_tx", llc_payload["urls"]["OpenCorporates"])

            with db.db_session(path) as conn:
                case_id = db.upsert_case(
                    conn,
                    {
                        "county_fips": "48201",
                        "case_number": "SYNTH-NO-PROP",
                        "plaintiff": "HARRIS COUNTY TAX ASSESSOR-COLLECTOR",
                        "defendant_raw": JANE,
                        "defendant_normalized": "JANE Q PUBLIC",
                        "case_type": "OCV",
                        "filed_date": "2026-01-01",
                        "status": "OPEN",
                        "source_url": "fixture",
                        "retrieved_at": "2026-09-17T00:00:00",
                        "dedupe_key": "SYNTH-NO-PROP",
                    },
                )
                bare = db.ensure_lead(conn, case_id, "needs_review")
                with self.assertRaisesRegex(ValueError, "no property"):
                    research_hints_for_lead(conn, bare)
                with self.assertRaisesRegex(ValueError, "Lead not found"):
                    research_hints_for_lead(conn, 999999)

    def test_cli_prints_urls_and_json_without_writing_contacts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hints.db"
            with db.db_session(path) as conn:
                lead_id = _insert_lead(conn, name=JANE, situs=SITUS)
            stdout = io.StringIO()
            args = build_parser().parse_args(
                ["review", "research-hints", str(lead_id), "--db", str(path)]
            )
            with patch("leads.cli.load_secrets"), redirect_stdout(stdout), \
                 patch("leads.db.migrate", side_effect=AssertionError("must not migrate")), \
                 patch("socket.socket.connect", side_effect=AssertionError("must not connect")):
                self.assertEqual(cmd_review(args), 0)
            text = stdout.getvalue()
            self.assertIn("TruePeopleSearch name:", text)
            self.assertIn("77002", text)
            self.assertNotIn("OpenCorporates", text)

            stdout = io.StringIO()
            args = build_parser().parse_args(
                ["review", "research-hints", str(lead_id), "--json", "--db", str(path)]
            )
            with patch("leads.cli.load_secrets"), redirect_stdout(stdout), \
                 patch("leads.db.migrate", side_effect=AssertionError("must not migrate")), \
                 patch("socket.socket.connect", side_effect=AssertionError("must not connect")):
                self.assertEqual(cmd_review(args), 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["name"], JANE)
            self.assertIn("Google", payload["urls"])
            with db.db_session(path) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) AS n FROM contact_record").fetchone()["n"],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
