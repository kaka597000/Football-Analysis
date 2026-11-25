import unittest
from datetime import date, timedelta
from math import log

import numpy as np

from research_program.territory_history import LEAGUES, METRICS, TerritoryHistory


def source(mid, day, home=('1', 'Alpha'), away=('2', 'Bravo'), competition='EPL', **fields):
    row = dict(competition=competition, source_match_id=mid, date=day, home_id=home[0],
               home=home[1], away_id=away[0], away=away[1], source_sha256='a' * 64)
    for side in ('home', 'away'):
        row.update({side + '_xg': 1.35, side + '_deep': 6,
                    side + '_ppda_att': 220, side + '_ppda_def': 20})
    return dict(row, **fields)


def game(day, home='Alpha', away='Bravo', competition='eng.1', **fields):
    return dict(date=day, home=home, away=away, competition=competition, **fields)


class TerritoryHistoryTest(unittest.TestCase):
    def test_provider_and_canonical_leagues_share_the_literal_source_day(self):
        for slug, code in LEAGUES.items():
            with self.subTest(slug=slug):
                first = source('m', '2025-03-01 23:58:00', competition=slug, home_xg=3)
                normalized = dict(first, competition=code)
                other = source('m', first['date'], ('9', 'Alpha'), competition='eng.2', home_xg=10)
                histories = [TerritoryHistory(rows + [other], code)
                             for rows in ([first], [normalized], [first, normalized])]
                now = game('2025-03-01T23:59:59', competition=code)
                after = game('2025-03-02', competition=code)
                expected, evidence = histories[0].context(after)
                for history in histories:
                    self.assertFalse(history.context(now)[1]['available'])
                    x, meta = history.context(after)
                    np.testing.assert_array_equal(x, expected)
                    self.assertEqual(meta, evidence)
                    self.assertEqual(meta['home_id'], '1')
                    self.assertEqual(meta['home_history_dates'], ['2025-03-01'])
                    self.assertEqual(meta['home_exposure'], dict.fromkeys(METRICS, 1))
                    self.assertTrue(meta['available'])
                    self.assertFalse(history.context(after, cutoff=date(2025, 3, 1))[1]['available'])
                    self.assertFalse(history.context(after, cutoff='2025-03-02T00:01:00+08:00')[1]['available'])
                    self.assertEqual(history.context(now, cutoff='2026-01-01')[1]['cutoff'], '2025-03-01')

    def test_venue_expectations_are_frozen_for_the_whole_source_date(self):
        background = source('a', '2025-01-01 01:00:00', ('8', 'Eight'), ('9', 'Nine'),
                            home_xg=None, away_xg=4, home_deep=20, away_deep=None,
                            home_ppda_att=500, home_ppda_def=None, away_ppda_att=300, away_ppda_def=10)
        first = source('b', '2025-01-01 23:00:00', home_xg=3, away_xg=1, home_deep=2, away_deep=8,
                       home_ppda_att=110, home_ppda_def=22, away_ppda_att=330, away_ppda_def=15)
        second = source('c', '2025-01-02', ('3', 'Charlie'), ('1', 'Alpha'), home_xg=2, away_xg=None,
                        home_deep=float('nan'), away_deep=0, away_ppda_att=0, away_ppda_def=0)
        history = TerritoryHistory([second, first, background], 'eng.1')
        x, meta = history.context(game('2025-01-02'))
        only_first = TerritoryHistory([first], 'eng.1').context(game('2025-01-02'))[0]
        np.testing.assert_array_equal(x, only_first)
        self.assertEqual(meta['home_status'], 'full')

        # Alpha played at home, then away. Each missing field masks only its own expectation.
        x, meta = history.context(game('2025-01-03', away='Unknown'))
        self.assertEqual(meta['home_exposure'], dict(xg_for=1, xg_against=2, deep_for=2, deep_against=1, ppda=2))
        self.assertEqual(meta['home_status'], 'partial')
        self.assertEqual(meta['away_exposure'], dict.fromkeys(METRICS, 0))
        att_norm = (40 * 220 + 300 + 330) / 42
        def_norm = (40 * 20 + 10 + 15) / 42
        press = log(((880 + 220 + att_norm) / (80 + 20 + def_norm)) / ((880 + 110) / (80 + 22)))
        np.testing.assert_allclose(x, [
            [log(8.4 / 6.75), 0, log(26 / (24 + 6 + (240 + 8) / 41)), 0, press, 0],
            [0, log(8.4 / (5.4 + 1.35 + (54 + 3) / 41)), 0, log(32 / 30), 0, press]])
        swapped, _ = history.context(game('2025-01-03', home='Unknown', away='Alpha'))
        np.testing.assert_array_equal(swapped, x[::-1])

    def test_last_eight_fixtures_include_missing_games_and_150_day_boundary(self):
        target = date(2025, 11, 1)
        at = lambda age: (target - timedelta(days=age)).isoformat()
        rows = [source('old', at(151), home_xg=20), source('edge', at(150), home_deep=1000)]
        query = game(target)
        x, meta = TerritoryHistory(rows, 'eng.1').context(query)
        self.assertEqual(meta['home_history_dates'], [at(150)])
        self.assertEqual(x[0, 2], 1.)
        self.assertEqual(TerritoryHistory(rows, 'eng.1').context(query, cutoff=at(150))[1]['home_status'], 'stale')
        for age in range(8, 0, -1):
            row = source(str(age), at(age))
            for side in ('home', 'away'):
                row.update({side + '_xg': None, side + '_deep': -1, side + '_ppda_def': None})
            rows.append(row)
        x, meta = TerritoryHistory(rows, 'eng.1').context(query)
        self.assertEqual(meta['home_history_dates'], [at(i) for i in range(8, 0, -1)])
        self.assertEqual(meta['home_status'], 'missing_metrics')
        self.assertEqual(meta['home_exposure'], dict.fromkeys(METRICS, 0))
        self.assertFalse(meta['available'])
        np.testing.assert_array_equal(x, np.zeros((2, 6)))
        neutral, meta = TerritoryHistory([source('neutral', at(1))], 'eng.1').context(query)
        np.testing.assert_array_equal(neutral, x)
        self.assertTrue(meta['available'])

    def test_raw_spelling_is_dated_and_alias_conflicts_do_not_overwrite_an_id(self):
        old = source('old', '2025-01-01', ('7', 'Old Club'))
        new = source('rename', '2025-01-07', ('7', 'Néw Club'))
        collision = source('other', '2025-01-11', ('8', 'New Club'))
        history = TerritoryHistory([collision, old, new], 'eng.1')
        day_of_rename = game('2025-01-07', 'New-Club')
        self.assertIsNone(history.context(day_of_rename)[1]['home_id'])
        self.assertIsNone(history.context(game('2025-02-01', 'New-Club'), cutoff='2025-01-07')[1]['home_id'])
        _, meta = history.context(game('2025-01-11', 'New-Club'))
        self.assertEqual((meta['home_method'], meta['home_id'], meta['home_source_name'], meta['home_source_name_date']),
                         ('exact', '7', 'Néw Club', '2025-01-07'))
        self.assertEqual(history.context(game('2025-01-12', 'New Club'))[1]['home_status'], 'ambiguous')

        first = source('a', '2025-01-01', ('5', 'Manchester United'))
        conflicting = source('b', '2025-01-05', ('6', 'Man Utd'))
        history = TerritoryHistory([conflicting, first], 'eng.1')
        _, meta = history.context(game('2025-01-05', 'Man United'))
        self.assertEqual((meta['home_id'], meta['home_method']), ('5', 'alias'))
        self.assertIsNone(history.context(game('2025-01-06', 'Man United'))[1]['home_id'])
        _, meta = history.context(game('2025-01-04', 'Man United', 'Manchester United'))
        self.assertEqual((meta['home_status'], meta['away_id']), ('ambiguous', '5'))

    def test_calendar_can_resolve_a_documented_alias_conflict_without_any_metric(self):
        dates = [f'2025-01-0{i}' for i in range(1, 5)]
        known = [source(str(i), day, ('5', 'Manchester United')) for i, day in enumerate(dates)]
        other = source('other', '2025-01-02', ('6', 'Man Utd'), ('7', 'Other'))
        for row in known + [other]:
            for side in ('home', 'away'):
                for metric in ('xg', 'deep', 'ppda_att', 'ppda_def'):
                    del row[side + '_' + metric]
        history = TerritoryHistory(known + [other], 'eng.1')
        history.walk(game(day, 'Man United', 'R5 Opponent') for day in dates)
        x, meta = history.context(game('2025-01-05', 'Man United', 'Unknown'))
        self.assertEqual((meta['home_id'], meta['home_method'], meta['home_status']),
                         ('5', 'calendar', 'missing_metrics'))
        self.assertEqual(meta['home_signature'], dict(games=4, matches=4, runner_up=1))
        self.assertFalse(meta['available'])
        np.testing.assert_array_equal(x, np.zeros((2, 6)))
        # A different archived team key that normalizes alike cannot borrow these four dates.
        other_spelling = history.context(game('2025-01-05', 'Man-United', 'Unknown'))[1]
        self.assertIsNone(other_spelling['home_id'])
        self.assertEqual(other_spelling['home_signature']['games'], 0)

    def test_forward_calendar_cannot_retroactively_identify_a_same_day_target(self):
        rows, games = [], []
        for n in range(1, 5):
            day = f'2025-01-0{n}'
            clubs = [('1', 'Alpha'), (str(n + 10), f'Opponent {n}')]
            names = ['R5 Only', f'R5 Opponent {n}']
            if n % 2 == 0:
                clubs.reverse()
                names.reverse()
            rows.append(source(str(n), day, *clubs, home_xg=3))
            games.append(game(day, *names))
        evening = dict(games[-1], date='2025-01-04 23:00:00')
        future = game('2025-01-05', 'R5 Only', 'Unknown', home_id='irrelevant-provider-id')
        inputs = [future, evening, *games[::-1]]
        history = TerritoryHistory(rows, 'eng.1')
        x, meta = history.walk(inputs)
        self.assertEqual((x.shape, x.dtype), ((6, 2, 6), np.dtype(float)))
        self.assertEqual((meta[0]['home_id'], meta[0]['home_method']), ('1', 'calendar'))
        self.assertEqual(meta[0]['home_signature'], dict(games=4, matches=4, runner_up=0))
        self.assertEqual(meta[1], meta[2])
        self.assertIsNone(meta[1]['away_id'])
        self.assertEqual(meta[1]['away_signature']['games'], 3)
        for i, target in enumerate(inputs):
            again, evidence = history.context(target)
            np.testing.assert_array_equal(again, x[i])
            self.assertEqual(evidence, meta[i])
        _, cutoff_meta = history.context(future, cutoff='2025-01-04')
        self.assertIsNone(cutoff_meta['home_id'])
        self.assertEqual(cutoff_meta['home_signature']['matches'], 3)
        # A different league cannot supply the fourth R5 slot, even with the same names.
        split = TerritoryHistory(rows, 'eng.1')
        split.walk([*games[:3], dict(games[3], competition='esp.1')])
        self.assertIsNone(split.context(future)[1]['home_id'])
        # Arbitrary score/price labels and external target IDs cannot alter the link.
        changed = [dict(g, home_goals=99 - i, away_goals=i, actual=[42, 17], home_id='2') for i, g in enumerate(inputs)]
        relabeled = TerritoryHistory([dict(r, home_goals=61, away_goals=15) for r in rows], 'eng.1')
        x2, meta2 = relabeled.walk(changed)
        np.testing.assert_array_equal(x2, x)
        self.assertEqual(meta2, meta)
        np.testing.assert_array_equal(history.walk(inputs)[0], x)
        self.assertEqual(history.walk([])[0].shape, (0, 2, 6))

    def test_calendar_requires_four_hits_75_percent_and_two_ahead_in_the_latest_eight(self):
        target = date(2025, 12, 1)

        def match(ages, hits, rival):
            rows, games = [], []
            for i, age in enumerate(ages):
                day = target - timedelta(days=age)
                games.append(game(day, 'R5 Club', 'Unknown'))
                if i in hits:
                    rows.append(source(f'h{i}', day))
                if i in rival:
                    rows.append(source(f'r{i}', day, ('3', 'Rival'), ('4', 'Other')))
            history = TerritoryHistory(rows, 'eng.1')
            history.walk(games)
            return history.context(game(target, 'R5 Club', 'Unknown'))[1]

        ages = list(range(10, 0, -1))
        correct = match(ages, range(2, 8), range(2, 6))
        self.assertEqual(correct['home_id'], '1')
        self.assertEqual(correct['home_signature'], dict(games=8, matches=6, runner_up=4))
        self.assertEqual(match(ages, range(2, 8), range(2, 7))['home_status'], 'ambiguous')
        self.assertIsNone(match(ages, range(2, 7), ())['home_id'])
        self.assertIsNone(match([4, 3, 2, 1], range(3), ())['home_id'])
        boundary = match([400, 399, 398, 397], range(4), ())
        self.assertEqual((boundary['home_id'], boundary['home_status']), ('1', 'stale'))
        self.assertIsNone(match([401, 399, 398, 397], range(4), ())['home_id'])

    def test_fixture_pair_audit_is_separate_from_features_and_uses_ordered_ids(self):
        prior = source('prior', '2025-01-01', home_xg=3)
        same = source('same', '2025-01-08 01:00:00', home_xg=20)
        rotated = source('flipped', same['date'], ('2', 'Bravo'), ('1', 'Alpha'))
        target = game('2025-01-08', home_goals=71, away_goals=0)
        baseline = TerritoryHistory([prior], 'eng.1')
        x, meta = baseline.context(target)
        for row, status in [(same, 'matched'), (rotated, 'disagreement')]:
            history = TerritoryHistory([prior, row], 'eng.1')
            x2, meta2 = history.context(target)
            np.testing.assert_array_equal(x2, x)
            self.assertEqual(meta2, meta)
            audit = history.audit_fixture(target, meta)
            self.assertEqual(audit['status'], status)
            self.assertEqual(audit['source_match_id'], 'same' if status == 'matched' else None)
        self.assertEqual(baseline.audit_fixture(target, meta)['status'], 'date_absent')
        foreign = game(target['date'], competition='esp.1')
        x, metadata = baseline.walk([foreign])
        self.assertEqual(metadata[0]['home_status'], 'competition_mismatch')
        np.testing.assert_array_equal(x, np.zeros((1, 2, 6)))
