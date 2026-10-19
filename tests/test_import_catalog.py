import copy
import unittest
from pathlib import Path
from import_catalog import parse_official, propose, validate_source
SOURCE = 'https://www.onepiece-cardgame.com/cardlist/?search=true&series=550112'
HTML = Path(__file__).with_name('fixtures').joinpath('official-op12-event.html').read_text()
class ImportTests(unittest.TestCase):
    def test_official_fields(self):
        row = parse_official(HTML, SOURCE)[0]
        self.assertEqual(row['officialId'], 'OP12-018')
        self.assertEqual(row['name'], '覇王色の覇気')
        self.assertNotIn('image', row)
    def test_preserves_manual_and_idempotent(self):
        catalog = {'cards':[{'num':'OP12-018','slot':8,'name':'Manual','variants':[{'id':'p1','kind':'manga'}]}]}
        before = copy.deepcopy(catalog)
        result = propose(catalog, parse_official(HTML,SOURCE))
        self.assertEqual(catalog,before)
        card = result['catalog']['cards'][0]
        self.assertEqual((card['slot'],card['name'],card['variants']),(8,'Manual',before['cards'][0]['variants']))
        self.assertEqual(propose(result['catalog'],parse_official(HTML,SOURCE))['changes'],[])
    def test_ambiguous_parallel_review(self):
        rows = parse_official(HTML.replace('id="OP12-018"','id="OP12-018_p9"'),SOURCE)
        result = propose({'cards':[{'num':'OP12-018','alt':['p9']}]},rows)
        self.assertEqual(len(result['review']),1)
        self.assertEqual(result['changes'],[])
    def test_new_unplaced_no_deletions(self):
        result = propose({'cards':[{'num':'OLD','slot':1}]},parse_official(HTML,SOURCE))
        self.assertEqual(len(result['catalog']['cards']),2)
        self.assertIsNone(result['catalog']['cards'][1]['slot'])
    def test_invalid_source_and_empty(self):
        for url in ['https://evil.test/cardlist/','http://www.onepiece-cardgame.com/cardlist/']:
            with self.assertRaises(ValueError): validate_source(url)
        with self.assertRaises(ValueError): parse_official('<html/>',SOURCE)

class ApplyTests(unittest.TestCase):
    def test_apply_uses_reviewed_etag_and_single_put(self):
        import tempfile
        import json
        from unittest.mock import patch, MagicMock
        from import_catalog import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'proposal.json'
            path.write_text(json.dumps({'endpoint':'http://127.0.0.1:8765/api/catalog','etag':'"reviewed"','catalog':{'cards':[]}}))
            response = MagicMock()
            response.__enter__.return_value = response
            with patch('sys.argv',['import_catalog.py','apply',str(path)]), patch('import_catalog.urlopen',return_value=response) as request:
                main()
            sent = request.call_args.args[0]
            self.assertEqual(sent.method,'PUT')
            self.assertEqual(sent.get_header('If-match'),'"reviewed"')
            self.assertEqual(request.call_count,1)

    def test_duplicate_number_is_reviewed(self):
        result = propose({'cards':[{'num':'OP12-018'},{'num':'OP12-018'}]},parse_official(HTML,SOURCE))
        self.assertEqual(result['changes'],[])
        self.assertEqual(len(result['review']),1)

class CanonicalImportTests(unittest.TestCase):
    def test_new_base_passes_canonical_validation(self):
        import workflow
        catalog = workflow.normalize_catalog({})
        result = propose(catalog,parse_official(HTML,SOURCE))
        self.assertTrue(workflow.valid_catalog(workflow.normalize_catalog(result['catalog'])))
