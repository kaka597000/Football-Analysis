"""Offline synthetic-contract tests; these fixtures are never production freezes."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

if __package__:
    from . import infer_market_snapshot as adapter
else:
    import infer_market_snapshot as adapter


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 12, 15, 46, tzinfo=timezone.utc)


class InferSnapshotTests(unittest.TestCase):
    now = FixedClock.now()

    def inputs(self):
        events = {'data': [{'id': 'evt_SYNTHETIC', 'sport': 'football',
                           'league': {'id': 'lg_BEJAVP29', 'name': 'UEFANLA'},
                           'home_team': {'id': 'tm_HOME', 'name': 'Home'},
                           'away_team': {'id': 'tm_AWAY', 'name': 'Away'},
                           'scheduled_at': '2026-11-12T17:00:00Z', 'status': 'scheduled'}],
                  'page': {'next_cursor': None}}
        odds = {'event_id': 'evt_SYNTHETIC', 'as_of': '2026-11-12T15:45:00Z', 'stale': False,
                'odds': [{'bookmaker': 'crown', 'market_type': 'totals', 'period': 'full_time',
                          'line': 2.5, 'handicap_components': None,
                          'prices': {'over': 1.9, 'under': 2.01}, 'format': 'decimal',
                          'status': 'open', 'as_of': '2026-11-12T15:40:00Z'}]}
        board = {'leagues': [{'slug': 'uefa.nations'}], 'events': [{
            'id': 'SYNTHETIC', 'date': '2026-11-12T17:00Z', 'season': {'slug': 'league-phase'},
            'status': {'type': {'state': 'pre', 'completed': False}},
            'competitions': [{'group': {'name': 'Group A1'}, 'competitors': [
                {'homeAway': 'home', 'team': {'id': '10', 'displayName': 'Home'}},
                {'homeAway': 'away', 'team': {'id': '20', 'displayName': 'Away'}}]}]}]}
        return events, odds, board

    def metadata(self, root, name, data, url):
        path = root / name
        path.write_text(json.dumps(data))
        return {'file': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'url': url,
                'requested_at': '2026-11-12T15:44:59Z', 'captured_at': '2026-11-12T15:45:00Z'}

    def fixtures(self):
        return [{'match_id': 'espn:SYNTHETIC', 'competition': 'uefa.nations',
                 'cohort': 'uefa.nations:A:league-phase', 'stage': 'league-phase',
                 'home': 'espn:10', 'away': 'espn:20', 'kickoff_at': '2026-11-12T17:00Z'}]

    def run_case(self, mutate=lambda e, o, b: None, *, modify_meta=None, fixtures=None, now=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            events, odds, board = self.inputs()
            mutate(events, odds, board)
            metas = [self.metadata(root, 'events.json', events, adapter.BASE + '/v1/events?limit=50'),
                     self.metadata(root, 'odds.json', odds, adapter.BASE + '/v1/events/evt_SYNTHETIC/odds'),
                     self.metadata(root, 'identity.json', board, adapter.ESPN)]
            if modify_meta:
                modify_meta(root, metas)
            now = now or self.now
            return adapter.build(root, *metas, cutoff=now.isoformat(), now=now, fixtures=fixtures)

    def test_complete_quote_preserves_row_time_and_full_identity(self):
        result = self.run_case(fixtures=self.fixtures())
        self.assertTrue(result['market_model_ready'])
        self.assertEqual(result['verified_quote_count'], 1)
        snap = result['snapshots'][0]
        self.assertEqual(snap['target']['cohort'], 'uefa.nations:A:league-phase')
        self.assertEqual(snap['target']['stage'], 'league-phase')
        self.assertEqual(snap['target']['competition'], 'uefa.nations')
        self.assertEqual(snap['policy']['market_max_age_seconds'], 3600)
        row = snap['observations'][0]
        self.assertEqual(row['value']['quoted_at'], '2026-11-12T15:40:00Z')
        self.assertNotEqual(row['observed_at'], row['value']['quoted_at'])
        self.assertIn('provider_event_evidence', row)
        self.assertFalse(result['quote_time_inferred_from_capture'])

    def test_missing_quote_time_never_replaced_with_global_or_capture(self):
        self.assertFalse(self.run_case(lambda e, o, b: o['odds'][0].pop('as_of'))['market_model_ready'])

    def test_old_quote_rejected_using_source_age(self):
        result = self.run_case(lambda e, o, b: o['odds'][0].update(as_of='2026-11-12T14:45:59Z'))
        self.assertEqual(result['verified_quote_count'], 0)
        self.assertEqual(result['snapshots'][0]['excluded']['stale_current_evidence'], 1)

    def test_stale_or_unknown_response_rejected(self):
        for value in (True, None, 'false', 0):
            with self.subTest(stale=value):
                self.assertFalse(self.run_case(lambda e, o, b: o.update(stale=value))['market_model_ready'])

    def test_after_capture_future_naive_and_postkickoff_quote_rejected(self):
        for timestamp in ('2026-11-12T15:45:01Z', '2026-11-12T17:00:00Z', '2026-11-12T15:40:00', None):
            with self.subTest(timestamp=timestamp):
                self.assertFalse(self.run_case(lambda e, o, b: o['odds'][0].update(as_of=timestamp))['market_model_ready'])

    def test_already_started_or_completed_target_rejected(self):
        for mutate in (lambda e, o, b: b['events'][0]['status']['type'].update(state='post', completed=True),
                       lambda e, o, b: b['events'][0].update(date='2026-11-12T15:00Z'),
                       lambda e, o, b: b['events'][0]['competitions'][0].update(status={'type': {'state': 'post', 'completed': True}})):
            self.assertFalse(self.run_case(mutate)['market_model_ready'])

    def test_wrong_team_kickoff_provider_league_and_grade_rejected(self):
        for change in (
            lambda e, o, b: e['data'][0]['home_team'].update(name='Away'),
            lambda e, o, b: e['data'][0].update(scheduled_at='2026-11-12T18:00Z'),
            lambda e, o, b: e['data'][0]['league'].update(id='lg_UNKNOWN'),
            lambda e, o, b: e['data'][0]['league'].update(name='UEFA Nations League'),
            lambda e, o, b: b['events'][0]['competitions'][0]['group'].update(name='Group C1'),
            lambda e, o, b: b['events'][0]['season'].update(slug='final'),
            lambda e, o, b: b['events'][0]['competitions'][0].pop('group'),
            lambda e, o, b: b['events'][0]['competitions'][0].update(id='DIFFERENT'),
            lambda e, o, b: b['events'][0]['competitions'][0].update(date='2026-11-12T18:00Z'),
        ):
            self.assertFalse(self.run_case(change)['market_model_ready'])

    def test_requested_fixture_cannot_override_independent_identity(self):
        for field, value in [('cohort', 'uefa.nations:C:league-phase'), ('home', 'espn:999'),
                             ('stage', 'final'), ('kickoff_at', '2026-11-12T18:00Z')]:
            fixtures = self.fixtures(); fixtures[0][field] = value
            self.assertFalse(self.run_case(fixtures=fixtures)['market_model_ready'])

    def test_grade_c_verified_by_both_sources(self):
        def change(e, o, b):
            e['data'][0]['league'] = {'id': 'lg_FDN4KCTA', 'name': 'UEFANLC'}
            b['events'][0]['competitions'][0]['group']['name'] = 'Group C2'
        result = self.run_case(change)
        self.assertEqual(result['snapshots'][0]['target']['cohort'], 'uefa.nations:C:league-phase')

    def test_reviewed_alias_requires_exact_national_team_id(self):
        def change(e, o, b):
            e['data'][0]['home_team']['name'] = 'Turkey'
            b['events'][0]['competitions'][0]['competitors'][0]['team'] = {'id': '465', 'displayName': 'Türkiye'}
        self.assertTrue(self.run_case(change)['market_model_ready'])
        def incorrect(e, o, b):
            change(e, o, b)
            b['events'][0]['competitions'][0]['competitors'][0]['team']['id'] = '999'
        self.assertFalse(self.run_case(incorrect)['market_model_ready'])

    def test_named_single_book_only_and_full_time_open_decimal(self):
        for field, values in [('bookmaker', ['Consensus', 'best', None, 'unknown']),
                              ('period', ['half_time', None]), ('status', ['closed', None]),
                              ('format', ['hongkong', None]), ('market_type', ['asian_handicap'])]:
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertFalse(self.run_case(lambda e, o, b: o['odds'][0].update({field: value}))['market_model_ready'])

    def test_incomplete_or_mixed_line_prices_rejected(self):
        for patch_quote in ({'prices': {'over': 1.9}}, {'prices': {'over': {'line': 2.5, 'price': 1.9}, 'under': 2.0}},
                            {'under_line': 3}, {'line': 2.7}, {'line': -1}, {'line': True},
                            {'handicap_components': [2, 3]}, {'prices': {'over': 1.0, 'under': 2.0}}):
            self.assertFalse(self.run_case(lambda e, o, b: o['odds'][0].update(patch_quote))['market_model_ready'])

    def test_valid_quarter_line_components_retained(self):
        result = self.run_case(lambda e, o, b: o['odds'][0].update(line=2.25, handicap_components=[2, 2.5]))
        self.assertTrue(result['market_model_ready'])

    def test_duplicate_quotes_deduplicated_and_conflicts_removed(self):
        def duplicate(e, o, b):
            o['odds'].append(deepcopy(o['odds'][0]))
        self.assertEqual(self.run_case(duplicate)['verified_quote_count'], 1)
        def conflict(e, o, b):
            duplicate(e, o, b); o['odds'][1]['prices']['over'] = 1.8
        self.assertFalse(self.run_case(conflict)['market_model_ready'])

    def test_ambiguous_event_identity_not_selected(self):
        self.assertFalse(self.run_case(lambda e, o, b: e['data'].append(deepcopy(e['data'][0])))['market_model_ready'])
        self.assertFalse(self.run_case(lambda e, o, b: b['events'].append(deepcopy(b['events'][0])))['market_model_ready'])

    def test_raw_hash_path_url_and_capture_are_verified(self):
        changes = [lambda root, m: (root / m[0]['file']).write_text('{}'),
                   lambda root, m: m[1].update(file='../odds.json'),
                   lambda root, m: m[2].update(url='https://evil.example/identity'),
                   lambda root, m: m[0].update(captured_at='2026-11-12T15:47:00Z'),
                   lambda root, m: m[0].update(url=adapter.BASE + '/v1/events?apiKey=secret')]
        for change in changes:
            with self.assertRaises(ValueError):
                self.run_case(modify_meta=change)

    def test_odds_endpoint_identity_bound_to_body(self):
        with self.assertRaises(ValueError):
            self.run_case(lambda e, o, b: o.update(event_id='evt_OTHER'))

    def test_missing_credential_makes_no_requests(self):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {}, clear=True), patch.object(adapter, 'fetch') as fetch:
            result = adapter.collect(Path(d) / 'run', self.fixtures())
            self.assertEqual(result['status'], 'MISSING_CREDENTIAL')
            fetch.assert_not_called()

    def test_auth_error_stops_service_and_does_not_save_secret_text(self):
        for code in (401, 403, 429):
            with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'infer_yapan_zhishu': 'synthetic-private-key'}, clear=True), patch.object(adapter, 'datetime', FixedClock):
                out = Path(d) / 'run'
                events, odds, board = self.inputs()
                second = deepcopy(events['data'][0]); second['id'] = 'evt_SECOND'
                second['home_team']['name'] = 'Second home'; events['data'].append(second)
                second_board = deepcopy(board['events'][0]); second_board['id'] = 'SECOND'
                second_board['competitions'][0]['competitors'][0]['team'] = {'id': '30', 'displayName': 'Second home'}
                board['events'].append(second_board)
                fixtures = self.fixtures(); fixtures.append(dict(fixtures[0], match_id='espn:SECOND', home='espn:30'))
                calls = []
                def fake_fetch(url, path, **kwargs):
                    calls.append(url)
                    if '/odds?' in url:
                        raise HTTPError(url + '&key=synthetic-private-key', code, 'Bearer synthetic-private-key', {}, None)
                    return self.metadata(path.parent, path.name, board if url.startswith(adapter.ESPN) else events, url)
                with patch.object(adapter, 'fetch', side_effect=fake_fetch):
                    result = adapter.collect(out, fixtures)
                self.assertEqual(len(calls), 3)
                self.assertEqual(result['status'], 'COLLECTION_FAILED')
                self.assertEqual(result['failures'][0]['http_status'], code)
                self.assertNotIn('synthetic-private-key', ''.join(p.read_text() for p in out.iterdir()))

    def test_fetch_fixed_host_bearer_no_redirect_and_safe_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = b'{"data": []}'
            with patch.object(adapter, 'build_opener', return_value=opener) as create:
                meta = adapter.fetch(adapter.BASE + '/v1/events?limit=1', Path(d) / 'events.json', credential='synthetic-private-key')
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-private-key')
            self.assertIsInstance(create.call_args.args[0], adapter.NoRedirect)
            self.assertIsNone(adapter.NoRedirect().redirect_request(request, None, 302, '', {}, 'https://evil.example'))
            self.assertNotIn('synthetic-private-key', json.dumps(meta))
            with self.assertRaises(ValueError):
                adapter.fetch('https://evil.example/v1/events', Path(d) / 'evil.json', credential='synthetic-private-key')

    def test_echoed_credential_response_never_saved(self):
        with tempfile.TemporaryDirectory() as d:
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = b'{"detail": "synthetic-private-key"}'
            path = Path(d) / 'events.json'
            with patch.object(adapter, 'build_opener', return_value=opener), self.assertRaises(ValueError):
                adapter.fetch(adapter.BASE + '/v1/events', path, credential='synthetic-private-key')
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
