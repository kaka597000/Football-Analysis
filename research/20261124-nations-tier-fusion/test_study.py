"""Coverage gates, archive identity, original clocks and independent scoring denominators."""
from contextlib import redirect_stderr
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
import sys
import unittest
from unittest.mock import patch

import numpy as np

from archive_inputs import same_fixture, the_odds_inputs
import labels
import study


class MemoryBundle:
    def __init__(self, entries, at='2026-11-14T23:30:00Z'):
        self.at = labels.aware(at)
        self.files = {key: json.dumps(value).encode() for key, value in entries.items()}
        self.raw = {sha256(value).hexdigest(): value for value in self.files.values()}

    def value(self, name):
        return json.loads(self.files[name])


def odds_case():
    kickoff = '2026-11-15T19:45:00Z'
    captured = '2026-11-15T16:47:00Z'
    quoted = '2026-11-15T16:45:00Z'
    url = 'https://api.the-odds-api.com/v4/sports/soccer_uefa_nations_league/odds'
    prices = {'home':2.1, 'draw':3.25, 'away':3.6}
    outputs = [{'name':n, 'price':prices[k]} for k,n in [('home','Alpha'),('draw','Draw'),('away','Bravo')]]
    book = dict(key='pinnacle', last_update=quoted, markets=[
        dict(key='h2h', outcomes=outputs),
        dict(key='totals', outcomes=[dict(name='Over',point=2.5,price=1.95),dict(name='Under',point=2.5,price=1.9)])
    ])
    event = dict(id='football01', sport_key='soccer_uefa_nations_league', commence_time=kickoff,
                 home_team='Alpha', away_team='Bravo', bookmakers=[book])
    events = [event]
    source_hash = sha256(json.dumps(events).encode()).hexdigest()
    receipt = dict(sha256=source_hash, url=url, captured_at=captured, requested_at='2026-11-15T16:46:58Z')
    bundle = MemoryBundle({'capture/odds.json':events, 'capture/source-metadata.json':dict(odds=receipt)})
    shared = dict(match_id='espn:1', bookmaker='pinnacle', provider_match_id='football01',
                  source_sha256=source_hash, source_url=url, quoted_at=quoted, query_time=captured,
                  period='regulation_90')
    quote = dict(shared, line=2.5, over_odds=1.95, under_odds=1.9)
    snaps = [dict(shared,market='1x2',line=None,decimal_odds=prices),
             dict(shared,market='totals',line=2.5,decimal_odds={'over':1.95,'under':1.9})]
    f = dict(match_id='espn:1', kickoff_at=kickoff, cutoff_at='2026-11-15T17:00:00Z', market_snapshots=snaps)
    return bundle, {'names':{'home':{'alpha'}, 'away':{'bravo'}}}, f, quote


