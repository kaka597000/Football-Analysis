"""Offline evidence/identity/freshness regressions for the market adapter."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if __package__:
    from .market_snapshot import build, collect
else:
    from market_snapshot import build, collect


class MarketSnapshotTests(unittest.TestCase):
    now = datetime(2026, 11, 12, 12, 0, 30, tzinfo=timezone.utc)

    def inputs(self, root):
        board = {'leagues': [{'slug': 'uefa.nations'}], 'events': [{
            'id': '1', 'date': '2026-11-12T17:00Z', 'season': {'slug': 'league-phase'},
            'status': {'type': {'state': 'pre', 'completed': False}},
            'competitions': [{'competitors': [
                {'homeAway': 'home', 'team': {'id': '10', 'displayName': 'Home'}},
                {'homeAway': 'away', 'team': {'id': '20', 'displayName': 'Away'}}]}]}]}
        odds = [{'id': 'o1', 'sport_key': 'soccer_uefa_nations_league',
                 'home_team': 'Home', 'away_team': 'Away', 'commence_time': '2026-11-12T17:00:00Z',
                 'bookmakers': [{'key': 'test', 'markets': [{'key': 'totals',
                 'last_update': '2026-11-12T12:00:00Z', 'outcomes': [
                     {'name': 'Over', 'price': 1.9, 'point': 2.5},
                     {'name': 'Under', 'price': 2.0, 'point': 2.5}]}]}]}]
        return odds, board

    def run_case(self, mutate=lambda o, b: None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); odds, board = self.inputs(root); mutate(odds, board)
            metas = []
            for name, data in [('odds', odds), ('identity', board)]:
                p = root / (name + '.json'); p.write_text(json.dumps(data))
                metas.append({'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                              'url': 'https://example.org/' + name, 'captured_at': '2026-11-12T12:00:20Z'})
            return build(root, *metas, cutoff=self.now.isoformat(), now=self.now)

    def test_complete_same_line_keeps_real_quote_time(self):
        r = self.run_case(); self.assertTrue(r['market_model_ready'])
        self.assertEqual(r['covered_fixture_count'], 1)
        row = r['snapshots'][0]['observations'][0]
        self.assertEqual(row['value']['quoted_at'], '2026-11-12T12:00:00Z')
        self.assertEqual(row['match_id'], 'espn:1')

    def test_missing_time_not_replaced_with_bookmaker_or_capture(self):
        r = self.run_case(lambda o, b: o[0]['bookmakers'][0]['markets'][0].pop('last_update'))
        self.assertFalse(r['market_model_ready'])

    def test_old_quote_remains_missing(self):
        r = self.run_case(lambda o, b: o[0]['bookmakers'][0]['markets'][0].update(last_update='2026-11-12T11:59:00Z'))
        self.assertEqual(r['verified_quote_count'], 0)
        self.assertEqual(r['snapshots'][0]['excluded']['stale_current_evidence'], 1)

    def test_incomplete_mixed_line_and_future_quotes_rejected(self):
        for mutate in [lambda m: m['outcomes'].pop(),
                       lambda m: m['outcomes'][1].update(point=3.0),
                       lambda m: m.update(last_update='2026-11-12T12:00:25Z')]:
            r = self.run_case(lambda o, b: mutate(o[0]['bookmakers'][0]['markets'][0]))
            self.assertFalse(r['market_model_ready'])

    def test_wrong_identity_and_ambiguous_event_rejected(self):
        for mutate in [lambda o, b: o[0].update(home_team='Different'),
                       lambda o, b: o[0].update(commence_time='2026-11-12T18:00Z'),
                       lambda o, b: o.append(deepcopy(o[0]))]:
            self.assertFalse(self.run_case(mutate)['market_model_ready'])

    def test_missing_secret_reports_no_request(self):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {}, clear=True), patch(collect.__module__ + '.urlopen') as fetch:
            r = collect(Path(d) / 'run')
            self.assertEqual(r['status'], 'MISSING_CREDENTIAL'); fetch.assert_not_called()

    def test_missing_price_or_bookmaker_preserves_other_books(self):
        for missing in ('price', 'bookmaker'):
            def mutate(odds, board):
                books = odds[0]['bookmakers']; bad = deepcopy(books[0])
                if missing == 'price':
                    bad['key'] = 'broken'; bad['markets'][0]['outcomes'][0].pop('price')
                else:
                    bad.pop('key')
                books.insert(0, bad)
            r = self.run_case(mutate)
            self.assertEqual(r['verified_quote_count'], 1)
            self.assertEqual(r['excluded']['invalid_quote_or_timestamp'], 1)

    def test_exact_duplicates_count_once(self):
        r = self.run_case(lambda o,b: o[0]['bookmakers'].append(deepcopy(o[0]['bookmakers'][0])))
        self.assertEqual(r['verified_quote_count'], 1)
        self.assertEqual(r['excluded']['duplicate_quote'], 1)

    def test_conflicting_book_rejected_without_discarding_other_books(self):
        def mutate(o,b):
            books = o[0]['bookmakers']; conflict = deepcopy(books[0]); good = deepcopy(books[0])
            conflict['markets'][0]['outcomes'][0]['price'] = 2.1
            good['key'] = 'unaffected'; books.extend([conflict, good])
        r = self.run_case(mutate)
        self.assertEqual(r['verified_quote_count'], 1)
        self.assertEqual(r['excluded']['conflicting_market_group'], 1)
        self.assertEqual(r['snapshots'][0]['observations'][0]['value']['bookmaker'], 'unaffected')


if __name__ == '__main__':
    unittest.main()
