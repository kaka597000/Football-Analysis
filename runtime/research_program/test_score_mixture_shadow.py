import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from scipy.stats import poisson
from research_program import score_mixture_shadow as m


def model(h, a):
    return {'lambda_home': h, 'lambda_away': a}


def metrics(hits=20, wdl=50, loss=3., brier=.6, n=100):
    return {'n': n, 'statistical_top1_hits': hits, 'wdl_hits': wdl,
            'score_log_loss': loss, 'wdl_brier': brier}


class ScoreMixtureTests(unittest.TestCase):
    def test_mixes_probability_not_rates_and_preserves_tail(self):
        p = m.distribution(model(.4, .6), model(4., 3.), .5)
        actual = [8, 9]
        expected = .5 * poisson.pmf(8, .4) * poisson.pmf(9, .6) + .5 * poisson.pmf(8, 4.) * poisson.pmf(9, 3.)
        self.assertAlmostEqual(m.score(p, actual)['score_log_loss'], -np.log(expected))
        self.assertNotAlmostEqual(expected, poisson.pmf(8, 2.2) * poisson.pmf(9, 1.8), places=10)
        self.assertAlmostEqual(sum(p['wdl_probs']), 1.)

    def test_exact_integer_modes_use_lower_tie(self):
        self.assertEqual(m.distribution(model(1., 2.), model(7., 8.), 0.)['statistical_top1'], [0, 1])
        self.assertEqual(m.distribution(model(1., 2.), model(3., 4.), 1.)['statistical_top1'], [2, 3])

    def test_quality_guard_and_top1_priority(self):
        bad_quality = ('fresh_missingness', 1.)
        better_top1 = ('fresh_missingness', .5)
        better_loss = ('opponent_residual', .25)
        trials = {m.BASELINE: metrics(), bad_quality: metrics(hits=29, brier=.7),
                  better_top1: metrics(hits=23, loss=2.99), better_loss: metrics(hits=22, loss=2.5)}
        self.assertEqual(m.select(trials), better_top1)

    def test_gate_falls_back_without_candidate_reselection(self):
        selected = ('fresh_missingness', .5)
        self.assertEqual(m.gate(selected, metrics(hits=19, loss=2.7), metrics()), m.BASELINE)
        self.assertEqual(m.gate(selected, metrics(hits=21, wdl=49), metrics()), m.BASELINE)
        self.assertEqual(m.gate(selected, metrics(hits=21), metrics()), selected)
        with self.assertRaisesRegex(ValueError, 'denominators'):
            m.gate(selected, metrics(n=99), metrics())

    def test_whole_date_boundaries_and_cohort_identity(self):
        rows = [{'match_id': str(i), 'date': date, 'cohort': 'eng.1', 'competition': 'eng.1', 'stage': 'regular'}
                for i, date in enumerate(['2021-01-01', '2021-01-02', '2021-01-02', '2021-01-03'])]
        parts = m.split(rows, {'weight_selection_end_exclusive': '2021-01-02', 'gate_end_exclusive': '2021-01-03'})
        self.assertEqual(list(map(len, parts)), [1, 2, 1])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            m.split(rows + [rows[0]], {'weight_selection_end_exclusive': '2021-01-02', 'gate_end_exclusive': '2021-01-03'})
        rows[-1]['cohort'] = 'jpn.1'
        with self.assertRaisesRegex(ValueError, 'independent cohort'):
            m.split(rows, {'weight_selection_end_exclusive': '2021-01-02', 'gate_end_exclusive': '2021-01-03'})

    def test_invalid_rates_and_labels_fail_instead_of_clipping(self):
        for rate in [float('nan'), float('inf'), -1, 0, 51]:
            with self.assertRaises(ValueError):
                m.distribution(model(rate, 1), model(1, 1), .5)
        p = m.distribution(model(1, 1), model(2, 2), .5)
        for actual in [[True, 0], [1.2, 1], [-1, 0]]:
            with self.assertRaises(ValueError):
                m.score(p, actual)

    def test_paired_identical_predictions_have_zero_interval(self):
        rs = [{'date': '2021-01-0' + str(i), 'metrics': {'baseline': {'top1_hit': True, 'wdl_hit': False},
                                                       'gated_candidate': {'top1_hit': True, 'wdl_hit': False}}}
              for i in range(1, 4)]
        result = m.paired(rs, repetitions=100)
        self.assertEqual(result['statistical_top1_net_hits'], 0)
        self.assertEqual(result['statistical_top1_net_rate95'], [0., 0.])

    def test_midnight_target_allowed_but_same_day_training_rejected(self):
        path = Path(__file__).resolve().parents[2] / 'scripts' / 'run_score_mixture_shadow.py'
        spec = importlib.util.spec_from_file_location('mixture_runner_test', path)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        rows = [{'match_id': name, 'date': day, 'model_id': 'fit', 'cohort': 'eng.1',
                 'competition': 'eng.1', 'stage': 'regular'}
                for name, day in [('a', '2021-03-01'), ('b', '2021-03-02')]]
        fit = {'model_id': 'fit', 'group': ['eng.1', 'eng.1', 'regular'],
               'fit_boundary': '2021-03-01T00:00:00+00:00',
               'training_match_ids': ['warmup'], 'training_fixtures': 1}
        identity = [{k: r[k] for k in ('match_id', 'date', 'model_id')} for r in rows]
        with tempfile.TemporaryDirectory() as folder:
            predictions, fits = Path(folder) / 'rows.jsonl', Path(folder) / 'fits.jsonl'
            predictions.write_text('\n'.join(map(json.dumps, rows)))
            fits.write_text(json.dumps(fit))
            protocol = {'alpha_grid': list(m.ALPHAS), 'experts': list(m.EXPERTS), 'baseline': 'goals_only',
                        'minimum_partition_n': 100, 'guard_tolerance': 1e-12,
                        'inputs': {name: {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                   for name, p in [('predictions', predictions), ('fits', fits)]},
                        'partitions': {'eng.1|eng.1|regular': {'identity_sha256': runner.digest(identity)}}}
            groups, audit = runner.verify_inputs(protocol)
            self.assertEqual(len(groups['eng.1|eng.1|regular']), 2)
            self.assertEqual(audit['known_training_date_violations'], 0)
            fit['training_match_ids'] = ['a']
            fits.write_text(json.dumps(fit))
            protocol['inputs']['fits']['sha256'] = hashlib.sha256(fits.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'crosses monthly boundary'):
                runner.verify_inputs(protocol)


if __name__ == '__main__':
    unittest.main()
