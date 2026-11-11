import copy
from datetime import date, timedelta
import unittest
import numpy as np
from rolling_state import run, stamp


def result(mid, day, h=1, a=1, known=None):
    known = known or day+'T20:00:00+00:00'
    return dict(match_id=mid, cohort='test', competition='test', date=day,
        kickoff_at=day+'T10:00:00+00:00', home='A', away='B', neutral=False,
        home_goals=h, away_goals=a, score_basis='regulation_90', result_available_at=known,
        result_evidence={'status': 'VERIFIED_RAW_REGULATION_90', 'captured_at': known,
                         'sha256': 'a'*64, 'source_url': 'https://example.org/results'})


def target(mid, day):
    return dict(match_id=mid, cohort='test', competition='test', date=day,
        kickoff_at=day+'T10:00:00+00:00', home='A', away='B', neutral=False,
        fixture_evidence={'verified': True, 'captured_at': '2026-10-31T00:00:00+00:00',
                          'sha256': 'b'*64, 'source_url': 'https://example.org/fixtures'})


class ToyCore:
    """Synthetic state-sensitive contract double, never a performance benchmark."""
    def fit_core(self, rows, **config):
        return [sum(r['home_goals'] for r in rows)/len(rows), sum(r['away_goals'] for r in rows)/len(rows)]

    def rates(self, core, rows):
        for row in rows:
            assert not {'home_goals', 'away_goals', 'home_xg', 'result'} & row.keys()
        return np.asarray([core for _ in rows])

    def matrix(self, rates):
        m = np.full((5, 5), .001)
        m[min(4, int(rates[0])), min(4, int(rates[1]))] += 1
        return m/m.sum()