class MarketEvidence(unittest.TestCase):
    def setUp(self):
        self.bundle, self.row, self.fixture, self.quote = odds_case()

    def run_comparison(self):
        return the_odds_inputs(self.bundle, self.row, self.fixture, self.quote)

    def test_independent_market_uses_aligned_two_market_response(self):
        grid, issue = self.run_comparison()
        self.assertIsNone(issue)
        self.assertEqual(grid.shape, (51,51))
        self.assertAlmostEqual(grid.sum(), 1.)

    def test_reversed_home_away_is_rejected(self):
        self.row['names'] = {'home':{'bravo'}, 'away':{'alpha'}}
        with self.assertRaisesRegex(ValueError, 'SIDED_PROVIDER_EVENT'):
            self.run_comparison()

    def test_source_digest_and_original_cutoff_are_required(self):
        self.fixture['cutoff_at'] = '2026-11-15T16:46:00Z'
        with self.assertRaisesRegex(ValueError, 'CAPTURE_RECEIPT_MISSING'):
            self.run_comparison()
        self.quote['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'ODDS_BYTES_MISSING'):
            self.run_comparison()

    def test_changed_raw_two_sided_price_is_rejected(self):
        self.fixture['market_snapshots'][1]['decimal_odds']['under'] = 1.91
        with self.assertRaisesRegex(ValueError, 'TWO_SIDED_LINE_UNCONFIRMED'):
            self.run_comparison()

    def test_missing_wdl_saves_original_total_but_has_no_expert(self):
        self.fixture['market_snapshots'].pop(0)
        grid, issue = self.run_comparison()
        self.assertIsNone(grid)
        self.assertEqual(issue, 'NO_ALIGNED_TWO_MARKET_CAPTURE')

    def test_conflicting_cross_provider_id_is_rejected(self):
        for target in (self.quote,self.fixture['market_snapshots'][0]):
            with self.subTest(target=target.get('market')):
                old = target['provider_match_id']
                target['provider_match_id'] = 'other'
                with self.assertRaisesRegex(ValueError, 'PROVIDER_ID_CONFLICT'):
                    self.run_comparison()
                target['provider_match_id'] = old

    def test_different_stage_and_sided_id_are_rejected(self):
        f = dict(match_id='espn:4',competition='uefa.nations',cohort='uefa.nations:A:league-phase',
                 stage='league-phase',home='espn:aa',away='espn:bb',kickoff_at='2026-11-15T17:00:00Z')
        self.assertTrue(same_fixture(f, dict(f,kickoff_at='2026-11-15T18:00:00+01:00')))
        for new in (dict(stage='playoff'),dict(away='espn:aa'),dict(kickoff_at='2026-11-15T17:01:00Z')):
            self.assertFalse(same_fixture(f,dict(f,**new)))


class CalendarAndScoring(unittest.TestCase):
    def setUp(self):
        self.base = np.array([[.4,.1],[.3,.2]])
        self.market = np.array([[.8,.08],[.1,.02]])
        tier_list = list('AAAAAAAABBBBBBCCCCCCCCDD')
        self.early = {str(i):dict(tier=t,date=f'2026-11-{12+i%3}',base=self.base,market=self.market)
                      for i,t in enumerate(tier_list)}
        self.known = {k:dict(score=[0,0],observed_at='2026-11-14T22:00:00Z',committed_at='2026-11-15T07:55:00Z')
                      for k in self.early}

    def test_fit_cap_and_tier_support(self):
        weights = study.fit(self.early,self.known)
        self.assertEqual(weights['pairs'],24)
        self.assertEqual(weights['global_weight'],.5)
        self.assertEqual(weights['weights'],dict(A=1/3, B=.3, C=1/3, D=0.))

    def test_small_panel_and_single_date_both_disable_all_weights(self):
        smaller = dict(list(self.early.items())[:15])
        for rows in (smaller, {k:dict(v,date='2026-11-14') for k,v in self.early.items()}):
            self.assertEqual(study.fit(rows,self.known)['weights'], dict.fromkeys('ABCD',0.))

    def test_held_out_date_and_late_training_result_rejected(self):
        wrong = deepcopy(self.early)
        wrong['0']['date'] = '2026-11-15'
        with self.assertRaisesRegex(ValueError,'held-out'):
            study.fit(wrong,self.known)
        for field in ('observed_at','committed_at'):
            with self.subTest(field=field):
                values = deepcopy(self.known)
                values['0'][field] = '2026-11-15T08:00:01Z'
                with self.assertRaisesRegex(ValueError,'fit origin'):
                    study.fit(self.early,values)

    def test_pre_origin_missing_market_and_rare_tier_retain_baseline(self):
        weights = study.fit(self.early,self.known)
        row = dict(tier='A',cutoff='2026-11-15T12:00:00Z',base=self.base,market=self.market)
        for changes in (dict(cutoff='2026-11-10T08:00:00Z'),dict(market=None),dict(tier='D')):
            matrices, w = study.forecast(dict(row,**changes),weights)
            self.assertEqual(w,0)
            self.assertIs(matrices['tier_pool'],self.base)
        matrices, w = study.forecast(row,weights)
        self.assertEqual(w,1/3)
        np.testing.assert_allclose(matrices['tier_pool'], self.base*(1-w)+self.market*w)
        np.testing.assert_array_equal(matrices['market_reference'], self.market)

    def test_incompatible_score_support_rejected(self):
        row = dict(tier='A', cutoff='2026-11-15T12:00:00Z', base=self.base, market=np.array([[.5,.5]]))
        with self.assertRaisesRegex(ValueError,'same score support'):
            study.forecast(row,study.fit(self.early,self.known))

    def test_top1_top3_and_push_are_scored_separately(self):
        picks = study.selection(self.base,1.)
        o = study.outcomes(self.base,picks,(1,0),1.)
        self.assertFalse(o['top1'])
        self.assertTrue(o['top3'])
        self.assertEqual(o['settlement'],'PUSH')
        self.assertIsNone(o['total'])
        self.assertAlmostEqual(o['score_log_loss'],-np.log(.3))

    def test_quarter_line_keeps_five_states_and_omits_only_pushes(self):
        picks = dict(primary='1-1',top3=['1-1','0-0','1-0'],wdl='draw',total='over')
        rows = []
        for line in (.5,.75,1.,1.25,1.5,None):
            c = dict(picks,total=None) if line is None else picks
            o = study.outcomes(self.base,c,(1,0),line)
            rows.append(dict(has_issue=True, expert=False,weight=0.,has_line=line is not None,score=(1,0),
                             choices=dict(original_test=c,tier_pool=c),arms=dict(original_test=o,tier_pool=o)))
        summary = study.aggregate(rows)
        self.assertEqual(summary['original_lines'],5)
        self.assertEqual(summary['tier_pool']['total'],dict(n=4,hits=1))
        self.assertEqual(summary['tier_pool']['states'],dict.fromkeys(study.STATES,1))
        self.assertEqual(summary['market_reference']['total'],dict(n=0,hits=None))

    def test_root_and_nested_indexes_use_only_selected_window(self):
        indexed = dict(match_id='espn:10',observed_at='2026-11-14T23:00:00Z',event_status='FT',
                       score_basis='regulation_90',source_url='https://site.api.espn.com/soccer/uefa.nations/summary?event=10',
                       home_goals=1,away_goals=0)
        f = dict(kickoff_at='2026-11-14T20:00:00Z',home='espn:a',away='espn:b',home_name='Alpha',away_name='Bravo')
        raw = dict(header=dict(competitions=[dict(competitors=[
            dict(homeAway='home',team=dict(id='a')),dict(homeAway='away',team=dict(id='b'))])]))
        indexed['source_sha256'] = sha256(json.dumps(raw).encode()).hexdigest()
        for path in ('verified-results.json','reviews/verified-results.json'):
            bundle = MemoryBundle({path:[indexed],'raw.json':raw})
            with patch.object(labels,'Bundle',side_effect=[bundle,MemoryBundle({}),MemoryBundle({})]) as opened:
                with patch.object(labels,'regulation_result',return_value=[1,0]):
                    seen, issues = labels.certified('.',{'espn:10':{'f':f}},training=True)
            self.assertEqual([c.args[1] for c in opened.call_args_list],['r12','r13','r14'])
            self.assertEqual(seen['espn:10']['score'],[1,0])
            self.assertEqual(issues,{})

    def test_grading_refuses_an_unlocked_ref_before_opening_data(self):
        argv = ['study','grade','--data','absent','--out','unused','--seal-commit','HEAD']
        with patch.object(sys,'argv',argv), patch.object(study,'build') as build, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                study.main()
        build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
