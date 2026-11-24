import unittest
from datetime import date, timedelta

from research_program.prior_dismissals import card_pair, fit_season, forecast


def histories(n=90):
    start = date(2021, 7, 1)
    return [dict(day=start+timedelta(days=i), home='A' if i%2 else 'B',
                 away='B' if i%2 else 'A', goals=(2,1), HR='0', AR='0') for i in range(n)]


class PriorMatchAblationTests(unittest.TestCase):
    def test_equal_or_unknown_kept_in_both(self):
        rows=histories()
        rows[0].update(HR='1',AR='1')
        rows[1].update(HR='-',AR='0')
        rows[2].update(HR='0',AR=None)
        fit=fit_season(rows,2021)
        self.assertEqual(fit[1],{'baseline':90,'candidate':90,'known_equal':88,'unknown_cards':2})
        prediction=forecast(fit,date(2021,11,20),'A','B',2.75)
        self.assertEqual(prediction['baseline'],prediction['candidate'])
        self.assertFalse(prediction['candidate_fallback'])
        self.assertEqual(len(set(prediction['candidate']['top3'])),3)

    def test_invalid_red_counts_have_no_count_interpretation(self):
        for x in ('1.0','-1',None,'',True,'３'):
            self.assertIsNone(card_pair(x,'0'))
        self.assertEqual(card_pair(' 2 ','0'),(2,0))

    def test_only_unequal_prior_matches_omitted_in_both_directions(self):
        rows=histories()
        for i in range(8):
            rows[i].update(HR=str(i%2),AR=str(1-i%2),goals=(0,6))
        models,n=fit_season(rows,2021)
        self.assertEqual(n['excluded_unequal'],8)
        ref,_=fit_season(rows[8:],2021)
        self.assertEqual(models[1].snapshot(),ref[0].snapshot())
        self.assertNotEqual(models[1].snapshot(),models[0].snapshot())

    def test_future_targets_are_never_read(self):
        rows=histories()
        future={'day':date(2021,11,20)}
        a=fit_season(rows,2021)
        b=fit_season(rows+[future],2021)
        self.assertEqual(forecast(a,date(2021,11,20),'A','B'),forecast(b,date(2021,11,20),'A','B'))

    def test_fallback_for_rare_club_or_short_filtered_history(self):
        rows=histories()
        rows[0].update(home='C',HR='1')
        fit=fit_season(rows,2021)
        for candidate in (fit, fit_season(rows[:60],2021)):
            p=forecast(candidate,date(2021,11,20),'C','A')
            self.assertTrue(p['candidate_fallback'])
            self.assertEqual(p['baseline'],p['candidate'])
        self.assertEqual(forecast(fit,date(2021,11,20),'X','Y')['status'],'unresolved_club')
        self.assertEqual(forecast(fit_season(rows[:59],2021),date(2021,11,20),'A','B')['status'],
                         'short_baseline_history')

    def test_missing_line_is_abstention_for_totals_only(self):
        p=forecast(fit_season(histories(),2021),date(2021,11,20),'A','B')
        self.assertEqual(p['status'],'paired')
        self.assertIsNone(p['candidate']['total'])
        self.assertEqual(p['candidate']['first'],p['candidate']['top3'][0])
