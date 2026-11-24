from datetime import date, timedelta
import unittest

import numpy as np

from research_program.halftime_balance import TeamHistory, adjusted, fit, half_balance, picks, training_example


class HalfBalanceTests(unittest.TestCase):
    def test_sides_flip_and_six_latest_matches_only(self):
        state=TeamHistory()
        for i in range(7):
            # The first match would reverse the signal if mistakenly used.
            state.observe(date(2024,1,1)+timedelta(days=i),'A','B',(0,0) if i==0 else (1,0),(4,0) if i==0 else (1,0))
        self.assertEqual(state.signal(date(2024,1,8),'A','B'),(1.,'available'))
        self.assertEqual(state.signal(date(2024,1,8),'B','A'),(-1.,'available'))
        self.assertEqual(state.signal(date(2024,5,1),'A','B')[1],'oldest_over_90_days')
        self.assertEqual(state.signal(date(2024,1,8),'A','X')[1],'less_than_six')

    def test_bad_period_evidence_is_unavailable_and_never_replaced(self):
        self.assertIsNone(half_balance((2,0),(1,0)))
        self.assertIsNone(half_balance((True,0),(1,0)))
        state=TeamHistory()
        for i in range(7):
            state.observe(date(2024,1,i+1),'A','B',None if i==3 else (0,0),(1,0))
        self.assertEqual(state.signal(date(2024,1,8),'A','B'),(0.,'incomplete_half_or_full'))

    def test_same_day_target_and_duplicate_history_rejected(self):
        state=TeamHistory()
        for i in range(6):
            state.observe(date(2024,1,i+1),'A','B',(0,0),(1,0))
        with self.assertRaises(ValueError):
            state.signal(date(2024,1,6),'A','B')
        with self.assertRaises(ValueError):
            state.observe(date(2024,1,6),'B','C',(0,0),(1,1))

    def test_conditional_tilt_can_change_top1_preserving_every_total_and_zero_cell(self):
        m=np.zeros((5,5))
        m[0,1],m[1,0],m[1,1],m[0,0],m[2,2]=.27,.25,.2,.15,.13
        for signal in (-2.,2.):
            b=adjusted(m,signal,.4)
            t=np.add.outer(np.arange(5),np.arange(5)).ravel()
            np.testing.assert_allclose(np.bincount(t,weights=b.ravel()),np.bincount(t,weights=m.ravel()),atol=1e-15)
            np.testing.assert_array_equal(b==0,m==0)
            for line in (1.75,2.,2.5,3.25):
                self.assertEqual(picks(b,line)['total'],picks(m,line)['total'])
        self.assertEqual(picks(m,2.5)['first'],'0-1')
        self.assertEqual(picks(adjusted(m,2.,.4),2.5)['first'],'1-0')
        self.assertIs(adjusted(m,0.,.4),m)
        self.assertIs(adjusted(m,2.,0.),m)

    def test_conditional_likelihood_and_signed_fit(self):
        m=np.ones((5,5))/25
        home=training_example(m,1.,(2,0))
        away=training_example(m,1.,(0,2))
        self.assertAlmostEqual(sum(home[1]),1.)
        self.assertGreater(fit([home]*10),0.)
        self.assertLess(fit([away]*10),0.)
        self.assertAlmostEqual(fit([home,away]*8),0.)
        self.assertAlmostEqual(fit([training_example(m,0.,(2,0))]),0.)
