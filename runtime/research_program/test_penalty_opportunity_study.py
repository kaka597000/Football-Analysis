from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from research_program import penalty_opportunity_study as study


def game(mid, day, score=(1, 0)):
    return dict(id=mid, competition='eng.1', stage='regular', date=day, home='Alpha', away='Bravo',
                home_goals=score[0], away_goals=score[1], neutral=False, paired_market=False,
                h2h=None, total=None, bookmaker=None, market_time_basis='missing', sources=[], source_sha256='a'*64)


def grid(entries):
    result = np.zeros(study.base.H.shape)
    for cell, weight in entries.items():
        result[cell] = weight
    return result / result.sum()


def issue(g, prior, window, clock):
    line, actual = study.base.actual_line(g), [g['home_goals'], g['away_goals']]
    pick = study.base.pick(prior, line)
    return dict(id=g['id'], cohort=g['competition'], stage=g['stage'], date=g['date'], home=g['home'],
                away=g['away'], actual=actual, line=line, source_sha256=g['source_sha256'], window=window,
                kickoff=clock, prices=dict(h2h=g['h2h'], total=g['total']), bookmaker=g['bookmaker'],
                market_time_basis=g['market_time_basis'], forecast=dict(r7=pick, market={}, candidate=pick),
                quality=dict(r7=study.r22._quality(prior, pick, actual), market=None,
                             candidate=study.r22._quality(prior, pick, actual)))


class SelectionTests(unittest.TestCase):
    def test_validation_priority_and_exact_zero_dose(self):
        g = game('one', '2024-08-01')
        prior = grid({(0, 0): .7, (1, 0): .2, (2, 0): .1})
        full = grid({(0, 0): .4, (1, 0): .5, (2, 0): .1})
        r7 = dict(primary='0-0', top3=['0-0', '2-0', '1-0'], wdl='away', total_side=None, total_line=None)
        dose, trials, details = study._selection([g], [prior], [full], [r7])
        self.assertEqual(dose, 1.)
        self.assertEqual([r['dose'] for r in trials], [0., .5, 1.])
        self.assertEqual([r['first_hits'] for r in trials], [0, 0, 1])
        self.assertEqual(details[0][0]['top3'], r7['top3'])
        self.assertEqual(study._selection([g], [prior], [prior], [r7])[0], 0.)
        for dose in study.DOSES:
            pick, _ = study._pick(prior, full, dose, r7, None)
            self.assertEqual(pick['wdl'], 'away')

    def test_ordered_three_then_likelihood(self):
        g = game('one', '2024-08-01')
        prior = grid({(0, 0): .42, (2, 0): .29, (0, 2): .19, (1, 0): .1})
        full = grid({(0, 0): .45, (2, 0): .31, (1, 0): .21, (0, 2): .03})
        r7 = study.base.pick(prior, None)
        dose, trials, _ = study._selection([g], [prior], [full], [r7])
        self.assertEqual(dose, 1.)
        self.assertEqual([r['first_hits'] for r in trials], [0, 0, 0])
        self.assertEqual([r['top3_hits'] for r in trials], [0, 1, 1])
        self.assertLess(trials[2]['negative_log_likelihood'], trials[1]['negative_log_likelihood'])

    def test_archive_manifest_from_a_later_release_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / 'workspace-updates/20261125-r22-draw-preserving'
            pkg.mkdir(parents=True)
            (pkg / 'manifest.json').write_text('{"files": {}}')
            with self.assertRaisesRegex(ValueError, study.R22_DATA):
                study._archives(root)

    def test_missing_identity_is_neutral_but_retained(self):
        g = game('one', '2025-07-01')
        home = dict(n=0, games=[], source_team_id=None)
        away = dict(n=1, games=[dict(date='2025-06-30', valid=True)], source_team_id='2')
        x = np.array([[0., .07, .02, 0.]])
        meta = [dict(cutoff=g['date'], league_mean=.1, home=home, away=away)]
        study._check_design([g], x, meta)
        self.assertEqual(study._coverage(meta, x)['one_club'], 1)
        self.assertEqual(study._coverage(meta, x)['sides']['home']['missing_identity'], 1)
        away['games'][0]['date'] = g['date']
        with self.assertRaisesRegex(ValueError, 'as-of club history'):
            study._check_design([g], x, meta)


