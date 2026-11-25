import unittest

import numpy as np

from research_program import all_scope_model as base
from research_program import r10_shot_model as model


class ShotDistributionTests(unittest.TestCase):
    def setUp(self):
        self.prior = base.poisson_matrix([1.5, 1.2], (base.SIZE, base.SIZE))

    def match(self, day, mid, stage='regular'):
        return dict(id=mid, date=day, home='A', away='B', home_goals=2, away_goals=1,
                    competition='aus.1', stage=stage)

    def test_block_update_keeps_target_and_later_shots_out(self):
        rows = [self.match('2025-03-01','a'), self.match('2025-03-01','b'),
                self.match('2025-03-02','c'), self.match('2025-03-02','d','playoff')]
        d = {g['id']: dict(g, home_sot=6, away_sot=4) for g in rows}
        c, _, _ = model.walk(rows, d, 'aus.1')
        self.assertEqual([v['ready'] for v in c], [False, False, True, False])
        d['c']['home_sot'] = 20
        changed, _, _ = model.walk(rows, d, 'aus.1')
        np.testing.assert_array_equal(c[2]['shots'], changed[2]['shots'])
        self.assertEqual(len(model.walk(rows, {}, 'aus.1')[0]), 4)

    def test_conversion_conflict_keeps_volume(self):
        g = self.match('2025-03-01', 'a')
        _, shots, state = model.walk([g], {'a': dict(g, home_sot=1, away_sot=4)}, 'aus.1')
        self.assertEqual(list(shots[0]), [1., 4.])
        home = state['regular']['teams']['A'][1]
        self.assertEqual(home[0], [1., 1., 0., 0.])
        self.assertEqual(home[1], [1., 4., 4., 1.])
        self.assertIsNone(model.observation(dict(g, home_shots=2, home_sot=3, away_sot=4), g))
        with self.assertRaises(ValueError):
            model.observation(dict(g, home='X', home_sot=1, away_sot=4), g)

    def test_shared_and_opposing_pace_shift_covariance(self):
        ctx = dict(ready=True, shots=np.array([5., 4.]), conversion=np.array([.3, .3]), relative_finish=np.ones(2))
        for sign in (-1, 1):
            rate, m = model.alternatives(self.prior, ctx, dict(beta=[0., 0.], signed_shock=sign*.3))
            self.assertIs(rate, self.prior)
            self.assertAlmostEqual(m.sum(), 1.)
            covariance = (m * base.H * base.A).sum() - (m * base.H).sum() * (m * base.A).sum()
            self.assertGreater(sign * covariance, .05)
        self.assertIs(model.alternatives(self.prior, dict(ready=False), {})[1], self.prior)

    def test_zero_dose_preserves_archived_decisions(self):
        other = base.poisson_matrix([.5, 3.], (base.SIZE, base.SIZE))
        origin = dict(base.pick(self.prior, 2.5), primary='0-0', top3=['0-0','1-0','0-1'])
        chosen, _ = model.decisions(self.prior, other, dict(score=0., wdl=1., total=0.), 2.5, origin)
        self.assertEqual(chosen['top3'], origin['top3'])
        self.assertEqual(chosen['wdl'], 'away')
        self.assertEqual(chosen['total_side'], origin['total_side'])
        self.assertEqual(model.choose([], [], [])[0], dict(score=0., wdl=0., total=0.))
