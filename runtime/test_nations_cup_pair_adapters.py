"""Portable guard checks; no real forecasts or publication calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nations_cup_pair_adapters import Resolver, predict_pair, sha, _output, _source_modules
import numpy as np


class ResearchPairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'index.json'; self.path.write_text('{}')
        self.route = dict(engine='nations_cup', status='READY_RESEARCH_PAIR', competition='uefa.nations',
                          stages=['league-phase'], index_path=str(self.path), index_sha256=sha(self.path))
        self.fixture = dict(match_id='test-only', competition='uefa.nations', cohort='uefa.nations:A:league-phase', stage='league-phase')

    def test_opt_in_only(self):
        for field, value in [('engine','active'), ('status','PROMOTED'), ('competition','other'), ('stages',[])]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                predict_pair(self.fixture, {**self.route, field:value})

    def test_index_tamper(self):
        self.path.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError,'checksum'): predict_pair(self.fixture,self.route)

    def test_unknown_index(self):
        with patch('nations_league.safe_cli.validate_future'):
            with self.assertRaisesRegex(ValueError,'index required'): predict_pair(self.fixture,self.route)

    def test_source_validator_not_bypassed(self):
        with self.assertRaises((KeyError,ValueError)): predict_pair(self.fixture,self.route)

    def test_pair_not_promoted(self):
        with patch('nations_cup_pair_adapters._nations', return_value=({'current':{'role':'current'},'test':{'role':'test'}},{})):
            result=predict_pair(self.fixture,self.route)
        self.assertFalse(result['automatic_promotion']); self.assertFalse(result['active_route'])
        self.assertTrue(result['publication_required']); self.assertFalse(result['formal_kpi_eligible'])
        self.assertEqual(result['execution_audit']['index_sha256'],sha(self.path))

    def test_relocation_conflict(self):
        other=self.path.with_name('other');other.write_text('different')
        with self.assertRaisesRegex(ValueError,'conflicting'):
            Resolver({str(other):str(self.path)}).file(str(other),sha(self.path))

    def test_relocation_rechecked(self):
        r=Resolver({'old':str(self.path)});r.file('old',sha(self.path));self.path.write_text('changed')
        with self.assertRaises(ValueError):r.recheck()

    def test_exact_source_set(self):
        import nations_cup_pair_adapters as module
        with self.assertRaises(ValueError):_source_modules({'/old/unexpected.py':sha(module.__file__)},{'adapter.py':module})
        self.assertEqual(_source_modules({'/old/adapter.py':sha(module.__file__)},{'adapter.py':module}),{'adapter.py':sha(module.__file__)})

    def test_invalid_matrix(self):
        with self.assertRaises(ValueError):_output(np.array([[np.nan]]),{},self.fixture,'a',(0,0))

    def test_output_does_not_mutate_input(self):
        before=copy.deepcopy(self.fixture)
        out=_output(np.array([[.1,.2],[.3,.4]]),{'version':'test','role':'current','core':{'fit_end':'2020-01-01'}},self.fixture,'a',(1,1))
        out['input']['stage']='changed';self.assertEqual(self.fixture,before)
        self.assertEqual(out['primary'],'1-1');self.assertAlmostEqual(sum(out['wdl'].values()),1.)

if __name__=='__main__':unittest.main()
