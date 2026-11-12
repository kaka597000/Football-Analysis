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
    from .market_snapshot import ALIAS_REVIEW, build, collect
else:
    from market_snapshot import ALIAS_REVIEW, build, collect


class MarketSnapshotTests(unittest.TestCase):
    now = datetime(2026, 11, 12, 12, 0, 30, tzinfo=timezone.utc)

    def inputs(self, root):
        board = {'leagues': [{'slug': 'uefa.nations'}], 'events': [{
            'id': '1', 'date': '2026-11-12T17:00Z', 'season': {'slug': 'league-phase'},
            'status': {'type': {'state': 'pre', 'completed': False}},
            'competitions': [{'group': {'name': 'Group A1'}, 'competitors': [
                {'homeAway': 'home', 'team': {'id': '10', 'displayName': 'Home'}},
                {'homeAway': 'away', 'team': {'id': '20', 'displayName': 'Away'}}]}]}]}
        odds = [{'id': 'o1', 'sport_key': 'soccer_uefa_nations_league',
                 'home_team': 'Home', 'away_team': 'Away', 'commence_time': '2026-11-12T17:00:00Z',
                 'bookmakers': [{'key': 'test', 'markets': [{'key': 'totals',
                 'last_update': '2026-11-12T12:00:00Z', 'outcomes': [
                     {'name': 'Over', 'price': 1.9, 'point': 2.5},
                     {'name': 'Under', 'price': 2.0, 'point': 2.5}]}]}]}]
        return odds, board

    def run_case(self, mutate=lambda o, b: None, *, now=None, captured_at='2026-11-12T12:00:20Z',
                 competition='uefa.nations'):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); odds, board = self.inputs(root); mutate(odds, board)
            metas = []
            for name, data in [('odds', odds), ('identity', board)]:
                p = root / (name + '.json'); p.write_text(json.dumps(data))
                metas.append({'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                              'url': 'https://example.org/' + name, 'captured_at': captured_at})
            now = now or self.now
            return build(root, *metas, cutoff=now.isoformat(), now=now, competition=competition)

    def test_real_group_identity_is_carried_to_prediction_bridge(self):
        def mutate(odds, board):
            board['events'][0]['competitions'][0]['group'] = {'name': 'GROUP C2'}
        result = self.run_case(mutate)
        target = result['snapshots'][0]['target']
        self.assertEqual(target['cohort'], 'uefa.nations:C:league-phase')
        self.assertEqual(target['competition'], 'uefa.nations')
        self.assertEqual(target['stage'], 'league-phase')
        self.assertEqual(result['snapshots'][0]['observations'][0]['cohort'], target['cohort'])

    def test_domestic_scope_requires_matching_provider_and_regular_stage(self):
        def mutate(odds, board):
            board['leagues'][0]['slug'] = 'eng.1'
            board['events'][0]['season']['slug'] = 'regular-season'
            odds[0]['sport_key'] = 'soccer_epl'
        result = self.run_case(mutate, competition='eng.1')
        self.assertEqual(result['covered_fixture_count'], 1)
        self.assertEqual(result['snapshots'][0]['target']['stage'], 'regular')
        def wrong_stage(odds, board):
            mutate(odds, board)
            board['events'][0]['season']['slug'] = 'playoffs'
        self.assertFalse(self.run_case(wrong_stage, competition='eng.1')['market_model_ready'])

    def test_unverified_nations_grade_cannot_be_ready(self):
        result = self.run_case(lambda o, b: b['events'][0]['competitions'][0].pop('group'))
        self.assertFalse(result['market_model_ready'])
        self.assertEqual(result['excluded']['missing_verified_nations_grade'], 1)

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

    def alias_case(self, rule, *, side='home', mutate=lambda o, b: None, before_review=False):
        def setup(odds, board):
            team = board['events'][0]['competitions'][0]['competitors'][0 if side == 'home' else 1]['team']
            team.update(id=rule['espn_id'], displayName=rule['espn_name'])
            odds[0][side + '_team'] = rule['provider_name']
            if not before_review:
                odds[0]['bookmakers'][0]['markets'][0]['last_update'] = '2026-11-12T14:00:00Z'
            mutate(odds, board)
        return self.run_case(setup, now=None if before_review else self.now.replace(hour=14),
                             captured_at='2026-11-12T12:00:20Z' if before_review else '2026-11-12T14:00:20Z')

    def test_reviewed_team_ids_work_on_either_side_and_keep_raw_identity(self):
        for rule in ALIAS_REVIEW['teams']:
            for side in ('home', 'away'):
                with self.subTest(rule=rule, side=side):
                    r = self.alias_case(rule, side=side)
                    self.assertEqual(r['verified_quote_count'], 1)
                    row = r['snapshots'][0]['observations'][0]
                    evidence = row['identity_alias_review']
                    self.assertEqual(evidence['teams'][side], rule)
                    self.assertEqual(evidence['provider_source_names'][side], rule['provider_name'])
                    self.assertEqual(evidence['identity_source_names'][side], rule['espn_name'])
                    self.assertEqual(evidence['review_sha256'], r['identity_alias_review']['sha256'])
                    self.assertEqual(row['value']['quoted_at'], '2026-11-12T14:00:00Z')
                    self.assertEqual(row['observed_at'], '2026-11-12T14:00:20Z')
                    self.assertEqual(row['evidence']['sha256'], r['identity_mappings'][0]['odds_evidence_sha256'])

    def test_alias_cannot_be_backdated_before_identity_review(self):
        for rule in ALIAS_REVIEW['teams']:
            self.assertFalse(self.alias_case(rule, before_review=True)['market_model_ready'])

    def test_alias_rejects_wrong_id_name_opponent_side_time_competition_and_phase(self):
        rule = ALIAS_REVIEW['teams'][0]
        mutations = [
            lambda o, b: b['events'][0]['competitions'][0]['competitors'][0]['team'].update(id='999'),
            lambda o, b: b['events'][0]['competitions'][0]['competitors'][0]['team'].update(displayName='Türkiye U21'),
            lambda o, b: o[0].update(home_team='turkey'),
            lambda o, b: o[0].update(away_team='Different'),
            lambda o, b: o[0].update(home_team='Away', away_team='Turkey'),
            lambda o, b: o[0].update(commence_time='2026-11-12T18:00:00Z'),
            lambda o, b: o[0].update(sport_key='soccer_other'),
            lambda o, b: b['events'][0]['season'].update(slug='knockout'),
        ]
        for mutate in mutations:
            self.assertFalse(self.alias_case(rule, mutate=mutate)['market_model_ready'])

    def test_exact_and_alias_candidates_are_ambiguous_not_first_wins(self):
        def mutate(odds, board):
            duplicate = deepcopy(odds[0]); duplicate.update(id='o2', home_team='Türkiye')
            odds.append(duplicate)
        r = self.alias_case(ALIAS_REVIEW['teams'][0], mutate=mutate)
        self.assertFalse(r['market_model_ready'])
        self.assertEqual(r['excluded']['missing_or_ambiguous_identity'], 1)

    def test_exact_matches_have_no_alias_metadata_added_to_observations(self):
        row = self.run_case()['snapshots'][0]['observations'][0]
        self.assertNotIn('identity_alias_review', row)


if __name__ == '__main__':
    unittest.main()
