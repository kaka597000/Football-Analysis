"""Regression coverage for checked CLI dispatch without expensive refits."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
for directory in ('v2', 'v3', 'v4', 'v5', 'v6'):
    sys.path.insert(0, str(ROOT / directory))
import pipeline
import checked_context_learning as checked


def context_rows():
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(240):
        kickoff = start + timedelta(days=i)
        cutoff = (kickoff-timedelta(hours=1)).isoformat()
        rows.append(dict(cohort='eng.1', kickoff_at=kickoff.isoformat(), cutoff_at=cutoff,
            baseline_observed_at='2019-12-01T00:00:00+00:00',
            result_observed_at=(kickoff+timedelta(hours=3)).isoformat(),
            baseline_home=1.5, baseline_away=1., home_goals=1, away_goals=1,
            features={'strength':i % 2},
            evidence=[dict(source_url='https://example.org/official', query_time=cutoff, source_tier='L1')]))
    return rows


def load_cli(version):
    spec = importlib.util.spec_from_file_location('checked_entry_'+version, ROOT/version/'cli.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EntryPointTests(unittest.TestCase):
    def test_v3_and_v4_validation_cannot_bypass_mixed_timezone_cutoff(self):
        rows = context_rows()
        rows[0]['result_observed_at'] = '2020-06-17T01:00:00+08:00'
        rows[1]['result_observed_at'] = '2020-06-16T23:30:00+00:00'
        with patch.object(pipeline, 'prepare_samples', return_value=rows):
            with self.assertRaisesRegex(ValueError, 'artifact not available'):
                pipeline.train_context([], validate=True)
        with patch.object(checked, 'prepare', return_value=rows):
            with self.assertRaisesRegex(ValueError, 'artifact not available'):
                checked.train_context({}, [])

    def test_v4_wrapper_preserves_original_preparation_guard(self):
        with patch.object(checked, 'prepare', side_effect=ValueError('tampered training forecast')):
            with patch.object(checked, 'validate_context') as fit:
                with self.assertRaisesRegex(ValueError, 'tampered'):
                    checked.train_context({}, [])
                fit.assert_not_called()

    def test_v3_training_reports_real_latest_observed_time(self):
        rows = context_rows()
        for row in rows:
            row['result_observed_at'] = '2021-01-01T00:00:00+00:00'
        rows[0]['result_observed_at'] = '2021-01-01T08:00:00+08:00'
        rows[1]['result_observed_at'] = '2021-01-01T01:00:00+00:00'
        original = copy.deepcopy(rows)
        with patch.object(pipeline, 'prepare_samples', return_value=rows):
            fit = pipeline.train_context([])
        self.assertEqual(fit['observed_at'], '2021-01-01T01:00:00+00:00')
        self.assertEqual(rows, original)

    def test_v4_cli_dispatches_checked_context_training(self):
        self.assertIs(load_cli('v4').train_context, checked.train_context)

    def test_history_cli_boundaries_normalize_before_training(self):
        rows = [dict(match_id=str(i), cohort='eng.1', date='2020-01-0'+str(i+1),
                     home='A', away='B', home_goals=1, away_goals=0,
                     neutral=False, score_basis='regulation_90',
                     source_url='https://example.org/results', observed_at=stamp)
                for i,stamp in enumerate(('2021-01-01T08:00:00+08:00',
                                          '2021-01-01T01:00:00+00:00'))][::-1]
        for version in ('v4','v5','v6'):
            module = load_cli(version)
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)/'history.json'; source.write_text(json.dumps(rows))
                out = Path(tmp)/'output'
                argv = ['cli', str(source), '--out', str(out)] if version=='v6' else ['cli','train',str(source),'--directory',str(out)]
                artifact={'cohort':'eng.1','selected_recipe':'baseline'}
                report={'weights':{},'acceptance':{},'test':{}}
                with patch.object(module,'train',return_value=(artifact,report)) as train:
                    with patch('sys.argv',argv): module.main()
                fitted = train.call_args.args[0]
                self.assertEqual([r['match_id'] for r in fitted], ['0','1'])
                self.assertEqual(fitted[0]['observed_at'],'2021-01-01T00:00:00+00:00')
                self.assertEqual(max(r['observed_at'] for r in fitted),'2021-01-01T01:00:00+00:00')
                self.assertEqual(json.loads(source.read_text()),rows)


if __name__ == '__main__': unittest.main()
