import unittest
from leads.db import _to_pg_sql


class PostgresSqlTranslationTests(unittest.TestCase):
    def test_native_casts_survive_alongside_named_parameters(self):
        self.assertEqual(
            _to_pg_sql('SELECT md5(row_to_json(t)::text), :value::integer FROM lead t WHERE id = :id'),
            'SELECT md5(row_to_json(t)::text), %(value)s::integer FROM lead t WHERE id = %(id)s',
        )

    def test_positional_parameters_still_translate(self):
        self.assertEqual(_to_pg_sql('SELECT ?::text'), 'SELECT %s::text')
