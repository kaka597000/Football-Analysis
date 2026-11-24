"""Offline checks for the registered Brazil collection gate."""

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from data_sources import team_market_quotes as quotes
from research.analysis_freeze import digest
from research_program import team_market_candidate as study


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(study.encode(value))


def signed(record):
    body = {k: v for k, v in record.items() if k != 'record_sha256'}
    return dict(body, record_sha256=digest(body))


class BrazilTeamMarketPreflight(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.batch = self.root / study.DEFAULT_BATCH
        self.out = self.root / 'offline-quotes'
        self.at = datetime(2026, 11, 18, 20, tzinfo=timezone.utc)
        self.recorded = (self.at - timedelta(hours=1)).isoformat()
        self.enterContext(patch.object(quotes, 'now', return_value=self.at.isoformat()))
        self.enterContext(patch.dict(os.environ, {'THE_ODDS': 'offline-test-only'}))

        fixtures, events = [], []
        for i, minutes in enumerate((60, 0, 120), 1):
            mid = f'espn:{i}'
            kickoff = (self.at + timedelta(minutes=minutes)).isoformat()
            fixtures.append(dict(match_id=mid, competition='bra.1', cohort='bra.1', stage='regular',
                                 home=f'espn:{i + 10}', away=f'espn:{i + 20}', kickoff_at=kickoff,
                                 cutoff_at=self.recorded, identity_verified=True, score_basis='regulation_90'))
            events.append(dict(id=f'event-{i}', sport_key='soccer_brazil_campeonato',
                               home_team=f'Home {i}', away_team=f'Away {i}', commence_time=kickoff))
        raw = self.root / 'market/runs/anchor/odds.json'
        # The anchor also has an unregistered future event.
        write(raw, events + [dict(events[0], id='unregistered')])
        raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        markets = {f['match_id']: dict(match_id=f['match_id'], provider='the-odds-api',
                                      provider_match_id=e['id'], source_sha256=raw_sha, bookmaker='test')
                   for f, e in zip(fixtures, events, strict=True)}
        plan = signed({'recorded_at': self.recorded, 'payload': dict(fixtures=fixtures, markets=markets,
                                      binding={'market_archive': 'market/runs/anchor'},
                                      sources={'odds': {'sha256': raw_sha}})})
        plan_sha = plan['record_sha256']
        write(self.root / quotes.DEFAULT_PLAN, plan)
        protocol = {'version': study.VERSION, 'parent_plan': plan_sha}
        self.protocol_sha = digest(protocol)
        write(self.batch / 'protocol.json', protocol)
        write(self.batch.parent / 'registration.json',
              dict(registered_at=self.recorded, forward_plan_parent=plan_sha, forward_ids=list(markets),
                   forward_arms={'team_markets': list(study.ARMS)}))
        self.original = {}
        for i, f in enumerate(fixtures, 1):
            mid = f['match_id']
            base = study.picks([[.2, .2], [.2, .4]], 1.5)
            record = signed(dict(match_id=mid, recorded_at=self.recorded, evaluation_slot='FIRST_PREMATCH',
                                 fixture=f, payload=dict(plan_sha256=plan_sha, market=markets[mid],
                                                        arms={'market_baseline': base})))
            self.original[mid] = record
            write(self.root / study.PARENT / 'freezes' / (str(i) + '.json'), record)
        self.fetch = Mock(side_effect=self.fake_fetch)

    def first(self, mid, recorded_at=None):
        original = self.original[mid]
        stamp = (recorded_at or self.at - timedelta(minutes=1)).isoformat()
        base = original['payload']['arms']['market_baseline']
        payload = dict(version=study.VERSION, protocol_sha256=self.protocol_sha,
                       base_freeze_sha256=original['record_sha256'], base_market=original['payload']['market'],
                       arms={k: base for k in study.ARMS})
        record = signed(dict(match_id=mid, track=study.TRACK, stage='prematch', evaluation_slot='FIRST_PREMATCH',
                             revision=1, previous_sha256=None, recorded_at=stamp,
                             fixture=dict(original['fixture'], cutoff_at=stamp), payload=payload))
        path = self.batch / 'records' / mid.split(':')[1] / '000001.json'
        write(path, record)
        return path, record

    def fake_fetch(self, fixture, regions, key, out, markets):
        mid, event_id = fixture['match_id'], fixture['provider_match_id']
        stamp = self.at.isoformat()
        event = dict(id=event_id, sport_key=fixture['sport_key'], commence_time=fixture['kickoff_at'],
                     home_team=fixture['provider_home'], away_team=fixture['provider_away'],
                     bookmakers=[dict(key='test', markets=[dict(key='btts', last_update=stamp,
                                                              outcomes=[dict(name='Yes', price=1.8),
                                                                        dict(name='No', price=2.0)])])])
        rel = f'raw/{event_id}.json'
        raw = study.encode(event)
        (out / rel).write_bytes(raw)
        return (dict(match_id=mid, provider_match_id=event_id, status='OK', http_status=200,
                     quota={'x-requests-last': 1}, source_file=rel, source_sha256=hashlib.sha256(raw).hexdigest(),
                     source_url='https://example.test/odds', requested_at=stamp, captured_at=stamp), event)

    def collect(self, **kwargs):
        return quotes.collect(self.root, self.out, fetcher=self.fetch, brazil_first_issue_preflight=True, **kwargs)

    def test_all_first_issues_require_zero_requests_and_keep_all_targets(self):
        for mid in self.original:
            self.first(mid)
        result = study.preflight(self.root, self.batch, as_of=self.at)
        self.assertEqual(result['status_counts'], {'PREVIOUSLY_ISSUED': 3})
        summary = self.collect()
        self.fetch.assert_not_called()
        self.assertEqual((summary['registered'], summary['requests'], summary['credits_reported']), (3, 0, 0))
        self.assertEqual(quotes.read(self.out / 'quotes.json'), [])
        self.assertEqual({r['match_id'] for r in quotes.read(self.out / 'targets.json')}, set(self.original))
        self.assertEqual(self.collect(), summary)
        self.fetch.assert_not_called()

    def test_mixed_batch_only_fetches_the_registered_unissued_future_target(self):
        _, first = self.first('espn:1')
        summary = self.collect()
        self.assertEqual([call.args[0]['match_id'] for call in self.fetch.call_args_list], ['espn:3'])
        self.assertEqual({r['match_id']: r['status'] for r in summary['fixtures']},
                         {'espn:1': 'PREVIOUSLY_ISSUED', 'espn:2': 'KICKED_OFF', 'espn:3': 'COLLECTED'})
        self.assertEqual(summary['fixtures'][0]['first_sha256'], first['record_sha256'])
        # Replaying a run checks what was known at its cutoff.
        self.first('espn:3', recorded_at=self.at + timedelta(minutes=1))
        self.assertEqual(quotes.verify_run(self.root, self.out), summary)
        self.assertEqual(self.collect(), summary)
        self.assertEqual(self.fetch.call_count, 1)
        summary['fixtures'][0]['first_sha256'] = '0' * 64
        write(self.out / 'coverage.json', summary)
        with self.assertRaisesRegex(ValueError, 'retained Brazil first-issue preflight differs'):
            quotes.verify_run(self.root, self.out)

    def test_conflicting_identity_or_bad_checksum_fails_before_collecting(self):
        path, first = self.first('espn:1')
        bad = (
            dict(first, record_sha256='0' * 64),
            signed(dict(first, fixture=dict(first['fixture'], home='espn:other'))),
            signed(dict(first, fixture=dict(first['fixture'], kickoff_at=self.at.isoformat()))),
            signed(dict(first, payload=dict(first['payload'], protocol_sha256='0' * 64))),
            signed(dict(first, payload=dict(first['payload'], base_market={'provider_match_id': 'wrong'}))),
            signed(dict(first, track='another-track')),
        )
        for i, record in enumerate(bad):
            with self.subTest(case=i):
                write(path, record)
                with self.assertRaises(ValueError):
                    self.collect()
        write(path, first)
        write(path.parent.parent / 'duplicate' / '000001.json', first)
        with self.assertRaisesRegex(ValueError, 'first issue conflict'):
            self.collect()
        self.fetch.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_preflight_cannot_filter_discovery_or_match_market_runs(self):
        for params in ({'discover': 'eng.1'}, {'match_markets': True}, {'plan': 'other.json'}):
            with self.subTest(params=params), self.assertRaisesRegex(ValueError, 'Brazil first-issue preflight'):
                self.collect(**params)
        self.fetch.assert_not_called()

    def test_collector_without_opt_in_never_consults_first_issues(self):
        self.first('espn:1')
        with patch.object(study, 'preflight', side_effect=AssertionError('default collector called preflight')):
            summary = quotes.collect(self.root, self.out, fetcher=self.fetch)
        self.assertEqual([call.args[0]['match_id'] for call in self.fetch.call_args_list], ['espn:1', 'espn:3'])
        self.assertEqual(summary['collection_settings'], {'days': None, 'max_events': None})

    def test_preflight_rejects_future_registration_or_parent_without_requesting(self):
        later = (self.at + timedelta(minutes=5)).isoformat()
        for path, field in ((self.batch.parent / 'registration.json', 'registered_at'),
                            (self.root / study.PARENT / 'freezes' / '1.json', 'recorded_at')):
            with self.subTest(field=field):
                previous = study.read(path)
                value = dict(previous, **{field: later})
                write(path, signed(value) if 'record_sha256' in value else value)
                try:
                    with self.assertRaisesRegex(ValueError, 'later than preflight'):
                        self.collect()
                finally:
                    write(path, previous)
        self.fetch.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_archived_default_run_cannot_satisfy_explicit_new_gate(self):
        old = quotes.collect(self.root, self.out, fetcher=self.fetch)
        del old['collection_settings']
        write(self.out / 'coverage.json', old)
        self.fetch.reset_mock()
        self.assertEqual(quotes.collect(self.root, self.out, fetcher=self.fetch), old)
        with self.assertRaisesRegex(ValueError, 'different collection settings'):
            self.collect()
        self.fetch.assert_not_called()
