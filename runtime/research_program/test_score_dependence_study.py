import copy
from datetime import date, timedelta
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from research_program import score_dependence_study as study


def fixture(mid, day, score=(1, 0)):
    return dict(id=mid, competition='eng.1', date=day, stage='regular', home='Alpha', away='Bravo',
                home_goals=score[0], away_goals=score[1], neutral=False, season='2526', h2h=None,
                total=None, bookmaker=None, paired_market=False, market_time_basis='missing',
                sources=[], source_sha256='a' * 64)


def pick(first='1-0'):
    return dict(primary=first, top3=[first, '0-1', '1-1'], wdl='home', total_line=None, total_side=None)


def controls(g, window):
    row = dict(id=g['id'], cohort=g['competition'], date=g['date'], home=g['home'], away=g['away'],
               actual=[g['home_goals'], g['away_goals']], line=None, stage=g['stage'],
               window=window, source_sha256=g['source_sha256'])
    signed = pick('2-0')
    return (dict(row, forecast=dict(market={})), dict(row, forecast=dict(candidate=signed, market={})),
            dict(row, forecast=dict(candidate=pick(), market={}, r7=signed),
                 quality={'candidate': dict(p_first=.15, p_three=.4, p_observed=.2)}))


class CutoffsAndControls(unittest.TestCase):
    def setUp(self):
        self.days = ['2024-06-28', '2024-06-30', '2024-07-01', '2025-06-30', '2025-07-01',
                     '2025-07-02', '2026-07-01', '2026-11-08', '2026-11-21', '2026-11-22']
        self.games = [fixture(str(i), d, (i % 3, i % 2)) for i, d in enumerate(self.days)]
        shared = dict(competition='eng.1', home='Alpha', away='Bravo', home_id='1', away_id='2',
                      source_sha256='b' * 64, source_history_sides=dict(home=dict(npxG=1, npxGA=2),
                                                                      away=dict(npxG=2, npxGA=1)))
        self.observed = [dict(shared, source_match_id='prior', date='2025-06-30'),
                         dict(shared, source_match_id='same_day', date='2025-07-01')]
        self.settings = dict(selection=dict(mix=0.), training_through=self.days[1], bounds=study.BOUNDS)
        _, _, state = study.r7.inputs(self.games[:8], 'eng.1')
        self.recipe = dict(through=self.days[7], competition='eng.1', state=study.r7.dump(state), policy=dict(mix=0.))
        self.refs = [list(reversed(col)) for col in zip(*(controls(g, 'evaluation' if i < 6 else 'recent')
                      for i, g in enumerate(self.games[4:8], 4)), strict=True)]
        self.clocks = {g['id']: dict(source_time='15:00') for g in self.games}

    def replay(self, target):
        return study.replay('eng.1', self.games[:8], self.games[8:], self.observed, self.refs,
                            self.settings, self.recipe, 'b' * 64, self.clocks, target)

    def test_real_training_validation_and_later_refit_with_genuine_model_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'study'
            with (patch.object(study.model, 'fit', wraps=study.model.fit) as trained,
                  patch.object(study.decision, 'choose', wraps=study.decision.choose) as choose):
                rows, batches = self.replay(out)
            self.assertEqual([(len(call.args[0]), call.kwargs['constant']) for call in trained.call_args_list],
                             [(2, False), (2, True), (10, False), (10, True)])
            for call in choose.call_args_list:
                self.assertEqual([g['date'] for g in call.args[0]], self.days[2:4])
            self.assertEqual(choose.call_count, 2)
            self.assertEqual([r['id'] for r in rows], ['4', '5', '6', '7', '8', '9'])
            self.assertEqual([r['window'] for r in rows], ['evaluation']*2 + ['recent']*2 + ['november_extension']*2)
            self.assertEqual(len(batches), 6)
            self.assertTrue(all(r['line'] is None for r in rows))
            self.assertTrue(all('r10' in r['forecast'] for r in rows[:4]))
            self.assertTrue(all('r10' not in r['forecast'] for r in rows[4:]))
            self.assertEqual(rows[0]['forecast']['r7']['primary'], '2-0')
            self.assertEqual(rows[0]['quality']['r10']['p_first'], .15)
            self.assertEqual([e['source_id'] for e in rows[0]['process']['home']['games']], ['prior'])
            self.assertEqual([e['source_id'] for e in rows[1]['process']['home']['games']], ['prior', 'same_day'])
            exposed = study.archive.read_gzip(out / 'exposure.jsonl.gz')
            self.assertEqual([e['id'] for e in exposed], [g['id'] for g in self.games])
            self.assertEqual(study.archive.read_gzip(out / 'forecasts.jsonl.gz'), rows)
            self.assertEqual(study.archive.read_gzip(out / 'seats.jsonl.gz'), batches)
            fit, forward = (study.control.read(out / nm) for nm in ('fit.json', 'forward.json'))
            self.assertEqual(set(forward), set('competition version through baseline_sha256 fit_sha256 fitted doses'.split()))
            self.assertEqual((forward['through'], forward['fit_sha256'], forward['baseline_sha256']),
                             (self.days[-1], study.sha(out / 'fit.json'), 'b' * 64))
            self.assertEqual(forward['doses'], fit['doses'])
            for arm, n in [('context', 3), ('constant', 1)]:
                self.assertEqual((fit['fitted'][arm]['training_rows'], forward['fitted'][arm]['training_rows']), (2, 10))
                self.assertEqual(len(forward['fitted'][arm]['beta']), n)
                self.assertEqual(set(forward['doses'][arm]), set(study.HEADS))

    def test_changed_holdout_outcomes_do_not_select_doses_or_fit_test_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / 'original'
            second = Path(tmp) / 'changed'
            before, _ = self.replay(first)
            self.games[4]['home_goals'] = 4
            for rows in self.refs:
                next(r for r in rows if r['id'] == '4')['actual'] = [4, self.games[4]['away_goals']]
            after, _ = self.replay(second)
            self.assertEqual((first/'fit.json').read_bytes(), (second/'fit.json').read_bytes())
            self.assertEqual(before[0]['forecast'], after[0]['forecast'])
            self.assertEqual(before[0]['features'], after[0]['features'])
            self.assertNotEqual(study.control.read(first/'forward.json')['fitted'],
                                study.control.read(second/'forward.json')['fitted'])

    def test_boundary_overlap_missing_or_wrong_window_controls(self):
        for issue in ('duplicate', 'absent', 'overlap', 'old_fit', 'swapped_window'):
            with self.subTest(issue=issue), tempfile.TemporaryDirectory() as tmp:
                games, settings, refs = copy.deepcopy((self.games, self.settings, self.refs))
                if issue == 'duplicate':
                    refs[0].append(refs[0][0])
                elif issue == 'absent':
                    refs[1].pop()
                elif issue == 'overlap':
                    games[8]['date'] = games[7]['date']
                elif issue == 'old_fit':
                    settings['training_through'] = games[2]['date']
                else:
                    refs[2][0]['window'] = 'evaluation'
                with self.assertRaises(ValueError):
                    study.replay('eng.1', games[:8], games[8:], self.observed, refs, settings, self.recipe, 'hash',
                                 self.clocks, Path(tmp)/'failed')

    def test_changed_line_identity_price_origin_or_original_pick_rejected(self):
        g = self.games[4]
        for arm, key, value in [(0, 'source_sha256', 'new'), (1, 'line', 2.5), (2, 'home', 'Bravo'),
                                (0, 'actual', [9, 0]), (2, 'r7', pick('0-0')), (1, 'market', pick())]:
            with self.subTest(arm=arm, key=key):
                refs = controls(g, 'evaluation')
                if key in ('r7', 'market'):
                    refs[arm]['forecast'][key] = value
                else:
                    refs[arm][key] = value
                with self.assertRaises(ValueError):
                    study.original_controls(g, 'evaluation', *refs)

    def test_strict_history_slots_cutoff_and_usable_counts(self):
        g = self.games[4]
        x, metadata = study.model.Design(self.observed, 'eng.1').walk([g])
        study.check_design([g], x, metadata)
        for issue in ('future', 'same_day', 'too_old', 'duplicate', 'count', 'wrong_target'):
            with self.subTest(issue=issue):
                meta = copy.deepcopy(metadata[0])
                entries = meta['home']['games']
                if issue == 'count':
                    meta['home']['usable'] += 1
                elif issue == 'wrong_target':
                    meta['cutoff'] = self.days[1]
                elif issue == 'duplicate':
                    entries.append(entries[0])
                    meta['home']['usable'] += 1
                else:
                    delta = {'future': 1, 'same_day': 0, 'too_old': -151}[issue]
                    entries[0]['date'] = (date.fromisoformat(g['date']) + timedelta(days=delta)).isoformat()
                with self.assertRaises(ValueError):
                    study.check_design([g], x, [meta])

    def test_matched_and_fixed_arms_and_probability_of_signed_original(self):
        g = self.games[4]
        grid = np.full(study.base.H.shape, .01)
        grid[1, 1] = 4.
        grid /= grid.sum()
        x = np.array([1., .3, -.2])
        fits = {arm: study.model.fit([grid], x[None, :], np.array([[1, 1]]), constant=arm == 'constant')
                for arm in study.ARMS}
        doses = dict(context=dict(score=0., wdl=.5, total=0.), constant=dict.fromkeys(study.HEADS, 1.))
        original = pick('2-0')
        row = study.record(g, 'evaluation', grid, x, {}, fits, doses, original, {}, self.clocks[g['id']])
        self.assertEqual(row['forecast']['candidate']['top3'], original['top3'])
        self.assertEqual(row['forecast']['constant_matched']['top3'], original['top3'])
        self.assertEqual(row['quality']['candidate']['p_first'], grid[2, 0])
        self.assertNotEqual(row['quality']['candidate']['p_first'], float(grid.max()))
        self.assertEqual(row['forecast']['constant'], row['forecast']['fixed_constant'])
        self.assertNotEqual(row['forecast']['constant']['primary'], original['primary'])
        self.assertIsNone(row['forecast']['candidate']['total_side'])


