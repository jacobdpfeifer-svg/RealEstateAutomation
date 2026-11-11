"""Synthetic CSV security regressions; no database or network access."""
import csv
import io
import unittest
from unittest.mock import patch

from leads.review import export_csv


class ExportSafetyTests(unittest.TestCase):
    def export_row(self, row):
        with patch("leads.review.list_review_queue", return_value=[row]):
            return list(csv.DictReader(io.StringIO(export_csv(None))))[0]

    def test_formula_prefixes_are_neutralized_in_every_text_column(self):
        prefixes = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠", "  =")
        fields = ("defendant_raw", "plaintiff", "case_number", "filed_date",
                  "situs_address", "apn", "property_type", "contact_name",
                  "phone", "email", "pipeline_status", "review_note")
        for prefix in prefixes:
            with self.subTest(prefix=repr(prefix)):
                payload = prefix + "1+1"
                row = dict.fromkeys(fields, payload)
                exported = self.export_row(row)
                for field in fields:
                    self.assertEqual(exported[field], "'" + payload)
                    self.assertEqual(row[field], payload)

    def test_quoted_separators_and_newlines_do_not_create_extra_cells(self):
        payload = '=1+1",=2+2\r\n=3+3'
        exported = self.export_row({"review_note": payload, "id": 7})
        self.assertEqual(exported["review_note"], "'" + payload)
        self.assertEqual(exported["lead_id"], "7")
        self.assertEqual(len(exported), 16)
        self.assertNotIn(None, exported)

    def test_ordinary_data_and_numeric_values_are_preserved(self):
        exported = self.export_row({"contact_name": "Jane Q Public",
                                    "email": "jane@example.com", "phone": "7135550100",
                                    "total_value": -1.5, "review_note": None})
        self.assertEqual(exported["contact_name"], "Jane Q Public")
        self.assertEqual(exported["email"], "jane@example.com")
        self.assertEqual(exported["phone"], "7135550100")
        self.assertEqual(exported["total_value"], "-1.5")
        self.assertEqual(exported["review_note"], "")