class ReplayTests(unittest.TestCase):
    def test_fit_and_complete_refit_use_distinct_chronological_anchors(self):
        days = ['2024-06-27', '2024-06-30', '2024-07-01', '2025-06-30', '2025-07-01', '2026-07-01', '2026-11-21']
        fixtures = [game(str(i), day, (i % 3, i % 2)) for i, day in enumerate(days)]
        games, extension = fixtures[:-1], fixtures[-1:]
        raw, _, state = study.r7.inputs(games, 'eng.1')
        settings = dict(bounds=study.BOUNDS, training_through=days[1], training_games=2, selection={'mix': 0.})
        recipe = dict(through=days[-2], n=len(games), competition='eng.1', state=study.r7.dump(state), policy={'mix': 0.})
        controls = list(raw[2:]) + [study.r7_forward.matrix(extension[0], recipe)]
        windows = [study.WINDOWS[0], *study.WINDOWS]
        clocks = {g['id']: dict(source_time='15:00') for g in fixtures}
        ref = [issue(g, prior, w, clocks[g['id']])
               for g, prior, w in zip(fixtures[2:], controls, windows, strict=True)]
        locked = dict(base_fixtures=len(games), validation_fixtures=2, evaluation_fixtures=1, recent_fixtures=1,
                      extension_fixtures=1)
        source = dict(competition='eng.1', home='Alpha', away='Bravo', home_id='1', away_id='2',
                      source_sha256='b'*64, source_history_sides=dict(
                          home=dict(xG=1.8, npxG=1., xGA=1., npxGA=1.),
                          away=dict(xG=1., npxG=1., xGA=1.8, npxGA=1.)))
        events = [dict(source, source_match_id=idx, date=day) for idx, day in enumerate(days[:-1], 1)]
        with tempfile.TemporaryDirectory() as tmp, patch.object(study.model, 'fit', wraps=study.model.fit) as fit:
            out = Path(tmp) / 'league'
            rows, batches, _ = study._replay('eng.1', games, extension, events, settings, recipe,
                                             dict(validation_through=days[3], dose=1.), ref, clocks, locked, {}, out)
            self.assertEqual([len(call.args[0]) for call in fit.call_args_list], [2, 7])
            np.testing.assert_array_equal(fit.call_args_list[0].args[0], raw[:2])
            self.assertEqual([r['window'] for r in rows], windows)
            self.assertEqual([r['process']['home']['n'] for r in rows], [2, 3, 4, 5, 6])
            self.assertTrue(all(b['primary_sweep'] is None for b in batches))
            fit_file, forward = [study._read(out / f'{name}.json') for name in ('fit', 'forward')]
            self.assertEqual((fit_file['training_through'], fit_file['validation_through'], forward['through']),
                             (days[1], days[3], days[-1]))
            self.assertEqual(forward['dose'], fit_file['dose'])
            self.assertEqual(forward['fit_sha256'], study.sha(out / 'fit.json'))
            self.assertEqual([len(data['fitted']['beta']) for data in (fit_file, forward)], [2, 2])
            self.assertEqual(len(study.archive.read_gzip(out / 'forecasts.jsonl.gz')), 3)

    def test_outside_grid_is_counted_in_fit_and_zero_in_selection(self):
        prior = grid({(0, 0): .5, (1, 0): .4, (2, 0): .1})
        g = game('one', '2024-08-01', (25, 0))
        fitted, count = study._fit(prior[None], np.zeros((1, 4)), np.array([[25, 0]]))
        self.assertEqual(count, 1)
        self.assertEqual(fitted['outside_grid'], 1)
        dose, trials, _ = study._selection([g], [prior], [prior], [study.base.pick(prior, None)])
        self.assertEqual(dose, 0.)
        self.assertTrue(all(t['negative_log_likelihood'] > 690. for t in trials))

    def test_market_tied_alternates_keep_the_archived_order(self):
        g = game('one', '2025-12-14', (3, 0))
        g.update(h2h=dict(home=2.7, draw=3.3, away=2.7), total=dict(line=2.5, over=1.91, under=1.91),
                 bookmaker='Bet365', paired_market=True)
        prior, _, _, _ = study.base.baseline(g, np.full(study.base.H.shape, 1 / study.base.H.size))
        clock = dict(source_time='15:00')
        ref = issue(g, prior, 'evaluation', clock)
        market = dict(ref['forecast']['r7'], top3=['1-1', '0-1', '1-0'])
        ref['forecast']['market'] = market
        ref['quality']['market'] = study.r22._quality(prior, market, ref['actual'])
        row = study._record(g, 'evaluation', prior, prior, 0., np.zeros(4), {}, ref, clock)
        self.assertEqual(row['forecast']['market'], market)
        self.assertIsNone(row['price_quote_at'])


def _audit_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def _audit_calls(row, arm):
    h, a = row['actual']
    score = f'{h}-{a}'
    pick = row['forecast'][arm]
    assert len(pick['top3']) == len(set(pick['top3'])) == 3 and pick['top3'][0] == pick['primary']
    goals = h + a
    line = row['line']
    # The locked replay has a real half-goal line on every comparison row.
    assert line == pick['total_line'] == 2.5
    assert pick['total_side'] in ('over', 'under')
    total_hit = (goals > line) == (pick['total_side'] == 'over')
    wdl = 'home' if h > a else 'away' if h < a else 'draw'
    return dict(score_top1=score == pick['primary'], score_top3=score in pick['top3'],
                wdl=wdl == pick['wdl'], actual_ou=total_hit)


