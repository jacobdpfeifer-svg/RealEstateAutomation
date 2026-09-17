"""Offline regression tests; synthetic contacts, no provider/network access."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from leads import db
from leads.models import CaseRecord, PropertyRecord
from leads.pipeline import Pipeline
from leads.review import approve_lead, export_csv
from leads.secrets import load_secrets
from providers.skip_trace.base import get_skip_trace_provider
from providers.skip_trace.batchdata import BatchDataSkipTraceProvider


class ContactEnrichmentTests(unittest.TestCase):
    def test_email_only_and_later_nonempty_channels_persist_and_export(self):
        session = Mock()
        session.post.return_value.json.return_value = {
            'status': {'code': 200},
            'results': {'persons': [
                {'name': {'first': 'Jane', 'last': 'Example'},
                 'emails': [None, {}, {'email': '  jane@example.com  '}],
                 'phoneNumbers': [None, {}]},
                {'fullName': 'Second Example', 'emails': [],
                 'phoneNumbers': [{}, None, {'number': '2145550100'}]},
            ]},
        }
        provider = BatchDataSkipTraceProvider(api_key='synthetic-key', session=session)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'contacts.db'
            with db.db_session(path) as conn:
                case = CaseRecord('48201', 'SYNTHETIC-CONTACT', 'HARRIS COUNTY TAX',
                                  'JANE EXAMPLE', 'JANE EXAMPLE', 'Tax', date(2026, 1, 1),
                                  'OPEN', 'fixture', datetime(2026, 1, 2), 'synthetic-contact')
                data = asdict(case)
                data['filed_date'] = case.filed_date.isoformat()
                data['retrieved_at'] = case.retrieved_at.isoformat()
                case_id = db.upsert_case(conn, data)
                lead_id = db.ensure_lead(conn, case_id)
                prop = PropertyRecord(case_id, 'SYNTHETIC-APN',
                                      '100 Example St, Houston, TX 77002-1234',
                                      'JANE EXAMPLE', None, None, 'fixture', 0.99)
                tax = Mock()
                tax.search_by_owner.return_value = [prop]
                with patch('leads.pipeline.get_tax_adapter', return_value=tax), \
                     patch('leads.pipeline.get_skip_provider_for_county', return_value=provider):
                    pipe = Pipeline(conn, max_enrich=1)
                    self.assertEqual(pipe.enrich_pending('harris')['enriched'], 1)
                    self.assertEqual(pipe.enrich_pending('harris')['processed'], 0)
                session.post.assert_called_once()
                payload = session.post.call_args.kwargs['json']['requests'][0]
                self.assertEqual(payload['propertyAddress'], {
                    'street': '100 Example St', 'city': 'Houston', 'state': 'TX', 'zip': '77002'})
                rows = conn.execute('SELECT * FROM contact_record ORDER BY id').fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual((rows[0]['email'], rows[0]['phone']), ('jane@example.com', ''))
                self.assertEqual(rows[1]['phone'], '2145550100')
                self.assertTrue(all(r['property_id'] == rows[0]['property_id'] for r in rows))
                self.assertEqual(conn.execute('SELECT contact_id FROM lead').fetchone()[0], rows[0]['id'])
                self.assertNotIn('jane@example.com', export_csv(conn, 'approved'))
                approve_lead(conn, lead_id, 'Synthetic test approval')
                self.assertIn('jane@example.com', export_csv(conn, 'approved'))
            with db.db_session(path) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM contact_record').fetchone()[0], 2)

    def test_zip_is_not_guessed_from_house_number(self):
        provider = BatchDataSkipTraceProvider(api_key='synthetic-key', session=Mock())
        request = provider._build_request('Example', '12345', 'Dallas', 'TX')
        self.assertNotIn('zip', request['requests'][0]['propertyAddress'])

    def test_blank_key_is_stub_and_file_loading_preserves_shell_override(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            path = Path(folder) / 'secrets.env'
            path.write_text('BATCHDATA_API_KEY=\n')
            load_secrets(path)
            self.assertEqual(get_skip_trace_provider('batchdata').name, 'stub')
            path.write_text('BATCHDATA_API_KEY=synthetic-file-key\n')
            # An explicitly empty shell variable remains a deliberate override.
            load_secrets(path)
            self.assertEqual(get_skip_trace_provider('batchdata').name, 'stub')
            with patch.dict('os.environ', {}, clear=True):
                load_secrets(path)
                self.assertEqual(get_skip_trace_provider('batchdata').name, 'batchdata')
        with self.assertRaises(RuntimeError):
            BatchDataSkipTraceProvider(api_key='   ')


if __name__ == '__main__':
    unittest.main()