class EvidenceAndSeatTests(unittest.TestCase):
    def test_protocol_pin_and_no_overwriting_outputs(self):
        self.assertEqual(study.sha(study.PROTOCOL), study.PROTOCOL_SHA)
        self.assertEqual(tuple(study.control.read(study.PROTOCOL)['competitions']), study.CODES)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with self.assertRaises(FileExistsError):
                study.run(*([out]*6))

    def test_raw_kickoff_bound_to_source_and_quote_category(self):
        blob = (b'Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,B365CH,B365CD,B365CA,'
                b'B365>2.5,B365<2.5,B365C>2.5,B365C<2.5\n'
                b'E0,01/07/2025,19:45,Alpha,Bravo,1,0,2.1,3.2,3.6,2.0,3.3,3.8,1.8,2.1,1.9,2.0\n')
        digest = hashlib.sha256(blob).hexdigest()
        raw = study.csv_rows(blob, digest)
        g = fixture('a', '2025-07-01')
        g.update(h2h=dict(home=2., draw=3.3, away=3.8), total=dict(line=2.5, over=1.9, under=2.),
                 source_sha256=digest, sources=[dict(row=2, sha256=digest,
                                         url='https://www.football-data.co.uk/mmz4281/2526/E0.csv')])
        self.assertEqual(study.source_clock(g, raw)['source_time'], '19:45')
        self.assertIsNone(study.source_clock(g, raw)['utc'])
        with self.assertRaisesRegex(ValueError, 'prices'):
            study.source_clock(g, raw, extension=True)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            study.csv_rows(blob + b' ', digest)
        for key, value in [('source_sha256', 'b'), ('home', 'Bravo'), ('home_goals', 2), ('season', '2627')]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'fixture'):
                study.source_clock(dict(g, **{key: value}), raw)

    def test_full_order_exact_sweeps_and_short_denominators(self):
        def row(mid, stamp, prob, actual, code='eng.1', day='2025-08-01'):
            return dict(id=mid, cohort=code, window='evaluation', date=day, actual=actual, line=None,
                        kickoff=dict(source_time=stamp), forecast=dict(candidate=pick()),
                        quality=dict(candidate=dict(p_first=prob)))
        rows = [row('c', '12:30', .2, [0, 1]), row('b', '12:30', .2, [1, 0]), row('a', '15:00', .2, [1, 0]),
                row('z', '16:00', .3, [1, 0]), row('f', '12:30', .2, [1, 0], code='fra.1'),
                row('s', '12:30', .2, [1, 0], day='2025-08-02')]
        batches = study.rank_batches(rows)
        self.assertEqual(batches[0]['ordered_ids'], ['z', 'b', 'c', 'a'])
        self.assertEqual([(r['batch_rank'], r['seat'], r['kickoff_order']) for r in rows[:4]],
                         [(3, 3, 2), (2, 2, 1), (4, None, 3), (1, 1, 4)])
        self.assertEqual([b['primary_sweep'] for b in batches], [False, None, None])
        self.assertEqual((batches[0]['primary_hits'], batches[0]['three_hits']), (2, 3))
        stats = study.settle_batches(batches)
        self.assertEqual((stats['full_batches'], stats['short_batches'], stats['three_first_sweeps']), (1, 2, 0))
        self.assertEqual([s['occupied'] for s in stats['per_seat'].values()], [3, 1, 1])
        self.assertEqual(stats['per_seat']['3']['metrics']['score_top3'], dict(hits=1, eligible=1))
        self.assertEqual(stats['per_seat']['3']['metrics']['actual_ou'], dict(hits=0, eligible=0))
        rows[0]['actual'] = [1, 0]
        self.assertTrue(study.rank_batches(rows)[0]['primary_sweep'])

    def test_missing_comparator_preserves_full_population(self):
        rows = [dict(id=str(i), cohort='eng.1', window='november_extension', date='2026-11-2'+str(i),
                     actual=[1, 0], line=None, kickoff=dict(source_time='15:00'),
                     process=dict(home=dict(usable=0), away=dict(usable=0)),
                     quality=dict(candidate=dict(p_first=.2)), forecast={'candidate': pick()}) for i in (1, 2)]
        stats = study.summarize(rows, study.rank_batches(rows))
        self.assertEqual(stats['population'], 2)
        self.assertEqual(stats['tracks']['candidate']['metrics']['score_top1']['hits'], 2)
        comp = stats['paired']['candidate_vs_r10']['score_top1']
        self.assertEqual((comp['eligible'], comp['resolved_gate_denominator'], comp['date_cluster_ci']), (0, 2, None))
        self.assertEqual(stats['side_exposure']['0'], 2)
        self.assertEqual(stats['tracks']['candidate']['metrics']['actual_ou']['eligible'], 0)


if __name__ == '__main__':
    unittest.main()