class RollingTests(unittest.TestCase):
    def setUp(self):
        self.history = [result('h'+str(i), (date(2026, 7, 1)+timedelta(days=i)).isoformat()) for i in range(80)]
        self.fixtures = [target('x', '2026-11-01'), target('y', '2026-11-01'),
                         target('z', '2026-11-02'), target('w', '2026-11-03')]
        self.results = self.history+[result('x', '2026-11-01', 20, 0),
                                    result('y', '2026-11-01', 10, 0), result('z', '2026-11-02', 0, 5)]

    def execute(self, fixtures=None, results=None, **kwargs):
        return run(self.fixtures if fixtures is None else fixtures,
            self.results if results is None else results, origin_at='2026-11-01T00:00:00+00:00',
            evaluation_at='2026-11-10T00:00:00+00:00', engine=ToyCore(), **kwargs)

    def test_all_same_day_use_identical_prior_state(self):
        ps = self.execute()['predictions']['DAILY_ROLLING']
        self.assertEqual(ps[0]['state_hash'], ps[1]['state_hash'])
        self.assertEqual(ps[0]['rates'], ps[1]['rates'])
        self.assertEqual(ps[0]['state_n'], 80); self.assertEqual(ps[2]['state_n'], 82)

    def test_fixed_state_never_updates(self):
        ps = self.execute()['predictions']['FIXED_STATE']
        self.assertEqual(len({p['state_hash'] for p in ps}), 1)
        self.assertTrue(all(p['state_n'] == 80 for p in ps))

    def test_permutation_invariance(self):
        self.assertEqual(self.execute(), self.execute(list(reversed(self.fixtures)), list(reversed(self.results))))

    def test_future_labels_cannot_change_earlier_predictions(self):
        a = self.execute(); changed = copy.deepcopy(self.results); changed[-1]['home_goals'] = 30
        b = self.execute(results=changed)
        self.assertEqual(a['predictions']['DAILY_ROLLING'][:3], b['predictions']['DAILY_ROLLING'][:3])
        self.assertNotEqual(a['predictions']['DAILY_ROLLING'][3]['state_hash'], b['predictions']['DAILY_ROLLING'][3]['state_hash'])

    def test_same_day_poison_does_not_affect_other_fixture(self):
        changed = copy.deepcopy(self.results); changed[-3]['home_goals'] = 40
        self.assertEqual(self.execute()['predictions']['DAILY_ROLLING'][:2], self.execute(results=changed)['predictions']['DAILY_ROLLING'][:2])

    def test_delayed_result_waits_until_actual_capture(self):
        rows = copy.deepcopy(self.results); rows[-3]['result_available_at'] = '2026-11-02T01:00:00+00:00'
        rows[-3]['result_evidence']['captured_at'] = rows[-3]['result_available_at']
        ps = self.execute(results=rows)['predictions']['DAILY_ROLLING']
        self.assertEqual(ps[2]['state_n'], 81); self.assertEqual(ps[3]['state_n'], 83)

    def test_post_cutoff_correction_applies_only_later(self):
        r = copy.deepcopy(self.results[-3]); r['home_goals'] = 3
        r['result_available_at'] = '2026-11-02T13:00:00+00:00'; r['result_evidence']['captured_at'] = r['result_available_at']
        a = self.execute()['predictions']['DAILY_ROLLING']; b = self.execute(results=self.results+[r])['predictions']['DAILY_ROLLING']
        self.assertEqual(a[:3], b[:3]); self.assertNotEqual(a[3], b[3])

    def test_unverified_missing_timestamp_and_aet_do_not_update(self):
        rows = copy.deepcopy(self.results); rows[-3]['result_evidence']['status'] = 'UNKNOWN'
        del rows[-2]['result_available_at']; rows[-1]['score_basis'] = 'final_including_extra_time'
        self.assertTrue(all(p['state_n'] == 80 for p in self.execute(results=rows)['predictions']['DAILY_ROLLING']))

    def test_source_capture_cannot_be_backdated(self):
        rows = copy.deepcopy(self.results); rows[-3]['result_available_at'] = '2026-11-01T11:00:00+00:00'
        self.assertEqual(self.execute(results=rows)['predictions']['DAILY_ROLLING'][2]['state_n'], 81)

    def test_target_outcomes_and_xg_are_stripped(self):
        fs = copy.deepcopy(self.fixtures)
        for f in fs: f.update(home_goals=40, home_xg=100, result='future')
        self.assertEqual(self.execute()['predictions'], self.execute(fixtures=fs)['predictions'])

    def test_late_fixture_abstains_both_arms_without_dropping(self):
        fs = copy.deepcopy(self.fixtures); fs[0]['fixture_evidence']['captured_at'] = '2026-11-01T01:00:00+00:00'
        out = self.execute(fixtures=fs)
        for ps in out['predictions'].values():
            self.assertEqual(len(ps), 4); self.assertEqual(ps[0]['reason'], 'FIXTURE_NOT_YET_AVAILABLE')
        self.assertEqual(out['comparison']['N_common_scope'], 4)

    def test_insufficient_origin_cannot_create_unpaired_coverage(self):
        out = self.execute(results=self.results[1:])
        self.assertTrue(all(p['status'] == 'ABSTAIN' for ps in out['predictions'].values() for p in ps))

    def test_unknown_results_remain_pending(self):
        self.assertEqual(self.execute()['comparison']['A']['N_pending'], 1)

    def test_duplicate_targets_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'): self.execute(fixtures=self.fixtures+self.fixtures[:1])

    def test_same_timestamp_conflict_rejected(self):
        r = copy.deepcopy(self.history[0]); r['home_goals'] = 4
        with self.assertRaisesRegex(ValueError, 'ambiguous'): self.execute(results=self.results+[r])

    def test_cross_cohort_results_do_not_leak(self):
        r = result('other', '2026-10-30', 40, 40); r['cohort'] = 'other'
        self.assertEqual(self.execute()['predictions'], self.execute(results=self.results+[r])['predictions'])

    def test_input_objects_not_mutated(self):
        before = copy.deepcopy((self.fixtures, self.results)); self.execute()
        self.assertEqual(before, (self.fixtures, self.results))

    def test_target_result_identity_conflict_rejected(self):
        for key, value in [('home', 'WRONG'), ('cohort', 'other'), ('competition', 'other'),
                           ('date', '2026-10-01'), ('kickoff_at', '2026-11-01T09:00:00+00:00')]:
            rows = copy.deepcopy(self.results); rows[-3][key] = value
            if key == 'date': rows[-3]['kickoff_at'] = value+'T10:00:00+00:00'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identity mismatch'):
                self.execute(results=rows)

    def test_cross_cohort_same_id_rejected(self):
        r = copy.deepcopy(self.history[0]); r['cohort'] = 'other'
        with self.assertRaisesRegex(ValueError, 'identity mismatch'): self.execute(results=self.results+[r])

    def test_timezone_required(self):
        with self.assertRaisesRegex(ValueError, 'timezone'): stamp('2026-11-01T00:00:00')


if __name__ == '__main__':
    unittest.main()
