from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research.analysis_freeze import digest
from research_program import goal_state_forward as forward
from research_program.result_schedule_revision import revised_actual


def stamp(time):
    return f'2026-11-24T{time}Z'


def freeze(fixture, track, recorded_at, payload):
    record = dict(fixture=fixture, match_id=fixture['match_id'], track=track, recorded_at=recorded_at,
                  freeze_id=track + '-v1', revision=1, stage='prematch', previous_sha256=None,
                  evaluation_slot='FIRST_PREMATCH', payload=payload)
    return dict(record, record_sha256=digest(record))


class ScheduleRevisionTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        self.enterContext(patch.object(forward, 'datetime', wraps=datetime)).now.return_value = self.now
        self.mid = 'espn:101'
        self.fixture = dict(match_id=self.mid, cohort='usa.1', stage='MLS', home='espn:11', away='espn:22',
                            kickoff_at=stamp('01:00'), cutoff_at=stamp('00:05'))
        self.plan = freeze(dict(match_id='test-plan'), 'test-registration', stamp('00:00'),
                           dict(fixtures={self.mid: self.fixture}, track='test',
                                identity=dict(pairs={'market': ['market', 'candidate']})))
        self.record = freeze(self.fixture, 'test', stamp('00:10'),
                             dict(registration_sha256=self.plan['record_sha256'], line=None, forecasts={}))
        pre = dict(type=dict(name='STATUS_SCHEDULED', state='pre', completed=False))
        teams = [dict(homeAway='home', team=dict(id='11')), dict(homeAway='away', team=dict(id='22'))]
        game = dict(id='101', date=stamp('01:00'), status=deepcopy(pre), timeValid=True, competitors=teams)
        event = dict(id='101', date=stamp('01:00'), status=pre, season=dict(year=2026, type=13837,
                     slug='western-playoffs'), competitions=[game])
        self.board = dict(leagues=[dict(slug='usa.1')], events=[event])
        self.query = dict(competition='usa.1', status=200, path='board.json', query_date='20261123',
                          url='https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard?dates=20261123',
                          query_observed_at=stamp('00:03'))
        final = deepcopy(game)
        final.update(date=stamp('01:15'), status=dict(period=2, type=dict(state='post', name='STATUS_FULL_TIME',
                                                                          completed=True)))
        for team, score in zip(final['competitors'], ('3', '2')):
            team['score'] = score
        self.ft = dict(header=dict(id='101', league=dict(slug='usa.1'), competitions=[final],
                                   season=dict(year=2026, type=13837, name='MLS Western Playoffs')),
                       keyEvents=[dict(type=dict(id='80', type='kickoff'), period=dict(number=1),
                                       clock=dict(value=0), wallclock=stamp('01:25:33'))])
        self.result = dict(self.fixture, event_status='FT', score_basis='regulation_90',
                           home_goals=3, away_goals=2, observed_at=stamp('04:00'))

    def write(self, file, value):
        body = json.dumps(value).encode()
        (self.root / file).write_bytes(body)
        return dict(file=file, sha256=sha256(body).hexdigest())

    def label(self, board=None, ft=None, query_change=None):
        board_ref = self.write('board.json', self.board if board is None else board)
        query = dict(self.query, sha256=board_ref['sha256'], bytes=(self.root / 'board.json').stat().st_size)
        query.update(query_change or {})
        revision = dict(scoreboard=board_ref, queries=self.write('queries.json', [query]))
        ft_ref = self.write('result.json', self.ft if ft is None else ft)
        evidence = dict(ft_ref, url='https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/summary?event=101',
                        query_time=self.result['observed_at'], identity_verified=True, schedule_revision=revision)
        return dict(self.result, evidence=evidence)

    def grade(self, label, record=None):
        return revised_actual(label, self.record if record is None else record, self.root, self.now)

    def ledger(self):
        def ref(record):
            copy = self.write(record['freeze_id'] + '.json', record)
            return dict(file=copy['file'], id=record['freeze_id'], sha256=record['record_sha256'])
        self.write('receipts.json', dict(registration=ref(self.plan), matches={self.mid: ref(self.record)}))

    def test_real_review_dispatch_keeps_original_identity_population_and_bytes(self):
        self.ledger()
        label = self.label()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        original = deepcopy(label)
        self.assertEqual(self.grade(label), (3, 2))
        report = forward.review(self.root, [label], self.root, by_competition=True)['usa.1']
        self.assertEqual((report['registered'], report['settled']), (1, 1))
        self.assertEqual(label, original)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)
        del label['evidence']['schedule_revision']
        with self.assertRaisesRegex(ValueError, 'retained kickoff conflict'):
            forward.review(self.root, [label], self.root)
        self.assertEqual(forward.review(self.root, [], self.root)['market']['pending'], 1)

    def test_already_strict_grade_needs_no_revision_files(self):
        self.ft['header']['competitions'][0]['date'] = self.fixture['kickoff_at']
        label = self.label()
        label['evidence']['schedule_revision'] = None
        self.assertEqual(self.grade(label), (3, 2))

    def test_later_matchday_is_scored_in_its_original_registered_slot(self):
        self.ledger()
        self.ft['header']['competitions'][0]['date'] = '2026-11-25T01:15Z'
        self.ft['keyEvents'][0]['wallclock'] = '2026-11-25T01:25:33Z'
        self.result['observed_at'] = '2026-11-25T04:00Z'
        with patch.object(forward, 'compare', wraps=forward.compare) as paired:
            forward.review(self.root, [self.label()], self.root)
        row, = paired.call_args.args[0]
        self.assertEqual((row['id'], row['date'], row['actual']), (self.mid, '2026-11-24', (3, 2)))

    def test_match_brought_forward_can_finish_before_old_schedule_plus_90(self):
        self.ft['header']['competitions'][0]['date'] = stamp('00:15')
        self.ft['keyEvents'][0]['wallclock'] = stamp('00:16:30')
        self.result['observed_at'] = stamp('02:00')
        self.assertEqual(self.grade(self.label()), (3, 2))

    def test_rehashed_sources_must_match_event_season_stage_sides_and_both_states(self):
        changes = [
            ('board', ('leagues', 0, 'slug'), 'usa.2'), ('board', ('events', 0, 'id'), '102'),
            ('board', ('events', 0, 'competitions', 0, 'id'), '102'),
            ('board', ('events', 0, 'status', 'type', 'state'), 'in'),
            ('board', ('events', 0, 'competitions', 0, 'status', 'type', 'completed'), True),
            ('board', ('events', 0, 'date'), stamp('01:15')),
            ('board', ('events', 0, 'competitions', 0, 'competitors', 0, 'homeAway'), 'away'),
            ('ft', ('header', 'season', 'year'), 2025), ('ft', ('header', 'season', 'type'), 1),
            ('ft', ('header', 'season', 'type'), True), ('ft', ('header', 'id'), '102'),
            ('ft', ('header', 'league', 'slug'), 'usa.2'),
            ('ft', ('header', 'competitions', 0, 'competitors', 1, 'team', 'id'), '11'),
            ('ft', ('header', 'competitions', 0, 'status', 'type', 'name'), 'STATUS_FINAL_AET'),
            ('ft', ('keyEvents',), []), ('ft', ('keyEvents', 0, 'clock', 'value'), False),
            ('ft', ('keyEvents', 0, 'wallclock'), '2026-11-24T01:25:33'),
        ]
        for part, keys, value in changes:
            with self.subTest(part=part, keys=keys):
                doc = deepcopy(getattr(self, part))
                obj = doc
                for key in keys[:-1]:
                    obj = obj[key]
                obj[keys[-1]] = value
                with self.assertRaises(ValueError):
                    self.grade(self.label(**{part: doc}))

    def test_query_hash_trace_clock_and_summary_hash_required(self):
        for changes in (dict(status=404), dict(competition='usa.2'), dict(query_date='20261125'),
                        dict(sha256='0' * 64), dict(bytes=3), dict(query_observed_at=stamp('00:11')),
                        dict(url=self.query['url'].replace('scoreboard', 'summary'))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.grade(self.label(query_change=changes))
        for ref in ('queries', 'scoreboard', 'summary'):
            with self.subTest(ref=ref):
                label = self.label()
                evidence = label['evidence']
                target = evidence if ref == 'summary' else evidence['schedule_revision'][ref]
                target['sha256'] = '0' * 64
                with self.assertRaises(ValueError):
                    self.grade(label)
        label = self.label()
        rows = json.loads((self.root / 'queries.json').read_bytes())
        label['evidence']['schedule_revision']['queries'] = self.write('queries.json', rows * 2)
        with self.assertRaisesRegex(ValueError, 'one retained query'):
            self.grade(label)

    def test_first_issue_precedes_three_clocks_and_full_time_follows_actual_90(self):
        for field in ('original', 'revised', 'actual', 'capture', 'at_90', 'outcome', 'fixture', 'reissued'):
            with self.subTest(field=field):
                record = deepcopy(self.record)
                ft = deepcopy(self.ft)
                if field == 'original':
                    record['recorded_at'] = self.fixture['kickoff_at']
                if field == 'revised':
                    ft['header']['competitions'][0]['date'] = record['recorded_at']
                if field == 'actual':
                    ft['keyEvents'][0]['wallclock'] = record['recorded_at']
                if field == 'reissued':
                    record['revision'] = 2
                record['record_sha256'] = digest({k: v for k, v in record.items() if k != 'record_sha256'})
                label = self.label(ft=ft)
                if field == 'capture':
                    label['observed_at'] = label['evidence']['query_time'] = stamp('02:55:32')
                if field == 'at_90':
                    label['observed_at'] = label['evidence']['query_time'] = stamp('02:55:33')
                if field == 'outcome':
                    label['home_goals'] = 4
                if field == 'fixture':
                    label['kickoff_at'] = ft['header']['competitions'][0]['date']
                with self.assertRaises(ValueError):
                    self.grade(label, record)
