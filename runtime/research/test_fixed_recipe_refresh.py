"""Real retained-source validation and fixed-recipe refresh regression checks."""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixed_recipe_refresh as refresh

DATA = Path('/workspace/WCPP/reports/auto-optimization-20261101/history')
CURRENT = Path('/workspace/WCPP/models/weighted-20261012/ita.1/artifact.json')
TEST = Path('/workspace/WCPP/models/ml-20261012/ita.1/artifact.json')


class RefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('WCPP_SKIP_PRIVATE_REFRESH_TESTS') == '1':
            raise unittest.SkipTest('private integration assets explicitly disabled; portable tests still run')
        required = (CURRENT, TEST, DATA/'rows.json', DATA/'coverage.json', DATA/'raw')
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            raise unittest.SkipTest('private integration assets unavailable: ' + ', '.join(missing))
        cls.current = refresh.read(CURRENT)
        cls.test = refresh.read(TEST)
        cls.rows = refresh.read(DATA/'rows.json')
        missing_raw = [r['score_evidence']['source_path'] for r in cls.rows
                       if not (DATA / r['score_evidence']['source_path']).is_file()
                       and not Path(r['score_evidence']['source_path']).is_file()]
        if missing_raw:
            raise unittest.SkipTest('private retained responses unavailable: ' + ', '.join(missing_raw))
        cls.row = next(r for r in cls.rows if r['cohort'] == 'ita.1')
        cls.cutoff = datetime.now(timezone.utc)
        cls.raw = DATA/'raw'

    def rejected(self, **fields):
        row = copy.deepcopy(self.row)
        row.update(fields)
        with self.assertRaises((ValueError, KeyError)):
            refresh.validate_new(row, 'ita.1', self.cutoff, self.raw)

    def test_real_source_accepted(self):
        self.assertEqual(refresh.validate_new(self.row, 'ita.1', self.cutoff, self.raw)['event'], self.row['match_id'])

    def test_cross_cohort(self): self.rejected(cohort='swe.1')
    def test_cross_competition(self): self.rejected(competition='swe.1')
    def test_unknown_neutral(self): self.rejected(neutral=None)
    def test_neutral_baseline_unsupported(self): self.rejected(neutral=True)
    def test_future_observed(self): self.rejected(observed_at='2099-01-01T00:00:00+00:00')
    def test_future_date(self): self.rejected(date='2099-01-01')
    def test_bad_score(self): self.rejected(home_goals=-1)
    def test_boolean_score(self): self.rejected(home_goals=True)
    def test_score_conflict(self): self.rejected(home_goals=self.row['home_goals']+1)
    def test_not_finished(self): self.rejected(status='LIVE')
    def test_not_ninety(self): self.rejected(score_basis='extra_time')
    def test_identity_conflict(self): self.rejected(home='espn:999999')
    def test_unreviewed_source(self): self.rejected(source_url='https://unreviewed.invalid/x')
    def test_no_fake_xg_zero(self): self.rejected(home_xg=0, away_xg=0)

    def test_future_evidence(self):
        ev = copy.deepcopy(self.row['score_evidence']); ev['query_time'] = '2099-01-01T00:00:00+00:00'
        self.rejected(score_evidence=ev)

    def test_same_day_ft_before_ninety_minutes_is_rejected(self):
        row = copy.deepcopy(self.row)
        early = refresh.legacy.aware(row['dateUTC']) + refresh.timedelta(minutes=30)
        row['observed_at'] = early.isoformat()
        for name in ('score_evidence','neutral_evidence'): row[name]['query_time'] = early.isoformat()
        with self.assertRaisesRegex(ValueError, 'precedes raw kickoff plus 90'):
            refresh.validate_new(row, 'ita.1', self.cutoff, self.raw)

    def test_score_query_before_finish_is_rejected(self):
        row = copy.deepcopy(self.row)
        row['score_evidence']['query_time'] = row['dateUTC']
        with self.assertRaisesRegex(ValueError, 'precedes raw kickoff plus 90'):
            refresh.validate_new(row, 'ita.1', self.cutoff, self.raw)

    def test_raw_hash_conflict(self):
        ev = copy.deepcopy(self.row['score_evidence']); ev['response_sha256'] = '0'*64
        self.rejected(score_evidence=ev)

    def test_conflicting_id(self):
        old = copy.deepcopy(self.current['history'])
        duplicate = copy.deepcopy(self.row)
        duplicate['match_id'] = old[0]['match_id']
        with self.assertRaisesRegex(ValueError, 'duplicate identity/score conflict'):
            refresh.merge_history(old, [duplicate], 'ita.1', self.cutoff, self.raw)

    def test_duplicate_same_event_is_deduplicated(self):
        merged, _ = refresh.merge_history(self.current['history'], [self.row, self.row], 'ita.1', self.cutoff, self.raw)
        self.assertEqual(len(merged), len(self.current['history'])+1)

    def test_preserves_real_old_features(self):
        old = copy.deepcopy(self.current['history'])
        old[0]['sentinel_existing_feature'] = {'real': 0.71, 'unknown': None}
        merged, _ = refresh.merge_history(old, [self.row], 'ita.1', self.cutoff, self.raw)
        self.assertEqual({r['match_id']:r for r in merged}[old[0]['match_id']], old[0])
        self.assertNotIn('home_xg', merged[-1])

    def test_incomplete_coverage_blocks(self):
        result = refresh.coverage_check(refresh.read(DATA/'coverage.json'), 'ita.1',
                                        refresh.date(2026,9,21), refresh.date(2026,10,31), [self.row])
        self.assertEqual(result['status'], 'PARTIAL_BLOCKED')

    def test_features_exclude_target_and_same_day(self):
        # Same-date labels never affect the target feature vector.
        rows = copy.deepcopy(self.current['history'])
        fixture = rows[-1]
        rates = (1.4, 1.1)
        original = refresh.ml.feature_matrix(rows, fixture, rates)
        for row in rows:
            if row['date'] >= fixture['date']:
                row['home_goals'] = 29; row['away_goals'] = 28
        np.testing.assert_equal(original, refresh.ml.feature_matrix(rows, fixture, rates))

    def test_fixed_current_refit_and_reload(self):
        before = refresh.legacy.digest(self.current)
        merged, _ = refresh.merge_history(self.current['history'], [self.row], 'ita.1', self.cutoff, self.raw)
        with patch.object(refresh.weighted, 'train', side_effect=AssertionError('no weight training')):
            candidate = refresh.current_refit(self.current, merged, self.cutoff.isoformat(), 'diagnostic')
        for key in ('weights', 'experts', 'config', 'code_hash', 'acceptance'):
            self.assertEqual(candidate[key], self.current[key])
        for key in ('selected_recipe', 'execution_recipe', 'config', 'code_hash', 'form', 'context_coefficients'):
            self.assertEqual(candidate['anchor'][key], self.current['anchor'][key])
        self.assertEqual(before, refresh.legacy.digest(self.current))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'current.json'; refresh.write_new(path,candidate)
            loaded = refresh.read(path)
            a = refresh.offline_replay(self.current, self.test, self.row)
            b = refresh.offline_replay(loaded, self.test, self.row)
            self.assertNotEqual(a['current']['matrix_hash'], b['current']['matrix_hash'])
            self.assertAlmostEqual(b['current']['sum'], 1.)
            with self.assertRaises(FileExistsError): refresh.write_new(path,candidate)

    def test_ml_fixed_alpha(self):
        merged, _ = refresh.merge_history(self.test['history'], [self.row], 'ita.1', self.cutoff, self.raw)
        with patch.object(refresh.ml, 'train', side_effect=AssertionError('no alpha selection')):
            candidate, report = refresh.ml_refit(self.test, merged, self.cutoff.isoformat(), 'diagnostic')
        for key in ('alpha','config','feature_names','code_hash','acceptance'):
            self.assertEqual(candidate[key],self.test[key])
        self.assertTrue(all(f['fit_end'] < f['first_target'] for f in report['folds']))
        self.assertGreater(report['training_matches'], 30)
        loaded = refresh.ml.lgb.Booster(model_str=candidate['booster_model'])
        self.assertEqual(loaded.feature_name(), refresh.ml.FEATURES)

    def test_new_prediction_entry_rejects_neutral(self):
        with self.assertRaisesRegex(ValueError, 'neutral unsupported'):
            refresh.predict_current(self.current, {'neutral':True})


