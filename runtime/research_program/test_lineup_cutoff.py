from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from research_program import lineup_cutoff as cutoff
from research_program import lineup_study as study
from research_program.test_lineup_study import archives, encoded, fixtures, lines, roster


class LineupCutoffTests(unittest.TestCase):
    def test_two_arms_use_same_target_and_drop_all_history_from_exactly_seven_days_ago(self):
        games = fixtures()
        target = next(g for g in games if g['date'] == '2025-01-12' and g['away'] == 'Club 1')
        anchors, _, _ = study.r7.inputs(games, study.CODE)
        history = cutoff.LineupHistory({})
        earlier = [i for i, g in enumerate(games) if g['date'] < target['date']]
        history.walk([games[i] for i in earlier], anchors[earlier])
        before = cutoff.cutoffs(history, target)
        (fresh, fresh_meta), (older, old_meta) = before
        self.assertEqual(fresh_meta['away']['previous']['date'], '2025-01-05')
        self.assertEqual(old_meta['away']['previous']['date'], '2024-12-23')
        self.assertEqual(fresh_meta['date'], old_meta['date'])
        self.assertFalse(np.array_equal(fresh, older))
        history.walk(games, anchors)
        for (expected, detail), (actual, actual_detail) in zip(before, cutoff.cutoffs(history, target), strict=True):
            np.testing.assert_array_equal(expected, actual)
            self.assertEqual(detail, actual_detail)

    def test_refit_identity_market_and_original_first_must_reproduce(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            games = fixtures()
            r5, r7, r10 = archives(root, games)
            roster_file = root / 'events.jsonl'
            roster_file.write_bytes(lines(roster(g) for g in games))
            manifest = dict(schema='j1-lineups-history-intake-snapshot-v1', events_jsonl_sha256=study.sha(roster_file),
                            events_jsonl_rows=len(games), snapshot_as_of_utc='2026-11-25T00:00:00Z',
                            r5_panel_member_sha256=study.sha256(lines(games)).hexdigest(),
                            r5_panel_package_sha256=study.sha(r5 / 'models/jpn.1.tar.gz'))
            (root / 'snapshot-manifest.json').write_bytes(encoded(manifest))
            out = root / 'r15'
            study.run(r5, r7, r10, roster_file, out)
            games, rosters, anchors, features, fit, settings, _ = cutoff.load(r5, r7, out, roster_file, root / 'inputs')
            history = cutoff.LineupHistory(rosters)
            history.walk(games, anchors)
            train = sum(g['date'] < study.BOUNDS[0] for g in games)
            holdout = sum(g['date'] < study.BOUNDS[1] for g in games)
            priors = cutoff.control.matrices(anchors[train:], features[train:], settings)[holdout-train:]
            originals = study.archive.read_gzip(out / 'forecasts.jsonl.gz')
            rows = [cutoff.evaluate(g, old, prior, history, fit) for g, old, prior in
                    zip(games[holdout:], originals, priors, strict=True)]
            for row, original in zip(rows, originals, strict=True):
                self.assertEqual(row['forecast']['current'], original['forecast']['candidate'])
                self.assertEqual(row['forecast']['market'], original['forecast']['market'])
            report, days = cutoff.summarize(rows[:-1])
            self.assertEqual(report['population'], 6)
            self.assertEqual(report['actual_lines'], 0)
            self.assertEqual(report['rankings']['current']['full_days'], 1)
            self.assertIsNone(days['lagged'][1]['three_first_sweep'])
            self.assertEqual(report['comparisons']['market']['tracks']['market']['metrics']['score_top1']['issued'], 0)
            for field, value, reason in [('window', 'recent', 'fixture'), ('home_id', 'other', 'fixture'),
                                         ('features', [[9] * 6] * 2, 'exposure')]:
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, reason):
                    cutoff.evaluate(games[holdout], dict(originals[0], **{field: value}), priors[0], history, fit)
            revised = deepcopy(originals[0])
            revised['quality']['candidate']['p_first'] += .05
            with self.assertRaisesRegex(ValueError, 'candidate did not reproduce'):
                cutoff.evaluate(games[holdout], revised, priors[0], history, fit)
            (out / 'fit.json').write_bytes(encoded(dict(fit, training_games=999)))
            with self.assertRaisesRegex(ValueError, 'chronological R15 fit'):
                cutoff.load(r5, r7, out, roster_file, root / 'retry')


if __name__ == '__main__':
    unittest.main()
