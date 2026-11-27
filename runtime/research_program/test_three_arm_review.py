"""First-issue accounting, Asian quarter lines and retained raw-result evidence."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

if __package__:
    from . import three_arm_review as reviewer
else:
    from research_program import three_arm_review as reviewer


class ThreeArmReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.batch = self.state / 'batch'
        (self.batch / 'freezes').mkdir(parents=True)
        (self.state / 'raw').mkdir()
        self.fixture = {'match_id': 'espn:123', 'home': 'espn:1', 'away': 'espn:2', 'cohort': 'bra.1',
                        'stage': 'regular', 'kickoff_at': '2020-02-10T15:00Z', 'cutoff_at': '2020-02-10T10:00Z'}
        self.market = {'bookmaker': 'pinnacle', 'total': {'line': 2.25}, 'match_id': 'espn:123',
                       'captured_at': '2020-02-10T09:58Z', 'total_quoted_at': '2020-02-10T09:56Z',
                       'h2h_quoted_at': '2020-02-10T09:55Z'}
        self.plan = self.freeze('three-arm-plan', 'plan', self.fixture, {'fixtures': [self.fixture]}, '10:01')
        self.write(self.batch / 'plan.json', self.plan)
        self.forecasts = {k: self.arm('2-0' if k != 'current' else '1-0', 'home', 'under' if k != 'current' else 'over')
                          for k in reviewer.ARMS}
        self.save_arms(self.forecasts)
        self.labels = self.state / 'normalized-results.jsonl'
        self.labels.write_text('')

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data))

    def freeze(self, track, mid, fixture, payload, stamp):
        record = {'track': track, 'match_id': mid, 'stage': 'prematch', 'revision': 1, 'evaluation_slot': 'FIRST_PREMATCH',
                  'recorded_at': '2020-02-10T' + stamp + 'Z', 'fixture': fixture, 'payload': payload}
        record['record_sha256'] = reviewer.digest(record)
        return record

    def arm(self, score, wdl, direction):
        matrix = [[0.] * 3 for _ in range(3)]
        h, a = map(int, score.split('-'))
        matrix[h][a] = 1.
        return {'primary': score, 'wdl': wdl, 'matrix': matrix, 'matrix_sha256': reviewer.digest(matrix),
                'total': {'line': 2.25, 'direction': direction} if direction else None}

    def save_arms(self, arms, **override):
        self.plan['payload'].update(markets={'espn:123': self.market}, artifact_sha256='a' * 64)
        self.plan.pop('record_sha256')
        self.plan['record_sha256'] = reviewer.digest(self.plan)
        self.write(self.batch / 'plan.json', self.plan)
        payload = {'arms': arms, 'market': self.market, 'plan_sha256': self.plan['record_sha256'], 'artifact_sha256': 'a' * 64}
        rec = self.freeze('three-arm-score', self.fixture['match_id'], dict(self.fixture, **override), payload, '12:00')
        self.write(self.batch / 'freezes/espn-123.json', rec)

    def add_result(self, goals=(2, 0), state='STATUS_FULL_TIME', season_type=None):
        f = self.fixture
        url = 'https://site.api.espn.com/apis/site/v2/sports/soccer/' + f['cohort'] + '/summary?event=123'
        comp = {'id': '123', 'date': f['kickoff_at'], 'status': {'period': 2, 'type': {'name': state, 'completed': True}},
                'competitors': [{'homeAway': side, 'team': {'id': f[side].split(':')[1]}, 'score': str(g)}
                                for side, g in zip(('home', 'away'), goals)]}
        header = {'id': '123', 'league': {'slug': f['cohort']}, 'competitions': [comp]}
        if season_type is not None:
            header['season'] = {'type': season_type}
        body = json.dumps({'header': header}).encode()
        checksum = hashlib.sha256(body).hexdigest()
        path = 'raw/' + checksum + '.json.gz'
        (self.state / path).write_bytes(gzip.compress(body))
        captured = '2020-02-10T18:12Z'
        r = dict(f, source_url=url, home_goals=goals[0], away_goals=goals[1], score_basis='regulation_90',
                 raw_reference={'sha256': checksum, 'path': path, 'captured_at': captured, 'source_url': url},
                 known_at=captured)
        with self.labels.open('a') as out:
            out.write(json.dumps(r) + '\n')
        return r

    def review(self):
        return reviewer.review(self.batch, self.state, datetime(2020, 2, 11, tzinfo=timezone.utc))

    def test_full_regulation_evidence_and_independent_hit_denominators(self):
        self.add_result()
        r = self.review()
        self.assertEqual((r['settled'], r['pending']), (1, 0))
        self.assertEqual(r['arms']['current']['total_settlements']['HALF_LOSS'], 1)
        self.assertEqual(r['arms']['market']['total_settlements']['HALF_WIN'], 1)
        self.assertEqual(r['arms']['market']['metrics']['total_strict_full_win']['hits'], 0)
        self.assertEqual(r['paired']['market_minus_current']['score_top1']['gained'], 1)
        self.assertEqual(r['paired']['market_minus_current']['wdl']['net_hits'], 0)
        self.assertIsNone(r['paired']['market_minus_current']['score_top1']['date_block_bootstrap_95pct_rate'])

    def test_push_and_missing_totals(self):
        self.add_result()
        self.market['total']['line'] = 2.
        for arm in self.forecasts.values():
            arm['total']['line'] = 2.
        self.save_arms(self.forecasts)
        r = self.review()['arms']['current']
        self.assertEqual(r['metrics']['score_top1']['n'], 1)
        self.assertEqual(r['real_line_coverage'], 1.)
        self.assertEqual(r['total_settlements']['PUSH'], 1)
        self.assertEqual(r['metrics']['total_strict_full_win']['n'], 0)
        self.market = None
        for arm in self.forecasts.values():
            arm['total'] = None
        self.save_arms(self.forecasts)
        r = self.review()['arms']['current']
        self.assertEqual((r['metrics']['wdl']['n'], r['real_line_coverage']), (1, 0.))

    def test_pending_does_not_turn_into_a_miss_and_unissued_remains_in_population(self):
        r = self.review()
        self.assertEqual((r['settled'], r['issued'], r['population']), (0, 1, 1))
        self.assertIsNone(r['arms']['current']['metrics']['score_top1']['accuracy'])
        (self.batch / 'freezes/espn-123.json').unlink()
        r = self.review()
        self.assertEqual((r['issued'], r['pending'], r['arms']['current']['forecast_coverage']), (0, 1, 0.))

    def test_conflicting_certified_scores_or_extra_time_remain_pending(self):
        self.add_result(state='STATUS_FINAL_AET')
        self.assertEqual(self.review()['pending'], 1)
        self.add_result()
        self.add_result(goals=(2, 1))
        self.assertIn('conflicting certified regulation scores', self.review()['rows'][0]['source_issues'])

    def test_raw_checksum_and_frozen_fixture_identity(self):
        label = self.add_result()
        raw_path = self.state / label['raw_reference']['path']
        raw_path.write_bytes(gzip.compress(b'{}'))
        self.assertIn('result raw source checksum mismatch', self.review()['rows'][0]['source_issues'])
        self.save_arms(self.forecasts, home='espn:2', away='espn:1')
        with self.assertRaisesRegex(ValueError, 'fixture identity'):
            self.review()

    def test_registered_j1_accepts_normalized_stage_with_raw_regular_season(self):
        self.fixture['cohort'] = 'jpn.1'
        fixture = dict(self.fixture, stage='Japanese J1 League')
        now = datetime(2020, 2, 11, tzinfo=timezone.utc)
        row = self.add_result(season_type=14287)
        verified = reviewer._certify(row, fixture, self.state, now)
        self.assertEqual((verified['home'], verified['away']), (2, 0))
        with self.assertRaisesRegex(ValueError, 'fixture identity'):
            reviewer._certify(dict(row, stage='playoffs'), fixture, self.state, now)
        other_phase = self.add_result(season_type=7)
        with self.assertRaisesRegex(ValueError, 'J1 regular-season identity'):
            reviewer._certify(other_phase, fixture, self.state, now)

    def test_independent_issue_scores_overrides_and_abstentions_outside_statistical_arms(self):
        self.add_result()
        cap = reviewer.read(self.batch / 'freezes/espn-123.json')
        entry = {'match_id': self.fixture['match_id'], 'score_freeze_sha256': cap['record_sha256'],
                 'baseline_primary': '1-0', 'selected_primary': '2-0', 'decision': 'OVERRIDE',
                 'decided_at': '2020-02-10T12:30Z', 'kickoff_at': self.fixture['kickoff_at']}
        def issue(stamp):
            record = self.freeze('three-arm-independent-comprehensive', 'batch-human', self.fixture,
                                 {'plan_sha256': self.plan['record_sha256'], 'rows': [entry]}, stamp)
            self.write(self.batch / 'comprehensive-decisions.json', record)
        issue('12:40')
        r = self.review()
        manual = r['independent_research_decisions']
        self.assertEqual(manual['paired_vs_original_current']['gained'], 1)
        self.assertEqual(r['arms']['current']['metrics']['score_top1']['hits'], 0)
        entry.update(decision='ABSTAIN', selected_primary=None)
        issue('12:40')
        self.assertEqual(self.review()['independent_research_decisions']['eligible'], 0)
        issue('15:10')
        with self.assertRaisesRegex(ValueError, 'first issue'):
            self.review()

    def test_repeated_cli_publishes_same_receipt_and_new_result_appends(self):
        out = self.batch / 'latest-review.json'
        args = ['review', '--batch', str(self.batch), '--state-dir', str(self.state), '--out', str(out)]
        with patch('sys.argv', args), patch('builtins.print'):
            reviewer.main()
            first = reviewer.read(out)
            reviewer.main()
            self.assertEqual(first, reviewer.read(out))
            self.add_result()
            reviewer.main()
        last = reviewer.read(out)
        self.assertEqual((first['pending'], last['settled']), (1, 1))
        self.assertNotEqual(first['freeze_receipt']['freeze_id'], last['freeze_receipt']['freeze_id'])
        self.assertEqual(len(list((self.batch / 'reviews').rglob('*.json'))), 2)

    def test_date_block_uncertainty_resamples_whole_matchdays(self):
        rows = [{'matchday': day, 'outcomes': {'current': {'wdl': False}, 'market': {'wdl': win}}}
                for day, win in [('a', True), ('a', False), ('b', True)]]
        gain = reviewer._paired(rows, 'current', 'market', 'wdl')
        self.assertEqual(gain['matchday_blocks'], 2)
        self.assertEqual(gain['date_block_bootstrap_95pct_rate'], [.5, 1.])

    def test_six_brazil_fixtures_across_utc_midnight_are_one_matchday(self):
        kickoffs = ['2026-11-18T22:30Z', '2026-11-18T23:00Z', '2026-11-18T23:30Z',
                    '2026-11-18T23:30Z', '2026-11-19T00:30Z', '2026-11-19T00:30Z']
        rows = [dict(reviewer._matchday({'cohort': 'bra.1', 'kickoff_at': stamp}),
                     outcomes={'current': {'wdl': False}, 'market': {'wdl': True}}) for stamp in kickoffs]
        self.assertEqual({r['kickoff_utc_date'] for r in rows}, {'2026-11-18', '2026-11-19'})
        self.assertEqual({r['matchday'] for r in rows}, {'2026-11-18'})
        paired = reviewer._paired(rows, 'current', 'market', 'wdl')
        self.assertEqual((paired['eligible'], paired['matchday_blocks']), (6, 1))
        self.assertIsNone(paired['date_block_bootstrap_95pct_rate'])
        other = reviewer._matchday({'cohort': 'eng.1', 'kickoff_at': kickoffs[-1]})
        self.assertEqual(other['matchday'], '2026-11-19')
