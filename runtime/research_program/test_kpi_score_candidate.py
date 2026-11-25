from datetime import date
import unittest

import numpy as np

from research_program import kpi_score_candidate as candidate
from research_program.score_form import poisson_matrix, summarize
from research_program.three_arm_model import score_picks


class CandidateTests(unittest.TestCase):
    def test_goals_fallback_has_consistent_feature_encoding(self):
        stats = summarize([], date(2026, 11, 23))
        values = candidate.form_features('ita.1', dict(stats, xgf=2.8, xg_n=6), stats)[None, :, :]
        candidate.mask_xg(values)
        n = candidate.FEATURE_NAMES
        for role in ('own','opponent'):
            self.assertTrue(np.array_equal(values[...,n.index(role+'_gf')], values[...,n.index(role+'_xgf')]))
            self.assertFalse(values[...,n.index(role+'_xg_n')].any())

    def test_market_residual_keeps_score_support_and_joint_probability(self):
        market = poisson_matrix((1.7,.9), candidate.SHAPE)[None,:,:]
        zero_tilt = np.zeros((4,25))
        self.assertTrue(np.allclose(market, candidate.recalibrate(market,zero_tilt)))
        zero_tilt[0,0] = 0.9
        changed = candidate.recalibrate(market, zero_tilt)
        self.assertGreater(changed[0,0,0], market[0,0,0])
        self.assertAlmostEqual(changed.sum(),1.)
        self.assertIn(score_picks(changed[0],line=2.75)['total']['direction'], ('over','under'))

    def test_unapproved_cohort_abstains(self):
        data = {'version':candidate.VERSION,'selected_recipe':'market_tilt'}
        data['artifact_sha256'] = candidate.artifact_identity(data)
        report = candidate.predict(data,{'cohort':'bra.1','stage':'regular','score_basis':'regulation_90'})
        self.assertEqual(report['status'],'ABSTAIN_OUTSIDE_TRAINED_SCOPE')


if __name__ == '__main__':
    unittest.main()
