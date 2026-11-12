"""Source-backed market attachment and missing-quote regressions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from runtime.automation.market_bridge import attach_total_quote
from runtime.v2_test.data_contract import digest, snapshot


class MarketBridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.cutoff = self.now - timedelta(seconds=2)
        self.fixture = {'match_id': 'espn:1', 'cohort': 'eng.1', 'stage': 'regular',
                        'home': 'espn:10', 'away': 'espn:20',
                        'kickoff_at': (self.now + timedelta(hours=3)).isoformat(),
                        'cutoff_at': self.now.isoformat()}

    def channel(self, root, *, provider='provider-a', bookmaker='book-a', line=2.5,
                quote_age=10, target_changes=None, extra_rows=()):
        root.mkdir(parents=True)
        identity = root/'identity.json'; identity.write_text('{"source":"synthetic identity"}')
        odds = root/'odds.json'; odds.write_text('{"source":"synthetic full market"}')
        evidence = {'file': odds.name, 'sha256': hashlib.sha256(odds.read_bytes()).hexdigest(),
                    'url': 'https://example.org/odds', 'query_time': self.cutoff.isoformat(),
                    'identity_verified': True}
        row = {'provider': provider, 'cohort': 'eng.1', 'match_id': 'espn:1',
               'entity_id': 'espn:10', 'field': 'market_quote', 'period': 'regulation_90',
               'observed_at': self.cutoff.isoformat(), 'event_kickoff_at': self.fixture['kickoff_at'],
               'evidence': evidence,
               'identity_evidence': {'file': identity.name,
                   'sha256': hashlib.sha256(identity.read_bytes()).hexdigest(),
                   'url': 'https://example.org/identity', 'captured_at': self.cutoff.isoformat()},
               'value': {'bookmaker': bookmaker, 'market': 'totals', 'line': line,
                         'quoted_at': (self.now-timedelta(seconds=quote_age)).isoformat(),
                         'odds': {'over': 1.9, 'under': 2.0}}}
        rows = [row]
        for fields in extra_rows:
            other = deepcopy(row); other['value'].update(fields); rows.append(other)
        target = dict(self.fixture, home_id=self.fixture['home'], away_id=self.fixture['away'],
                      cutoff_at=self.cutoff.isoformat())
        snap = snapshot(rows, target, root, provider=provider, now=self.cutoff,
                        market_max_age_seconds=3600)
        if target_changes:
            snap['target'].update(target_changes)
            snap['snapshot_sha256'] = digest({k:v for k,v in snap.items() if k!='snapshot_sha256'})
        data = {'schema': 'WCPP-MARKET-CHANNEL-1', 'snapshots': [snap]}
        path = root/'market-channel.json'; path.write_text(json.dumps(data))
        return path, data

    def test_valid_source_quote_is_attached_without_mutating_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, source = self.channel(Path(tmp)/'one')
            before = deepcopy(self.fixture)
            result, audit = attach_total_quote(self.fixture, tmp, now=self.now)
            self.assertEqual(audit['status'], 'ATTACHED')
            self.assertEqual(self.fixture, before)
            quote = result['total_quote']; row = source['snapshots'][0]['observations'][0]
            self.assertEqual(quote['quoted_at'], row['value']['quoted_at'])
            self.assertEqual(quote['query_time'], row['observed_at'])
            self.assertEqual(quote['source_sha256'], row['evidence']['sha256'])
            self.assertEqual(quote['line'], 2.5)
            self.assertFalse(audit['first_freeze_backfilled'])

    def test_identity_stage_cohort_sides_and_kickoff_must_match(self):
        for change in [{'stage': 'playoffs'}, {'cohort': 'esp.1'}, {'home_id': 'espn:20'},
                       {'away_id': 'espn:30'}, {'match_id': 'espn:99'},
                       {'kickoff_at': (self.now+timedelta(hours=4)).isoformat()},
                       {'stage': None}]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                self.channel(Path(tmp)/'one', target_changes=change)
                result, audit = attach_total_quote(self.fixture, tmp, now=self.now)
                self.assertNotIn('total_quote', result)
                self.assertEqual(audit['excluded']['FIXTURE_IDENTITY_OR_STAGE_MISMATCH'], 1)

    def test_tampered_snapshot_or_raw_identity_or_odds_is_not_accepted(self):
        for name in ('market-channel.json', 'identity.json', 'odds.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)/'one'; path, data = self.channel(root)
                if name == 'market-channel.json':
                    data['snapshots'][0]['observations'][0]['value']['line'] = 3.5
                    path.write_text(json.dumps(data))
                else:
                    (root/name).write_text('{"changed":true}')
                result, audit = attach_total_quote(self.fixture, tmp, now=self.now)
                self.assertNotIn('total_quote', result)
                self.assertGreater(audit['excluded']['INVALID_SNAPSHOT_OR_SOURCE_EVIDENCE'], 0)

    def test_stale_or_after_cutoff_does_not_supply_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.channel(Path(tmp)/'one', quote_age=3599)
            future = dict(self.fixture, cutoff_at=(self.now+timedelta(seconds=5)).isoformat())
            result, audit = attach_total_quote(future, tmp, now=self.now+timedelta(seconds=5))
            self.assertNotIn('total_quote', result)
            self.assertEqual(audit['excluded']['stale_current_evidence'], 1)
        with tempfile.TemporaryDirectory() as tmp:
            self.channel(Path(tmp)/'one')
            earlier = dict(self.fixture, cutoff_at=(self.cutoff-timedelta(seconds=1)).isoformat())
            result, audit = attach_total_quote(earlier, tmp, now=self.now)
            self.assertNotIn('total_quote', result)

    def test_infer_requires_retained_provider_event_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.channel(Path(tmp)/'one', provider='infersports')
            result, audit = attach_total_quote(self.fixture, tmp, now=self.now)
            self.assertNotIn('total_quote', result)
            self.assertEqual(audit['excluded']['INVALID_SNAPSHOT_OR_SOURCE_EVIDENCE'], 1)

    def test_provider_events_source_hash_time_and_path_are_rechecked(self):
        for provider in ('infersports', 'provider-a'):
            for problem in ('valid', 'tampered', 'late', 'outside'):
                with self.subTest(provider=provider, problem=problem), tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp)/'one';path, data=self.channel(root, provider=provider)
                    events=root/'events.json';events.write_text('{"event_id":"one","teams":[10,20]}')
                    metadata={'file':events.name,'sha256':hashlib.sha256(events.read_bytes()).hexdigest(),
                              'url':'https://example.org/events','captured_at':self.cutoff.isoformat()}
                    if problem == 'late':
                        metadata['captured_at']=(self.now+timedelta(seconds=1)).isoformat()
                    elif problem == 'outside':
                        outside=Path(tmp)/'outside.json';outside.write_bytes(events.read_bytes())
                        metadata['file']='../outside.json'
                    saved=data['snapshots'][0]
                    saved['observations'][0]['provider_event_evidence']=metadata
                    saved['snapshot_sha256']=digest({k:v for k,v in saved.items() if k!='snapshot_sha256'})
                    path.write_text(json.dumps(data))
                    if problem == 'tampered':
                        events.write_text('{"event_id":"one","teams":[999,888]}')
                    result,audit=attach_total_quote(self.fixture,tmp,now=self.now)
                    if problem == 'valid':
                        self.assertEqual(result['total_quote']['provider'],provider)
                    else:
                        self.assertNotIn('total_quote',result)
                        self.assertEqual(audit['excluded']['INVALID_SNAPSHOT_OR_SOURCE_EVIDENCE'],1)

    def test_fallback_preserves_valid_provider_after_broken_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp)/'bad'; self.channel(bad, provider='preferred')
            (bad/'odds.json').write_text('tampered')
            self.channel(Path(tmp)/'good', provider='fallback')
            result, audit = attach_total_quote(self.fixture, tmp, provider_order=['preferred','fallback'], now=self.now)
            self.assertEqual(result['total_quote']['provider'], 'fallback')
            self.assertEqual(audit['eligible_provider_books'], 1)

    def test_ambiguous_latest_line_or_price_does_not_choose_using_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.channel(Path(tmp)/'ambiguous', extra_rows=[{'line':3.0}])
            result, audit = attach_total_quote(self.fixture, tmp, now=self.now)
            self.assertNotIn('total_quote', result)
            self.assertEqual(audit['excluded']['AMBIGUOUS_LATEST_BOOK_LINE_OR_PRICE'], 1)

    def test_explicit_provider_and_book_priority_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.channel(Path(tmp)/'a', provider='a', bookmaker='a')
            self.channel(Path(tmp)/'b', provider='b', bookmaker='a', line=3.0)
            self.channel(Path(tmp)/'c', provider='b', bookmaker='b', line=3.5)
            result, _ = attach_total_quote(self.fixture, tmp, provider_order=['b','a'],
                                            bookmaker_order=['b','a'], now=self.now)
            self.assertEqual(result['total_quote']['provider'], 'b')
            self.assertEqual(result['total_quote']['bookmaker'], 'b')
            self.assertEqual(result['total_quote']['line'], 3.5)

    def test_no_source_keeps_fixture_and_explicit_missing_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, audit = attach_total_quote(self.fixture, tmp, now=self.now)
            self.assertNotIn('total_quote', result)
            self.assertEqual(result['match_id'], self.fixture['match_id'])
            self.assertEqual(audit['status'], 'NO_ELIGIBLE_RETAINED_TOTAL_QUOTE')


if __name__ == '__main__':
    unittest.main()
