import json
import unittest
from datetime import date, timedelta

import numpy as np

from research_program.lineup_history import LineupHistory


def day(n):
    return (date(2023, 1, 1) + timedelta(days=n)).isoformat()


def game(mid, n, home='A', away='C', score=(0, 0), **identity):
    return dict(id=f'espn:{mid}', date=day(n), competition='jpn.1', home=home, away=away,
                home_goals=score[0], away_goals=score[1], score_basis='regulation_90', **identity)


def anchor(h=1, a=2):
    p = np.zeros((h + 2, a + 2))
    p[h, a] = 1.
    return p


def xi(attack, defence):
    return ([dict(id=p, position='MF'[i % 2]) for i, p in enumerate(attack)]
            + [dict(id=p, position='G' if i == 0 else 'D') for i, p in enumerate(defence)])


def squad(prefix):
    return xi([prefix + 'A' + str(i) for i in range(6)], [prefix + 'D' + str(i) for i in range(5)])


def roster(mid, home_id='1', away_id='3', home=None, away=None):
    return dict(match_id=str(mid), home_id=home_id, away_id=away_id, home_starters=home, away_starters=away)


class LineupHistoryTests(unittest.TestCase):
    def test_six_columns_from_original_anchor_and_complete_club_schedules(self):
        common = [f'D{i}' for i in range(8)]
        records = {
            2: roster(2, home=xi(['P', 'R'], ['Q', *common])),
            3: roster(3, home=xi(['P', 'Q'], [*common, 'D8'])),
            5: roster(5, '4', '2', away=squad('B')),
        }
        history = LineupHistory(records)
        games = [game(1, 1, score=(0, 4)), game(2, 10, score=(8, 0)), game(3, 20, score=(3, 2)),
                 game(4, 2, 'D', 'B', (4, 3)), game(5, 11, 'D', 'B', (1, 0))]
        x, meta = history.walk(games, np.repeat(anchor()[None], len(games), axis=0))
        self.assertEqual(x.shape, (5, 2, 6))
        self.assertEqual([m['match_id'] for m in meta], [g['id'] for g in games])
        future = dict(home='A', away='B', date=day(30))
        x, meta = history.context(future)
        # A's residuals are (-1,2), (3,-2), (2,0). P and Q both started in
        # the last two games, but Q moved from defence to attack.
        np.testing.assert_allclose(x, [[4/7, 3/6, 7/30, -1/6, -.25, 0.],
                                       [-1/6, 0., -1/6, -1.6/9, 0., 8/9-.75]], atol=1e-15)
        self.assertTrue(meta['both_prior_rosters'])
        self.assertFalse(meta['all_neutral'])
        self.assertEqual(meta['home']['form_games'], 3)
        self.assertEqual(meta['home']['attack'], dict(players=2, rating_available=True, retention_available=True))
        self.assertEqual(meta['away']['defence'], dict(players=5, rating_available=True, retention_available=False))
        self.assertEqual(meta['away']['penultimate']['status'], 'missing_roster')
        np.testing.assert_allclose(history.context(dict(future, home='B', away='A'))[0], x[::-1])
        self.assertEqual(json.loads(json.dumps(meta, allow_nan=False)), meta)

    def test_form_uses_last_six_and_both_inclusive_window_boundaries(self):
        dates = [34, 35, 309, 310, 311, 320, 330, 340, 350, 399]
        goals = [9, 3, 2, 3, 0, 1, 1, 1, 1, 1]
        games = [game(i, d, score=(g, 0)) for i, (d, g) in enumerate(zip(dates, goals), 1)]
        rosters = {i: roster(i, home=squad('A')) for i in (1, 2, 10)}
        history = LineupHistory(rosters)
        history.walk(games, [anchor(0, 0)] * len(games))
        target = dict(date=day(400), home='A', away='Z')
        x, m = history.context(target)
        self.assertEqual((m['home']['form_games'], m['home']['club_games_365']), (6, 9))
        self.assertAlmostEqual(x[0, 0], .5)
        # Day 35 counts at age 365, day 34 does not. Missing XIs enter the club mean.
        average = sum(goals[1:]) / 9
        self.assertAlmostEqual(x[0, 2], (4 - 2 * average) / 10)
        x, m = history.context(target, cutoff=day(311))
        self.assertEqual((m['home']['form_games'], m['home']['club_games_365']), (1, 3))
        self.assertAlmostEqual(x[0, 0], 3/5)
        self.assertEqual(m['home']['previous']['date'], day(310))
        x, m = history.context(dict(target, date=day(401)), cutoff=day(311))
        self.assertEqual((m['home']['form_games'], m['home']['club_games_365']), (0, 2))
        self.assertIsNone(m['home']['previous'])
        np.testing.assert_array_equal(x, np.zeros((2, 6)))

    def test_residual_uses_both_full_marginal_means_and_coverage_is_per_group(self):
        first = np.array([[.1, 0., .2], [.3, .4, 0.]])
        h = LineupHistory({1: roster(1, home=xi([], [str(i) for i in range(11)])),
                           2: roster(2, home=squad('A'))})
        h.walk([game(1, 1, score=(2, 1)), game(2, 2)], [first, anchor()])
        home, m = h.context(dict(home='A', away='Z', date=day(2)))
        away, _ = h.context(dict(home='C', away='Z', date=day(2)))
        np.testing.assert_allclose([home[0, 0], home[1, 1], away[0, 0], away[1, 1]],
                                   [1.3/5, .2/5, .2/5, 1.3/5])
        self.assertTrue(m['home']['previous']['roster_available'])
        self.assertFalse(m['home']['attack']['rating_available'])
        self.assertTrue(m['home']['defence']['rating_available'])
        x, m = h.context(dict(home='A', away='Z', date=day(3)))
        self.assertTrue(m['home']['attack']['retention_available'])
        self.assertEqual(x[0, 4], -.75)

    def test_missing_side_or_bad_eleven_occupies_its_immediate_slot(self):
        base = squad('A')
        for invalid in (None, base[:-1], [*base[:-1], base[0]], [*base[:-1], dict(id='X', position='D/M')],
                        [*base[:-1], dict(id='X', position='D', starter=False)]):
            with self.subTest(invalid=None if invalid is None else invalid[-1]):
                rosters = {i: roster(i, home=invalid if i == 2 else base, away=squad('C')) for i in (1, 2, 3)}
                h = LineupHistory(rosters)
                games = [game(i, i, score=(i, 0)) for i in (1, 2, 3)]
                h.walk(games, [anchor()] * 3)
                x, meta = h.context(game(4, 3))
                self.assertEqual(meta['home']['previous']['match_ids'], ['espn:2'])
                self.assertFalse(meta['home']['previous']['roster_available'])
                self.assertFalse(meta['both_prior_rosters'])
                np.testing.assert_array_equal(x[[0, 1, 0, 1], [2, 3, 4, 5]], 0.)
                self.assertAlmostEqual(x[1, 4], .25)
                x, meta = h.context(game(4, 4))
                self.assertEqual(meta['home']['penultimate']['status'], 'missing_roster')
                self.assertTrue(meta['home']['attack']['rating_available'])
                self.assertFalse(meta['home']['attack']['retention_available'])
                self.assertEqual(x[0, 4], 0.)

    def test_tied_dates_do_not_supply_an_order_or_skip_an_appearance(self):
        games = [game(1, 1), game(2, 5, score=(3, 0)), game(3, 5, away='D', score=(0, 2)), game(4, 8)]
        rosters = {i: roster(i, '1', '4' if i == 3 else '3', home=squad('A')) for i in (1, 2, 4)}
        h = LineupHistory(rosters)
        x, meta = h.walk(games, [anchor()] * 4)
        np.testing.assert_array_equal(x[1, 0, [0, 2, 4]], x[2, 0, [0, 2, 4]])
        self.assertEqual(meta[1]['home'], meta[2]['home'])
        self.assertEqual(meta[3]['home']['previous']['status'], 'tied_date')
        self.assertEqual(meta[3]['home']['previous']['match_ids'], ['espn:2', 'espn:3'])
        self.assertFalse(meta[3]['home']['attack']['rating_available'])
        self.assertEqual(meta[3]['home']['form_games'], 3)
        future, detail = h.context(game(5, 9))
        self.assertEqual(detail['home']['penultimate']['status'], 'tied_date')
        self.assertTrue(detail['home']['attack']['rating_available'])
        self.assertFalse(detail['home']['attack']['retention_available'])
        self.assertEqual(future[0, 4], 0.)
        perm = [3, 2, 0, 1]
        permuted = LineupHistory(rosters)
        y, m = permuted.walk([games[i] for i in perm], [anchor()] * 4)
        np.testing.assert_array_equal(y, x[perm])
        self.assertEqual(m, [meta[i] for i in perm])

    def test_target_and_future_scores_anchors_lineups_and_names_cannot_leak(self):
        games = [game(1, 0, score=(2, 1)), game(2, 7, home='New spelling', score=(1, 2)),
                 game(3, 7, home='A', away='D'), game(4, 14, home='New spelling')]
        rosters = {i: roster(i, away_id=('4' if i == 3 else '3'), home=squad('A')) for i in (1, 2, 3, 4)}
        h = LineupHistory(rosters)
        x, meta = h.walk(games, [anchor()] * 4)
        self.assertEqual(meta[1]['home']['form_games'], 0)
        self.assertEqual(meta[2]['home']['form_games'], 1)
        changed = [games[0], dict(games[1], home_goals=9, away_goals=0), *games[2:]]
        altered = dict(rosters)
        altered[2] = roster(2, home_id='99', home=None)
        altered[4] = roster(4, home_id='99', home=squad('Z'))
        other = LineupHistory(altered)
        y, m = other.walk(changed, [anchor(), anchor(0, 4), anchor(), anchor(5, 0)])
        np.testing.assert_array_equal(x[:3], y[:3])
        self.assertEqual(meta[:3], m[:3])
        self.assertFalse(np.array_equal(x[3], y[3]))
        for i in (1, 2):
            historical, details = other.context(games[i])
            np.testing.assert_array_equal(historical, x[i])
            self.assertEqual(details, meta[i])
        query = dict(date=day(20), home='New spelling', away='C')
        np.testing.assert_array_equal(h.context(query, cutoff=day(7))[0], other.context(query, cutoff=day(7))[0])

    def test_date_block_is_emitted_before_roster_lookup(self):
        accesses = []
        at_prediction = {}

        class Rosters(dict):
            def __getitem__(self, key):
                accesses.append(key)
                return super().__getitem__(key)

        class TracedHistory(LineupHistory):
            def context(self, g, cutoff=None):
                at_prediction[g['id']] = list(accesses)
                return super().context(g, cutoff)

        rosters = Rosters({i: roster(i) for i in (1, 2, 3, 4)})
        history = TracedHistory(rosters)
        history.walk([game(1, 1), game(2, 2), game(3, 2, home='D')], [anchor()] * 3)
        self.assertEqual(at_prediction, {'espn:1': [], 'espn:2': [1], 'espn:3': [1]})
        history.context(game(4, 3))
        self.assertEqual(accesses, [1, 2, 3])

    def test_dated_unique_name_links_and_schedule_ids_keep_clubs_separate(self):
        games = [game(1, 0, home='Alpha FC', score=(3, 2)), game(2, 4, home='Alpha-FC', score=(2, 2)),
                 game(3, 8, home='Renamed', score=(1, 2)), game(4, 10, home='Renamed', score=(0, 2))]
        h = LineupHistory({2: roster(2, home=squad('A')), 3: roster(3, home=squad('A')),
                           4: roster(4, home_id='99', home=squad('Z'))})
        h.walk(games, [anchor()] * 4)
        t = dict(date=day(10), home='Renamed', away='Z')
        x, m = h.context(t)
        self.assertEqual((m['home']['club_key'], m['home']['form_games']), ('espn:1', 3))
        self.assertAlmostEqual(x[0, 0], 3/7)
        unknown, m = h.context(dict(t, date=day(11)))
        self.assertEqual(m['home']['identity_source'], 'ambiguous')
        np.testing.assert_array_equal(unknown, 0.)
        for club, n, form in [('1', 3, 3/7), ('99', 1, -1/5)]:
            explicit, m = h.context(dict(t, date=day(11), home_id=club))
            self.assertEqual(m['home']['identity_source'], 'schedule_id')
            self.assertEqual(m['home']['form_games'], n)
            self.assertAlmostEqual(explicit[0, 0], form)
        explicit, m = h.context(dict(t, date=day(2), home='ALPHA FC', home_id=1))
        self.assertEqual(m['home']['form_games'], 1)
        self.assertAlmostEqual(explicit[0, 0], 2/5)

    def test_unverified_colliding_spellings_and_verified_exact_aliases(self):
        games = [game(1, 1, home='A-B', score=(3, 2)), game(2, 2, home='AB', score=(0, 2))]
        h = LineupHistory({})
        h.walk(games, [anchor()] * 2)
        for name, expected in [('A-B', 2/5), ('AB', -1/5), ('A.B', 0.)]:
            x, _ = h.context(dict(date=day(3), home=name, away='Z'))
            self.assertAlmostEqual(x[0, 0], expected)
        verified = LineupHistory({1: roster(1, home_id='11'), 2: roster(2, home_id='22')})
        verified.walk(games, [anchor()] * 2)
        for name, expected in [('A-B', '11'), ('AB', '22'), ('A.B', None)]:
            _, m = verified.context(dict(date=day(3), home=name, away='Z'))
            self.assertEqual(m['home']['team_id'], expected)
        self.assertEqual(m['home']['identity_source'], 'ambiguous')

    def test_zoned_cutoff_uses_original_r5_utc_calendar_day(self):
        history = LineupHistory({})
        history.walk([game(1, 0, score=(3, 2)), game(2, 1, score=(2, 0))], [anchor()] * 2)
        future = dict(date=day(4), home='A', away='C')
        x, m = history.context(future, cutoff=day(2) + 'T00:15:00+09:00')
        self.assertEqual(m['cutoff'], day(1))
        np.testing.assert_array_equal(x, history.context(future, cutoff=day(1))[0])
        self.assertEqual(m['home']['previous']['match_ids'], ['espn:1'])
        x, m = history.context(dict(future, date=day(1) + 'T00:15:00+09:00'))
        self.assertEqual(m['date'], day(0))
        np.testing.assert_array_equal(x, 0.)

    def test_player_effects_are_team_local_and_clipped_after_shrinkage(self):
        picked = squad('P')
        records = {i: roster(i, home=picked if i > 20 else None) for i in range(1, 41)}
        games = [game(i, i, score=(6, 0) if i > 20 else (0, 6)) for i in range(1, 41)]
        h = LineupHistory(records)
        h.walk(games, [anchor(3, 3)] * len(games))
        x, _ = h.context(game(41, 41))
        self.assertEqual(x[0, 2], 1.)
        self.assertEqual(x[1, 3], -1.)
        # The same professional IDs at another club have only one start there.
        transferred = LineupHistory({**records, 41: roster(41, '77', '3', home=picked)})
        transferred.walk([*games, game(41, 41, home='New club', score=(6, 0))], [anchor(3, 3)] * 41)
        x, m = transferred.context(dict(date=day(42), home='New club', away='Z'))
        self.assertTrue(m['home']['attack']['rating_available'])
        self.assertEqual(m['home']['club_games_365'], 1)
        self.assertEqual(x[0, 2], 0.)
        self.assertEqual(x[1, 3], 0.)

    def test_all_missing_and_zero_signal_remain_distinct_from_eligible_zero_effect(self):
        games = [game(i, i, score=(9, 0)) for i in range(1, 9)]
        blank = LineupHistory({})
        x, meta = blank.walk(games, [anchor(0, 5)] * len(games))
        np.testing.assert_array_equal(x[0], 0.)
        self.assertTrue(meta[0]['all_neutral'])
        self.assertEqual(meta[-1]['home']['form_games'], 6)
        np.testing.assert_array_equal(x[-1, :, :2], [[1., 1.], [-1., -1.]])
        self.assertTrue(meta[-1]['personnel_neutral'])
        h = LineupHistory({1: roster(1, home=squad('same'), away=squad('same'))})
        h.walk([game(1, 0, score=(1, 2))], [anchor()])
        x, meta = h.context(game(2, 2))
        self.assertTrue(meta['all_neutral'])
        self.assertTrue(meta['both_prior_rosters'])
        self.assertTrue(meta['home']['attack']['rating_available'])
        self.assertFalse(meta['home']['attack']['retention_available'])

    def test_incremental_replay_and_rejections_leave_complete_date_blocks(self):
        games = [game(1, 1), game(2, 2), game(3, 3)]
        h = LineupHistory({})
        x, m = h.walk(games[:2], [anchor()] * 2)
        np.testing.assert_array_equal(h.walk(games[:2], [anchor()] * 2)[0], x)
        self.assertEqual(h.walk(games[:2], [anchor()] * 2)[1], m)
        h.walk(games[2:], [anchor()])
        self.assertEqual(h.context(game(4, 4))[1]['home']['form_games'], 3)
        with self.assertRaises(ValueError):
            h.walk([game(5, 2), dict(games[1], home_goals=5)], [anchor()] * 2)
        self.assertEqual(h.context(game(4, 4))[1]['home']['form_games'], 3)
        for rows, anchors in ((games, []), ([games[0]] * 2, [anchor()] * 2), ([game(5, 5)], [np.zeros((2, 2))])):
            with self.assertRaises(ValueError):
                h.walk(rows, anchors)
        with self.assertRaises(ValueError):
            LineupHistory({1: roster(2)}).walk(games[:1], [anchor()])
        with self.assertRaises(ValueError):
            LineupHistory({1: roster(1)}).walk([dict(games[0], home_id='88')], [anchor()])
        self.assertEqual(LineupHistory({}).walk([], [])[0].shape, (0, 2, 6))


if __name__ == '__main__':
    unittest.main()
