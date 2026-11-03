"""Synthetic software cases; none are football performance evidence."""
import copy
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data_contract import snapshot, validate


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'source.json').write_bytes(b'{"SYNTHETIC_ONLY":true}')
        self.now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
        self.target = {'match_id':'target', 'cohort':'eng.1', 'home_id':'h',
            'away_id':'a', 'cutoff_at':self.now.isoformat(),
            'kickoff_at':(self.now+timedelta(hours=10)).isoformat()}
        self.row = {'provider':'TEST_ONLY', 'cohort':'eng.1', 'match_id':'past',
            'entity_id':'h', 'entity_side':'home', 'field':'xg', 'value':1.2,
            'period':'regulation_90', 'event_status':'FT',
            'event_kickoff_at':(self.now-timedelta(days=2)).isoformat(),
            'observed_at':(self.now-timedelta(minutes=1)).isoformat(),
            'first_known_at':None, 'source_published_at':None,
            'evidence':{'url':'https://example.test/football', 'file':'source.json',
                'sha256':hashlib.sha256((self.root/'source.json').read_bytes()).hexdigest(),
                'query_time':(self.now-timedelta(minutes=1)).isoformat(),
                'identity_verified':True, 'source_tier':'L2'}}

    def build(self, rows, target=None):
        return snapshot(rows, target or self.target, self.root, provider='TEST_ONLY', now=self.now)

    def test_current_capture_can_support_future_without_inventing_first_known(self):
        result=self.build([self.row])
        self.assertEqual(result['features']['h']['xg']['n'],1)
        self.assertEqual(result['features']['h']['xg']['requested_n'],3)
        self.assertIsNone(result['observations'][0]['first_known_at'])
        self.assertFalse(result['claims']['historical_asof_certified'])

    def test_early_first_known_does_not_backdate_late_download(self):
        row=copy.deepcopy(self.row)
        row['first_known_at']=(self.now-timedelta(days=2)).isoformat()
        target=dict(self.target,cutoff_at=(self.now-timedelta(hours=1)).isoformat())
        self.assertEqual(self.build([row],target)['excluded']['captured_after_cutoff'],1)

    def test_file_tampering_and_traversal_rejected(self):
        row=copy.deepcopy(self.row);row['evidence']['file']='../source.json'
        with self.assertRaises(ValueError):self.build([row])
        (self.root/'source.json').write_text('changed')
        with self.assertRaises(ValueError):self.build([self.row])

    def test_missing_mismatched_and_future_capture_rejected(self):
        for change in ('missing','mismatch','future'):
            row=copy.deepcopy(self.row)
            if change=='missing':row.pop('observed_at')
            elif change=='mismatch':row['evidence']['query_time']=self.now.isoformat()
            else:
                row['observed_at']=(self.now+timedelta(seconds=1)).isoformat()
                row['evidence']['query_time']=row['observed_at']
            with self.subTest(change=change), self.assertRaises((ValueError,KeyError)):
                self.build([row])

    def test_duplicate_or_conflicting_same_capture_is_not_two_matches(self):
        with self.assertRaises(ValueError):self.build([self.row,self.row])
        other=copy.deepcopy(self.row);other['value']=2.
        with self.assertRaises(ValueError):self.build([self.row,other])

    def test_revised_value_selected_only_after_revised_capture(self):
        new=copy.deepcopy(self.row);new['observed_at']=self.now.isoformat()
        new['evidence']['query_time']=self.now.isoformat();new['value']=2.
        result=self.build([self.row,new])
        self.assertEqual(result['features']['h']['xg']['n'],1)
        self.assertEqual(result['features']['h']['xg']['mean'],2.)
        earlier=dict(self.target,cutoff_at=(self.now-timedelta(seconds=30)).isoformat())
        self.assertEqual(self.build([self.row,new],earlier)['features']['h']['xg']['mean'],1.2)

    def test_provider_and_competition_are_never_averaged_together(self):
        other=copy.deepcopy(self.row);other['provider']='OTHER';other['value']=20.
        wrong=copy.deepcopy(self.row);wrong['cohort']='usa.1'
        result=self.build([self.row,other,wrong])
        self.assertEqual(result['features']['h']['xg']['mean'],1.2)
        self.assertEqual(result['excluded']['provider_or_cohort_mismatch'],2)

    def test_own_match_future_and_too_early_full_time_rejected(self):
        row=copy.deepcopy(self.row);row['match_id']='target'
        self.assertEqual(self.build([row])['excluded']['not_prior_history_in_window'],1)
        row=copy.deepcopy(self.row);row['event_kickoff_at']=(self.now-timedelta(minutes=20)).isoformat()
        with self.assertRaises(ValueError):self.build([row])
        row=copy.deepcopy(self.row);row['event_status']='LIVE'
        with self.assertRaises(ValueError):self.build([row])

    def test_xga_requires_opposite_side_same_provider_same_match(self):
        row=copy.deepcopy(self.row);row['field']='xga'
        row['derived_from']={'provider':'TEST_ONLY','match_id':'past','field':'xg','side':'away'}
        self.assertEqual(self.build([row])['features']['h']['xga']['mean'],1.2)
        for key,value in [('provider','OTHER'),('match_id','different'),('side','home')]:
            bad=copy.deepcopy(row);bad['derived_from'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.build([bad])

    def market(self):
        row=copy.deepcopy(self.row)
        row.update(match_id='target',field='market_quote',event_kickoff_at=self.target['kickoff_at'])
        row['value']={'bookmaker':'TEST_BOOK','market':'totals','line':3.,
            'quoted_at':row['observed_at'],'odds':{'over':1.93,'under':1.98}}
        return row

    def test_market_requires_real_complete_prices_and_fresh_source_time(self):
        row=self.market()
        self.assertEqual(self.build([row])['features']['h']['market_quote']['n'],1)
        for change in ('missing_time','one_side','future','wrong_match','stale'):
            bad=copy.deepcopy(row)
            if change=='missing_time':bad['value'].pop('quoted_at')
            elif change=='one_side':bad['value']['odds'].pop('under')
            elif change=='future':bad['value']['quoted_at']=self.now.isoformat()
            elif change=='wrong_match':bad['match_id']='other'
            else:bad['value']['quoted_at']=(self.now-timedelta(minutes=2)).isoformat()
            with self.subTest(change=change):
                if change in ('missing_time','one_side','future'):
                    with self.assertRaises((KeyError,ValueError)):self.build([bad])
                else:self.assertEqual(len(self.build([bad])['observations']),0)

    def test_different_bookmakers_not_collapsed_to_one_quote(self):
        row=self.market();other=copy.deepcopy(row);other['value']['bookmaker']='OTHER_BOOK'
        self.assertEqual(self.build([row,other])['features']['h']['market_quote']['n'],2)

    def test_unofficial_absence_cannot_become_confirmed_out(self):
        row=copy.deepcopy(self.row);row.update(field='player_availability',player_id='p',
            match_id='target',event_kickoff_at=self.target['kickoff_at'],value={'status':'confirmed_out'})
        with self.assertRaises(ValueError):self.build([row])
        row['value']['status']='reported_out'
        self.assertEqual(self.build([row])['features']['h']['player_availability']['n'],1)

    def test_secret_urls_and_values_never_written_to_snapshot(self):
        for change in ('url','field','text'):
            row=copy.deepcopy(self.row)
            if change=='url':row['evidence']['url']='https://example.test/?api_key=SYNTHETIC_ONLY'
            elif change=='field':row['authorization']='SYNTHETIC_ONLY'
            else:row['note']='Bearer SYNTHETIC_ONLY'
            with self.subTest(change=change),self.assertRaises(ValueError):self.build([row])

    def test_negative_nonfinite_values_and_unreviewed_identity_rejected(self):
        for value in (-1, float('nan'), float('inf'), True):
            row=copy.deepcopy(self.row);row['value']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.build([row])
        row=copy.deepcopy(self.row);row['evidence']['identity_verified']=False
        with self.assertRaises(ValueError):self.build([row])

    def test_nonfinite_policy_cannot_disable_quote_expiry(self):
        for value in (float('nan'),float('inf'),True,-1):
            with self.subTest(value=value),self.assertRaises(ValueError):
                snapshot([self.market()],self.target,self.root,provider='TEST_ONLY',
                    market_max_age_seconds=value,now=self.now)


if __name__=='__main__':unittest.main()
