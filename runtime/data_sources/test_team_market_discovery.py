"""Regression checks for provider/ESPN cross-matching in rolling team-odds collection."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_sources import team_market_discovery as d
from data_sources import team_market_quotes as quotes


def provider(eid='a' * 32, kickoff='2026-11-21T12:30:00Z'):
    return dict(id=eid, sport_key='soccer_epl', commence_time=kickoff, home_team='Manchester City', away_team='Fulham')


def scoreboard():
    home = dict(id='382', displayName='Manchester City FC', shortDisplayName='Manchester City')
    away = dict(id='370', displayName='Fulham', shortDisplayName='Fulham')
    event = dict(id='401879233', date='2026-11-21T12:30Z', season=dict(year=2026, slug='2026-27-english-premier-league'),
                 status=dict(type=dict(state='pre', completed=False)),
                 competitions=[dict(competitors=[dict(homeAway='home', team=home), dict(homeAway='away', team=away)])])
    return dict(leagues=[dict(slug='eng.1')], events=[event])


class DiscoveryTests(unittest.TestCase):
    def discover(self, catalog, board):
        with tempfile.TemporaryDirectory() as temp:
            inputs = [(catalog, {'status': 'OK'}), (board, {'status': 'OK'})]
            with patch.object(d, '_fetch', side_effect=inputs) as fetch:
                targets, report = d.discover_targets('eng.1', 5, Path(temp), 'test-credential', '2026-11-18T00:00Z')
                return targets, report, fetch.call_count

    def test_rolling_next_matchday_and_independent_identity(self):
        events = [provider('c' * 32, '2026-11-23T14:30:00Z'),
                  provider('b' * 32, '2026-11-22T14:30:00Z'), provider()]
        targets, report, requests = self.discover(events, scoreboard())
        self.assertEqual(requests, 2)
        self.assertEqual(report['catalog_events'], 3)
        self.assertEqual(report['eligible_in_horizon'], 2)
        self.assertEqual(report['checked_on_nearest_day'], 1)
        self.assertEqual([x['match_id'] for x in targets], ['espn:401879233'])
        self.assertEqual(targets[0]['provider_home'], 'Manchester City')
        self.assertEqual(targets[0]['score_basis'], 'regulation_90')

    def test_reject_wrong_league_teams_time_and_ambiguous_source(self):
        e = provider()
        wrong_league = scoreboard()
        wrong_league['leagues'][0]['slug'] = 'eng.2'
        self.assertEqual(self.discover([e], wrong_league)[1]['status'], 'SCOREBOARD_LEAGUE_MISMATCH')
        wrong_stage = scoreboard()
        wrong_stage['events'][0]['season']['slug'] = 'friendly'
        self.assertEqual(self.discover([e], wrong_stage)[0], [])
        for source in (dict(e, home_team='Fulham', away_team='Manchester City'), dict(e, commence_time='2026-11-21T13:00:00Z')):
            self.assertEqual(self.discover([source], scoreboard())[0], [])
        twin = dict(e, id='b' * 32)
        targets, report, _ = self.discover([e, twin], scoreboard())
        self.assertEqual(targets, [])
        self.assertEqual(len(report['exclusions']), 2)
        self.assertEqual(set(report['exclusions'].values()), {'AMBIGUOUS_PROVIDER_EVENT'})

    def test_offline_replay_checks_archive_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def capture(url, filename, directory, key=None):
                value = [provider()] if key else scoreboard()
                raw = json.dumps(value).encode()
                (directory / filename).write_bytes(raw)
                return value, dict(source_file=filename, source_url=url, source_sha256=hashlib.sha256(raw).hexdigest(),
                                   status='OK', http_status=200, requested_at='2026-11-18T00:00Z',
                                   captured_at='2026-11-18T00:00Z')

            with patch.object(d, '_fetch', side_effect=capture):
                targets, receipt = d.discover_targets('eng.1', 5, root, 'test-credential', '2026-11-18T00:00Z')
            (root / 'targets.json').write_text(json.dumps(targets))
            with patch.object(d, '_fetch', side_effect=AssertionError('network forbidden')):
                self.assertEqual(d.replay_discovery(root, receipt), (targets, receipt))
                altered = copy.deepcopy(receipt)
                altered['catalog_events'] += 1
                with self.assertRaisesRegex(ValueError, 'summary'):
                    d.replay_discovery(root, altered)
                substituted = copy.deepcopy(receipt)
                substituted['requests'][1]['source_file'] = '../other-event.json'
                with self.assertRaisesRegex(ValueError, 'identity'):
                    d.replay_discovery(root, substituted)
                (root / 'targets.json').write_text('[]')
                with self.assertRaisesRegex(ValueError, 'targets'):
                    d.replay_discovery(root, receipt)
                (root / 'discovery-events.json').write_text('[]')
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    d.replay_discovery(root, receipt)

    def test_collect_verify_and_retry_without_confirmed_fixtures(self):
        wrong_league = scoreboard()
        wrong_league['leagues'][0]['slug'] = 'eng.2'
        scenarios = [
            ('NO_UPCOMING_EVENTS', 'test-key', [(200, [])]),
            ('MISSING_CREDENTIAL', '', []),
            ('NO_VERIFIED_EVENTS', 'test-key', [(200, [dict(provider(), away_team='Unknown')]), (200, scoreboard())]),
            ('EVENT_CATALOG_UNAVAILABLE', 'test-key', [(429, None)]),
            ('SCOREBOARD_UNAVAILABLE', 'test-key', [(200, [provider()]), (503, None)]),
            ('SCOREBOARD_LEAGUE_MISMATCH', 'test-key', [(200, [provider()]), (200, wrong_league)]),
        ]
        stamp = '2026-11-18T00:00:00+00:00'
        for status, key, responses in scenarios:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                out = root / 'run'
                remaining = iter(responses)

                def capture(url, filename, directory, key=None):
                    code, value = next(remaining)
                    meta = dict(source_url=url, http_status=code, requested_at=stamp, captured_at=stamp, quota={})
                    if code != 200:
                        return None, dict(meta, status='HTTP_ERROR')
                    raw = json.dumps(value).encode()
                    (directory / filename).write_bytes(raw)
                    return value, dict(meta, source_file=filename, source_sha256=hashlib.sha256(raw).hexdigest(), status='OK')

                def no_quote(*args):
                    self.fail('unexpected paid event request')

                env = {'THE_ODDS': key, 'WCPP_ODDS_API_KEY': '', 'WCPP_THE_ODDS_API_KEY': ''}
                with (patch.dict(os.environ, env), patch.object(quotes, 'now', return_value=stamp),
                      patch.object(d, '_fetch', side_effect=capture) as fetch):
                    first = quotes.collect(root, out, discover='eng.1', fetcher=no_quote)
                    self.assertEqual(fetch.call_count, len(responses))
                self.assertEqual((first['registered'], first['requests'], first['accepted_pairs']), (0, 0, 0))
                receipt = quotes.read(out / 'discovery-receipt.json')
                self.assertEqual(receipt['status'], status)
                self.assertEqual(quotes.read(out / 'targets.json'), [])
                self.assertEqual(quotes.read(out / 'quotes.json'), [])
                original = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
                with patch.object(d, '_fetch', side_effect=AssertionError('offline operation made a request')):
                    self.assertEqual(quotes.verify_run(root, out), first)
                    self.assertEqual(quotes.collect(root, out, discover='eng.1', fetcher=no_quote), first)
                self.assertEqual(original, {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()})

    def test_no_paid_calls_or_dates_when_schedule_missing(self):
        targets, report, requests = self.discover([], scoreboard())
        self.assertEqual((targets, report['status'], requests), ([], 'NO_UPCOMING_EVENTS', 1))
        with patch.object(d, '_fetch') as fetch:
            _, report = d.discover_targets('eng.1', 2, Path('.'), '', '2026-11-18T00:00Z')
            self.assertEqual(report['status'], 'MISSING_CREDENTIAL')
            self.assertFalse(fetch.called)


if __name__ == '__main__':
    unittest.main()
