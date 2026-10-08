import json
import math
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

import numpy as np
from model import (matrix,partitions,fit_strength,rates,research_predict,fit_nuisance,validate_history,total_settlement)
from evidence import (bounded_market,no_vig,evidence_before,market_predict,fit_context,validate_context)
from cli import freeze

def history():
    rows=[];start=datetime(2020,1,1,tzinfo=timezone.utc)
    for i in range(240):
        day=(start+timedelta(days=i)).date().isoformat()
        rows.append(dict(match_id=str(i),cohort='TEST',date=day,home='A' if i%2 else 'B',away='B' if i%2 else 'A',
            home_goals=2 if i%2 else 0,away_goals=0 if i%2 else 2,neutral=False,score_basis='regulation_90',
            source_url='https://example.org/results',observed_at='2026-01-01T00:00:00+00:00'))
    return rows

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.model=fit_nuisance(fit_strength(history()),history())

    def fixture(self):
        now=datetime.now(timezone.utc);cutoff=now.isoformat()
        return dict(match_id='test-future',cohort='TEST',home='A',away='B',phase='PreMatch',cutoff_at=cutoff,
            kickoff_at=(now+timedelta(days=1)).isoformat(),total_line=2.25,
            sources=[dict(source_url='https://example.org/fixture',query_time=cutoff)])

    def test_probability_partition_and_bivariate_marginals(self):
        for family,nuisance in [('poisson',0),('dixon_coles',-.04),('bivariate',.3)]:
            m=matrix(1.7,.8,family,nuisance)
            self.assertAlmostEqual(float(m.sum()),1,12);self.assertTrue(np.all(m>=0))
            self.assertAlmostEqual(float(np.arange(len(m))@m.sum(axis=1)),1.7,10)
            self.assertAlmostEqual(float(np.arange(len(m))@m.sum(axis=0)),.8,10)

    def test_zero_shared_matches_poisson(self):np.testing.assert_allclose(matrix(2,1,'bivariate',0),matrix(2,1),atol=1e-14)

    def test_quarter_settlement(self):
        m=np.zeros((51,51));m[2,0]=1
        self.assertEqual(total_settlement(m,2.25,'over')['half_loss'],1)
        self.assertEqual(total_settlement(m,2.25,'under')['half_win'],1)
        self.assertEqual(total_settlement(m,2.,'over')['push'],1)

    def test_strength_identifies_team_not_home_bias(self):
        lh,la,_=rates(self.model,dict(home='A',away='B'))
        rh,ra,_=rates(self.model,dict(home='B',away='A'))
        self.assertGreater(lh,la);self.assertGreater(ra,rh)

    def test_temporal_blocks_do_not_share_dates(self):
        blocks=partitions(history())
        for first,second in zip(blocks,blocks[1:]):self.assertLess(max(r['date'] for r in first),min(r['date'] for r in second))

    def test_duplicate_and_non90_rejected(self):
        with self.assertRaises(ValueError):validate_history(history()+[history()[0]])
        rows=history();rows[0]['score_basis']='after_extra_time'
        with self.assertRaises(ValueError):validate_history(rows)

    def test_postmatch_and_later_evidence_rejected(self):
        f=self.fixture();f['kickoff_at']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):research_predict(self.model,f,'poisson')
        f=self.fixture();f['sources'][0]['query_time']=f['kickoff_at']
        with self.assertRaises(ValueError):research_predict(self.model,f,'poisson')

    def test_untrained_context_rejected(self):
        f=self.fixture();f['context_features']={'missing_starters':2}
        with self.assertRaises(ValueError):research_predict(self.model,f,'poisson')
        with self.assertRaises(ValueError):fit_context([],['missing_starters'])

    def test_context_coefficients_learned_and_leakage_rejected(self):
        start=datetime(2020,1,1,tzinfo=timezone.utc);samples=[]
        for i in range(240):
            kickoff=start+timedelta(days=i);cutoff=(kickoff-timedelta(hours=1)).isoformat()
            samples.append(dict(cohort='TEST',kickoff_at=kickoff.isoformat(),cutoff_at=cutoff,
                baseline_observed_at=(start-timedelta(days=1)).isoformat(),result_observed_at=(kickoff+timedelta(hours=3)).isoformat(),
                baseline_home=1.5,baseline_away=1.,home_goals=3 if i%2 else 0,away_goals=1,
                features={'strength':1 if i%2 else -1},evidence=[dict(source_url='https://example.org/lineup',query_time=cutoff,source_tier='L1')]))
        fit=fit_context(samples[:150],['strength'])
        self.assertGreater(fit['coefficients'][0][0],0);self.assertFalse(fit['validated'])
        validated=validate_context(samples,['strength']);self.assertIn('validation',validated)
        samples[0]['baseline_observed_at']=samples[0]['kickoff_at']
        with self.assertRaises(ValueError):fit_context(samples,['strength'])

    def test_unseen_future_team_requires_explicit_prior(self):
        f=self.fixture();f['home']='Unknown club'
        with self.assertRaises(ValueError):research_predict(self.model,f,'poisson')
        f['accept_cold_start_prior']=True
        self.assertEqual(research_predict(self.model,f,'poisson')['unseen_teams'],['Unknown club'])

    def test_market_is_bounded(self):
        m=matrix(1.4,1.1);adjusted,meta=bounded_market(m,no_vig([1.1,12,25]),.2)
        self.assertLessEqual(meta['kl'],.02);self.assertAlmostEqual(float(adjusted.sum()),1)
        with self.assertRaises(ValueError):bounded_market(m,no_vig([2,3,4]),.5)

    def test_market_time_and_validation_gate(self):
        q=dict(source_url='https://example.org/odds',query_time='2026-01-01T00:00:00+00:00',decimal_1x2=[2,3,4])
        with self.assertRaises(ValueError):market_predict(matrix(1,1),q,'2026-01-02T00:00:00+00:00',{'validated':True,'alpha':.1})
        with self.assertRaises(ValueError):no_vig([1,2,3])
        with self.assertRaises(ValueError):evidence_before(q,'2025-01-01T00:00:00+00:00')

    def test_freeze_needs_audit_and_cannot_overwrite(self):
        f=self.fixture();p=research_predict(self.model,f,'poisson')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):freeze(p,self.model,d)
            f.update(identity_verified=True,context_audit_complete=True,
                audit_evidence=[dict(source_url='https://example.org/official',query_time=f['cutoff_at'],source_tier='L1',supports_field=['fixture','lineup','availability','motivation','integrity'])])
            p=research_predict(self.model,f,'poisson');path=freeze(p,self.model,d)
            saved=json.loads(Path(path).read_text());self.assertFalse(saved['formal_kpi_eligible'])
            with self.assertRaises(FileExistsError):freeze(p,self.model,d)
            p['primary']='9-9'
            with self.assertRaises(ValueError):freeze(p,self.model,d)

if __name__=='__main__':unittest.main()