def _audit_window(rows, batches, official, arms):
    assert official['population'] == len(rows)
    calls = {r['id']: {arm: _audit_calls(r, arm) for arm in arms} for r in rows}
    assert all(calls[r['id']] == r['outcomes'] for r in rows)
    rates, deltas, seats = {}, {}, {}
    for arm in arms:
        outcomes = [calling[arm] for calling in calls.values()]
        rates[arm] = {k: sum(r[k] for r in outcomes) for k in outcomes[0]}
        for key, count in rates[arm].items():
            stored = official['tracks'][arm]['metrics'][key]
            assert (stored['hits'], stored['eligible'], stored['issued']) == (count, len(rows), len(rows))
    for pair, reported in official['paired'].items():
        baseline, candidate = pair.split('_vs_')
        deltas[pair] = {}
        for key, stored in reported.items():
            by_date = defaultdict(list)
            changes = []
            for r in rows:
                call = calls[r['id']]
                change = int(call[candidate][key]) - int(call[baseline][key])
                changes.append(change)
                by_date[r['date']].append(change)
            counts = Counter(changes)
            assert (stored['gained'], stored['lost'], stored['tied'], stored['eligible']) == (
                counts[1], counts[-1], counts[0], len(rows))
            days = [by_date[day] for day in sorted(by_date)]
            interval = None
            if len(days) >= 2:
                sampled = np.random.default_rng(20261125).integers(len(days), size=(5000, len(days)))
                numerator = np.asarray([sum(day) for day in days])[sampled].sum(axis=1)
                denominator = np.asarray(list(map(len, days)))[sampled].sum(axis=1)
                interval = np.quantile(numerator / denominator, [.025, .975]).tolist()
                np.testing.assert_allclose(stored['date_cluster_ci'], interval, atol=1e-14, rtol=0)
            else:
                assert stored['date_cluster_ci'] is None
            deltas[pair][key] = [counts[1], counts[-1], interval]
    groups = defaultdict(list)
    for r in rows:
        groups[r['cohort'], r['date']].append(r)
    for arm in arms:
        by_batch = {(b['cohort'], b['date']): b for b in batches if b['arm'] == arm}
        assert set(groups) == set(by_batch)
        first = seats_three = full_batches = swept = short = 0
        for key, fixtures in groups.items():
            b = by_batch[key]
            expected_ids = [r['id'] for r in sorted(fixtures, key=lambda r: (
                -r['quality'][arm]['p_first'], r['kickoff']['source_time'], r['id']))]
            ids = [s['match_id'] for s in b['selected']]
            assert expected_ids == b['ordered_ids'] and ids == expected_ids[:3]
            assert b['short'] == (len(ids) < 3)
            hit = sum(calls[mid][arm]['score_top1'] for mid in ids)
            sweep = hit == 3 if len(ids) == 3 else None
            assert b['primary_sweep'] is sweep
            first += calls[ids[0]][arm]['score_top1']
            seats_three += len(ids)
            full_batches += len(ids) == 3
            swept += bool(sweep)
            short += len(ids) < 3
        stored = official['three_seat_sweep'][arm]
        assert (stored['batches'], stored['occupied_slots'], stored['full_batches'], stored['short_batches'],
                stored['three_first_sweeps'], stored['per_seat']['1']['metrics']['score_top1']['hits']) == (
                    len(groups), seats_three, full_batches, short, swept, first)
        seats[arm] = dict(chief_hits=first, batches=len(groups), all_three_first=swept,
                          full_batches=full_batches, short_batches=short, occupied_seats=seats_three)
    both = sum(r['process']['home']['n'] > 0 and r['process']['away']['n'] > 0 for r in rows)
    assert official['coverage']['both_clubs'] == both
    return dict(fixtures=len(rows), both_clubs=both, hits=rates, paired=deltas, seats=seats)


