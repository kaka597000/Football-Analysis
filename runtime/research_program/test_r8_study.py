import unittest

import numpy as np

from . import all_scope_model as base
from . import r8_market_movement as movement
from . import r8_score_calibration as model


class R8StudyTests(unittest.TestCase):
    def setUp(self):
        self.matrix = base.poisson_matrix([1.5, 1.1], (base.SIZE, base.SIZE))

    def test_all_settlements_and_wdl_stay_fixed(self):
        offsets = np.random.default_rng(42).normal(size=36)
        for line in (None, 2., 2.25, 2.5, 2.75):
            original = self.matrix.ravel()
            groups = model.partitions(line)
            changed = model.apply(self.matrix, offsets, line).ravel()
            np.testing.assert_allclose(np.bincount(groups, weights=changed),
                                       np.bincount(groups, weights=original), atol=1e-13, rtol=0)
            self.assertFalse(np.allclose(original, changed))
            self.assertAlmostEqual(changed.sum(), 1.)

    def test_fit_learns_relative_score_and_empty_fold_is_identity(self):
        scores = [(2,1)] * 24
        matrices = [self.matrix] * len(scores)
        offsets = model.fit(matrices, scores, [2.5] * len(scores))
        self.assertGreater(model.apply(self.matrix, offsets, 2.5)[2,1], self.matrix[2,1])
        np.testing.assert_allclose(model.apply(self.matrix, model.fit([],[],[]), 2.5), self.matrix)

    def test_market_movement_is_legal_and_same_book(self):
        def point(lag, book, price, source='phoenix'):
            return dict(seconds_to_kickoff=lag, bookmaker=book, source=source, market='1x2',
                        odds=dict(home=price,draw=3.4,away=4.4))
        points = [point(90000, 'a', 2.2),point(3600, 'a', 1.9),point(3599,'a',1.3),
                  point(91000,'b',2.4),point(3700,'b',1.9,source='other')]
        _, delta, pairs = movement.endpoints(points)
        self.assertEqual(pairs,1)
        self.assertGreater(delta[0],0)
        self.assertAlmostEqual(delta.sum(),0.)
        _, _, pairs = movement.endpoints(points+[point(3600,'a',2.1)])
        self.assertEqual(pairs,0)

    def test_residual_normalizes_and_updates_probability(self):
        prior = np.repeat([[.4,.25,.35]],30,axis=0)
        x = np.repeat([[.45,.20,.35]],30,axis=0)
        fit = movement.fit_residual(x,prior,np.array([0]*20+[1]*5+[2]*5))
        result = movement.infer(x,prior,fit)
        np.testing.assert_allclose(result.sum(axis=1), 1.)
        self.assertGreater(result[0,0], prior[0,0])