class PortableValidationTests(unittest.TestCase):
    """Explicitly synthetic protocol fixtures; no real data or training claims."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw_root = Path(self.tmp.name)/'raw'
        self.raw_root.mkdir()
        self.raw_path = self.raw_root/'synthetic-summary.json'
        self.payload = {'synthetic_test_fixture': True, 'header': {
            'id':'90001', 'date':'2020-01-02T12:00:00Z', 'league':{'slug':'ita.1'},
            'competitions':[{'neutralSite':False,
                'status':{'type':{'completed':True,'name':'STATUS_FULL_TIME'}},
                'competitors':[
                    {'homeAway':'home','team':{'id':'1'},'score':'2',
                     'linescores':[{'displayValue':'1'},{'displayValue':'1'}]},
                    {'homeAway':'away','team':{'id':'2'},'score':'1',
                     'linescores':[{'displayValue':'0'},{'displayValue':'1'}]}]}]}}
        self.raw_path.write_text(json.dumps(self.payload))
        self.row = {'match_id':'synthetic:90001','cohort':'ita.1','competition':'ita.1',
                    'stage':'regular','date':'2020-01-02','home':'espn:1','away':'espn:2',
                    'home_goals':2,'away_goals':1,'neutral':False,'score_basis':'regulation_90',
                    'status':'FT','observed_at':'2020-01-02T15:00:00+00:00',
                    'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/summary?event=90001',
                    'score_evidence':{'source_key':'espn:90001','query_time':'2020-01-02T15:00:00+00:00',
                                      'source_path':str(self.raw_path),'response_sha256':refresh.sha256(self.raw_path)},
                    'neutral_evidence':{'query_time':'2020-01-02T15:00:00+00:00','neutral_site':False,
                                        'response_sha256':refresh.sha256(self.raw_path)}}
        self.cutoff = refresh.legacy.aware('2020-01-03T00:00:00+00:00')

    def validate(self, row=None):
        return refresh.validate_new(row or self.row, 'ita.1', self.cutoff, self.raw_root)

    def reject(self, **changes):
        row=copy.deepcopy(self.row);row.update(changes)
        with self.assertRaises(ValueError): self.validate(row)

    def test_synthetic_valid_label(self):
        self.assertEqual(self.validate()['event'],'espn:90001')

    def test_cross_cohort_rejected(self): self.reject(cohort='swe.1')
    def test_unknown_neutral_rejected(self): self.reject(neutral=None)
    def test_unsupported_neutral_rejected(self): self.reject(neutral=True)
    def test_bad_score_rejected(self): self.reject(home_goals=-1)
    def test_boolean_score_rejected(self): self.reject(away_goals=True)
    def test_not_regulation_rejected(self): self.reject(score_basis='extra_time')
    def test_unfinished_rejected(self): self.reject(status='LIVE')
    def test_fake_zero_xg_rejected(self): self.reject(home_xg=0,away_xg=0)
    def test_raw_score_conflict_rejected(self): self.reject(home_goals=3)
    def test_future_observation_rejected(self): self.reject(observed_at='2099-01-01T00:00:00+00:00')

    def test_ft_before_ninety_minutes_rejected(self):
        row=copy.deepcopy(self.row)
        row['observed_at']='2020-01-02T12:30:00+00:00'
        for name in ('score_evidence','neutral_evidence'):
            row[name]['query_time']=row['observed_at']
        with self.assertRaisesRegex(ValueError,'precedes raw kickoff plus 90'): self.validate(row)

    def test_query_before_finish_rejected(self):
        row=copy.deepcopy(self.row)
        row['score_evidence']['query_time']='2020-01-02T12:30:00+00:00'
        with self.assertRaisesRegex(ValueError,'precedes raw kickoff plus 90'): self.validate(row)

    def test_three_periods_rejected_even_when_hash_matches(self):
        self.payload['header']['competitions'][0]['competitors'][0]['linescores'].append({'displayValue':'0'})
        self.raw_path.write_text(json.dumps(self.payload))
        for name in ('score_evidence','neutral_evidence'):
            self.row[name]['response_sha256']=refresh.sha256(self.raw_path)
        with self.assertRaisesRegex(ValueError,'two regulation period'): self.validate()

    def test_duplicate_id_conflict_rejected(self):
        old=copy.deepcopy(self.row)
        old['date']='2020-01-01';old['score_evidence']['source_key']='espn:90000'
        with self.assertRaisesRegex(ValueError,'duplicate identity/score conflict'):
            refresh.merge_history([old],[self.row],'ita.1',self.cutoff,self.raw_root)

    def test_coverage_requires_date_inventory_and_verified_event_ids(self):
        day=refresh.date(2020,1,2)
        scope={'checked_dates':[day.isoformat()],'expected_finished_event_ids':['espn:90001'],
               'inventory_verified':True,'missing':[],'unresolved':[]}
        complete={'cohorts':{'ita.1':scope}}
        self.assertEqual(refresh.coverage_check(complete,'ita.1',day,day,[self.row])['status'],
                         'COMPLETE_RESEARCH_ONLY')
        for key,value in [('checked_dates',[]),('inventory_verified',False),
                          ('expected_finished_event_ids',[]),('unresolved',['synthetic_missing'])]:
            broken=copy.deepcopy(complete);broken['cohorts']['ita.1'][key]=value
            with self.subTest(key=key):
                self.assertEqual(refresh.coverage_check(broken,'ita.1',day,day,[self.row])['status'],
                                 'PARTIAL_BLOCKED')


if __name__ == '__main__': unittest.main()