def audit_locked_replays(data_root, study_root, repeat_root):
    """Independently check complete locked replay rows, ranks, dates and statistics."""
    def payloads(root):
        return {p.relative_to(root): p for p in root.rglob('*') if p.is_file()}

    left, right = payloads(study_root), payloads(repeat_root)
    assert left.keys() == right.keys(), 'study and repeat have different files'
    digests = {}
    for name in sorted(left):
        data = left[name].read_bytes()
        assert data == right[name].read_bytes(), f'study/repeat difference in {name}'
        digests[name.as_posix()] = hashlib.sha256(data).hexdigest()

    arms = ('r7', 'r22', 'market', 'candidate', 'full_dose')
    windows = dict(validation_2024_25=1752, evaluation=1752, recent=500, november_extension=48)
    metrics = json.loads((study_root / 'metrics.json').read_bytes())
    reference = data_root / 'workspace-updates/20261125-r22-draw-preserving/replay/reference/main'
    all_rows, all_batches, leagues = [], [], {}
    for code in ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1'):
        folder = study_root / 'main' / code
        def linefile(name):
            return _audit_rows(folder / (name + '.jsonl.gz'))

        rows = linefile('validation') + linefile('forecasts')
        batches = linefile('validation-seats') + linefile('seats')
        old = {r['id']: r for name in ('validation', 'forecasts')
               for r in _audit_rows(reference / code / (name + '.jsonl.gz'))}
        assert set(old) == {r['id'] for r in rows} and len(old) == len(rows)
        fit = json.loads((folder / 'fit.json').read_bytes())
        forward = json.loads((folder / 'forward.json').read_bytes())
        exposure = linefile('exposure')
        training = [r for r in exposure if r['date'] < '2024-07-01']
        assert fit['training_games'] == len(training) and fit['training_through'] == training[-1]['date']
        assert forward['training_games'] == len(exposure) and forward['through'] == exposure[-1]['date']
        assert all(t['training_outside_grid'] == 0 and len(t['fitted']['beta']) == 2 for t in (fit, forward))
        trial_rows = defaultdict(list)
        for row in rows:
            original = old[row['id']]
            for field in ('date', 'home', 'away', 'actual', 'source_sha256', 'line', 'kickoff', 'prices', 'window'):
                assert row[field] == original[field], (row['id'], field)
            assert row['price_quote_at'] is None and not row['market_as_of_certified']
            for arm, original_arm in (('r7', 'r7'), ('market', 'market'), ('r22', 'candidate')):
                assert row['forecast'][arm] == original['forecast'][original_arm], (row['id'], arm)
            for arm in ('candidate', 'full_dose'):
                assert all(row['forecast'][arm][k] == row['forecast']['r7'][k]
                           for k in ('wdl', 'total_side', 'total_line'))
            if fit['dose'] == 0.:
                assert row['forecast']['candidate'] == row['forecast']['r7']
            actual = '-'.join(map(str, row['actual']))
            for trial in row.get('validation_doses', []):
                trial_rows[trial['dose']].append((actual, trial))
        scores = []
        for trial in fit['validation_trials']:
            dose = trial['dose']
            data = trial_rows[dose]
            first = sum(actual == r['primary'] for actual, r in data)
            three = sum(actual in r['top3'] for actual, r in data)
            loss = math.fsum(-math.log(max(1e-300, r['p_observed'])) for _, r in data)
            assert (first, three, len(data)) == (trial['first_hits'], trial['top3_hits'], fit['validation_games'])
            assert math.isclose(loss, trial['negative_log_likelihood'], abs_tol=1e-10, rel_tol=0.)
            scores.append((-first, -three, loss, dose))
        assert fit['dose'] == forward['dose'] == min(scores)[-1]
        for row in exposure:
            assert row['process']['cutoff'] == row['date']
            components = []
            for side in ('home', 'away'):
                history = row['process'][side]['games']
                assert len(history) <= 12 and all(g['date'] < row['date'] for g in history)
                valid = [g for g in history if g['valid']]
                n = len(valid)
                assert n == row['process'][side]['n']
                mean = row['process']['league_mean']
                components.extend([float(np.clip((math.fsum(g[k] for g in valid) - n * mean) / (n + 8), -.75, .75))
                                   if n else 0. for k in ('xg_for', 'xg_against')])
            np.testing.assert_allclose(row['features'], np.asarray(components)[[0, 3, 2, 1]], atol=2e-15, rtol=0.)
        leagues[code] = {w: _audit_window([r for r in rows if r['window'] == w],
                                          [b for b in batches if b['window'] == w],
                                          metrics['competitions'][code][w], arms) for w in windows}
        all_rows.extend(rows)
        all_batches.extend(batches)
    assert Counter(r['window'] for r in all_rows) == windows
    assert len({r['id'] for r in all_rows}) == sum(windows.values())
    overall = {w: _audit_window([r for r in all_rows if r['window'] == w], [b for b in all_batches if b['window'] == w],
                                metrics['overall'][w], arms) for w in windows}
    packed = json.dumps(digests, sort_keys=True, separators=(',', ':')).encode()
    return dict(independent_matches=sum(windows.values()),
                historical_matches=sum(windows.values()) - windows['validation_2024_25'],
                files_compared=len(digests), file_sha256=digests, replay_tree_sha256=hashlib.sha256(packed).hexdigest(),
                windows=overall, competitions=leagues)


if __name__ == '__main__':
    unittest.main()
