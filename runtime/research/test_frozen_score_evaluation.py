import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analysis_freeze import digest
from frozen_score_evaluation import (PROTOCOL, evaluate, register, validate_spec,
                                     paired_uncertainty, total_settlement, implementation_sha256)

T = datetime(2035, 1, 1, tzinfo=timezone.utc)


def stamp(hours):
    return (T+timedelta(hours=hours)).isoformat()


def seal(record):
    record.pop('record_sha256', None)
    record['record_sha256'] = digest(record)
    return record


class FrozenEvaluationTests(unittest.TestCase):
    def spec(self):
        identity = {'version': 'synthetic-v1', 'artifact_sha256': 'a'*64,
                    'training_snapshot_sha256': 'b'*64, 'training_cutoff': stamp(-24)}
        return {'experiment_id': 'synthetic-test', 'protocol_sha256': digest(PROTOCOL),
                'implementation_sha256': implementation_sha256(),
                'matches': [{'match_id': 'synthetic-one', 'cohort': 'synthetic', 'kickoff_at': stamp(3)},
                            {'match_id': 'synthetic-two', 'cohort': 'synthetic', 'kickoff_at': stamp(27)}],
                'tracks': [{'name': n, 'freeze_track': n, 'payload_key': None,
                            'identity_by_cohort': {'synthetic': copy.deepcopy(identity)}} for n in ('current', 'shadow')],
                'baseline_track': 'current', 'evaluation_not_before': stamp(48),
                'development_match_ids': ['synthetic-development']}

    def record(self, mid='synthetic-one', track='current', primary='1-1', revision=1):
        kickoff = 3 if mid == 'synthetic-one' else 27
        fixture = {'match_id': mid, 'cohort': 'synthetic', 'kickoff_at': stamp(kickoff), 'cutoff_at': stamp(1)}
        payload = {'version': 'synthetic-v1', 'artifact_sha256': 'a'*64, 'training_snapshot_sha256': 'b'*64,
                   'training_cutoff': stamp(-24), 'input_snapshot_sha256': 'c'*64,
                   'data_cutoff_at': stamp(.5), 'generated_at': stamp(1.5), 'primary': primary,
                   'matrix': [[.1, .2], [.3, .4]], 'wdl_reference': '主胜'}
        return seal({'schema': 'WCPP-ISSUED-FREEZE-1', 'freeze_id': mid+track+str(revision),
                     'match_id': mid, 'stage': 'prematch', 'track': track, 'revision': revision,
                     'previous_sha256': None,
                     'evaluation_slot': 'FIRST_PREMATCH' if revision == 1 else 'SEPARATE_REVISION_OR_STAGE',
                     'recorded_at': stamp(2), 'fixture': fixture, 'payload': payload})

    def plan(self):
        p = self.record()
        p.update(track='shadow-score-evaluation-plan', payload=self.spec(), recorded_at=stamp(0))
        p['fixture']['cutoff_at'] = stamp(0)
        return seal(p)

    def result(self, mid='synthetic-one', score=(1, 1)):
        return {'match_id': mid, 'status': 'FT', 'score_basis': 'regulation_90', 'home_goals': score[0],
                'away_goals': score[1], 'observed_at': stamp(40), 'source_url': 'https://example.org/synthetic-result'}

    def run_eval(self, records, results=None, plan=None):
        with patch('frozen_score_evaluation.now', return_value=T+timedelta(hours=50)):
            return evaluate(plan or self.plan(), records, [self.result()] if results is None else results)

    def test_full_denominator_and_first_abstention_not_replaced(self):
        first = self.record(primary=None)
        second = self.record(primary='1-1', revision=2)
        second['previous_sha256'] = first['record_sha256']; seal(second)
        r = self.run_eval([first, second, self.record(track='shadow')])
        current = next(g for g in r['diagnosis']['groups'] if g['track'] == 'current')
        self.assertEqual(current['population'], 2); self.assertEqual(current['settled'], 1)
        self.assertEqual(current['primary']['hits'], 0)
        self.assertEqual(r['paired_primary_comparisons'][0]['full_settled_population']['net_hits'], 1)
        self.assertEqual(r['paired_primary_comparisons'][0]['both_primary_available']['n'], 0)
        self.assertEqual(len(r['excluded_revisions_or_stages']), 1)
        self.assertFalse(r['population_complete']); self.assertFalse(r['automatic_promotion'])

    def test_missing_first_never_falls_back_to_revision(self):
        r = self.run_eval([self.record(revision=2)])
        g = next(g for g in r['diagnosis']['groups'] if g['track'] == 'current')
        self.assertEqual(g['primary']['issued'], 0)

    def test_hash_tampering_and_conflicting_first_rejected(self):
        r = self.record(); r['payload']['primary'] = '4-4'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.run_eval([r])
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.run_eval([self.record(), self.record(primary='2-2')])

    def test_model_identity_failure_preserves_population(self):
        r = self.record(); r['payload']['artifact_sha256'] = 'e'*64; seal(r)
        report = self.run_eval([r])
        g = next(g for g in report['diagnosis']['groups'] if g['track'] == 'current')
        self.assertEqual(g['population'], 2); self.assertEqual(g['primary']['hits'], 0)
        self.assertEqual(g['rows'][0]['source']['validation_status'], 'MODEL_IDENTITY_MISMATCH')

    def test_asof_fields_required_and_late_generation_rejected(self):
        r = self.record(); del r['payload']['input_snapshot_sha256']; seal(r)
        g = next(g for g in self.run_eval([r])['diagnosis']['groups'] if g['track'] == 'current')
        self.assertEqual(g['rows'][0]['source']['validation_status'], 'MISSING_ASOF_PROVENANCE')
        for field, value in [('generated_at', stamp(4)), ('data_cutoff_at', stamp(2)), ('generated_at', stamp(-1))]:
            r = self.record(); r['payload'][field] = value; seal(r)
            with self.assertRaisesRegex(ValueError, 'chronology'):
                self.run_eval([r])

    def test_late_plan_or_overlap_or_wrong_protocol_rejected(self):
        p = self.plan(); p['recorded_at'] = stamp(4); seal(p)
        with self.assertRaisesRegex(ValueError, 'late'):
            self.run_eval([], plan=p)
        s = self.spec(); s['development_match_ids'].append('synthetic-one')
        with self.assertRaisesRegex(ValueError, 'development'):
            validate_spec(s)
        s = self.spec(); s['protocol_sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'protocol'):
            validate_spec(s)

    def test_registration_uses_real_writer_and_blocks_same_id_changed_plan(self):
        with tempfile.TemporaryDirectory() as root, patch('frozen_score_evaluation.now', return_value=T), patch('analysis_freeze.datetime') as clock:
            clock.now.return_value = T
            clock.fromisoformat.side_effect = datetime.fromisoformat
            receipt = register(self.spec(), root)
            frozen = json.loads(Path(receipt['path']).read_text())
            self.assertEqual(frozen['evaluation_slot'], 'FIRST_PREMATCH')
            changed = self.spec(); changed['evaluation_not_before'] = stamp(72)
            with self.assertRaisesRegex(ValueError, 'idempotency conflict'):
                register(changed, root)
            self.assertEqual(len(list(Path(root).glob('*/*.json'))), 1)

    def test_registration_cannot_backdate(self):
        with tempfile.TemporaryDirectory() as root, patch('frozen_score_evaluation.now', return_value=T+timedelta(hours=4)):
            with self.assertRaisesRegex(ValueError, 'before first'):
                register(self.spec(), root)

    def test_evaluator_implementation_is_locked(self):
        s = self.spec(); s['implementation_sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'implementation'):
            validate_spec(s)

    def test_wrong_fixture_and_future_record_rejected(self):
        r = self.record(); r['fixture']['cohort'] = 'other'; seal(r)
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.run_eval([r])
        r = self.record(); r['recorded_at'] = stamp(60); seal(r)
        with self.assertRaisesRegex(ValueError, 'chronology'):
            self.run_eval([r])

    def test_duplicate_result_live_score_wrong_basis_and_early_observation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.run_eval([], [self.result(), self.result()])
        for key, value in [('status', 'LIVE'), ('score_basis', 'including_extra_time'), ('observed_at', stamp(4)), ('home_goals', True)]:
            r = self.result(); r[key] = value
            with self.assertRaises(ValueError):
                self.run_eval([], [r])

    def test_invalid_matrix_and_input_mismatch_fail_closed(self):
        r = self.record(); r['payload']['matrix'] = [[.9]]; seal(r)
        with self.assertRaisesRegex(ValueError, 'sum to 1'):
            self.run_eval([r])
        r = self.record(); r['payload']['input'] = {'other': 'fixture'}; seal(r)
        with self.assertRaisesRegex(ValueError, 'input differs'):
            self.run_eval([r])

    def test_independent_wdl_even_with_no_score(self):
        r = self.run_eval([self.record(primary=None)])
        self.assertEqual(r['independent_direction_denominators'][0]['wdl_n'], 1)
        self.assertEqual(r['diagnosis']['groups'][0]['primary']['settled_issued'], 0)

    def quote(self):
        return {'line': 2.25, 'market': 'standard_total', 'score_basis': 'regulation_90',
                'bookmaker': 'synthetic-book', 'source_url': 'https://example.org/synthetic-odds',
                'verified': True, 'source_file_sha256': 'd'*64, 'match_id': 'synthetic-one',
                'quoted_at': stamp(.4), 'captured_at': stamp(.5)}

    def test_actual_line_missing_or_stale_not_invented(self):
        r = self.record(); r['payload']['total'] = {'quote': {'line': 2.5}, 'reference_direction': '小'}; seal(r)
        report = self.run_eval([r]); self.assertEqual(report['independent_direction_denominators'][0]['total_n'], 0)
        r['payload']['total']['quote'] = self.quote(); seal(r)
        report = self.run_eval([r])
        self.assertEqual(report['independent_direction_denominators'][0]['total_settlements'], {'半赢': 1})

    def test_residual_primary_after_abstention_is_not_a_hit(self):
        r = self.record(); r['payload']['status'] = 'ABSTAIN'; seal(r)
        report = self.run_eval([r, self.record(track='shadow')])
        g = next(g for g in report['diagnosis']['groups'] if g['track'] == 'current')
        self.assertEqual(g['primary']['issued'], 0); self.assertEqual(g['primary']['hits'], 0)
        self.assertEqual(report['independent_direction_denominators'][0]['wdl_n'], 1)
        self.assertEqual(report['paired_primary_comparisons'][0]['full_settled_population']['net_hits'], 1)

    def test_total_quote_after_feature_cutoff_or_wrong_market_excluded(self):
        r = self.record()
        for field, value in [('captured_at', stamp(.75)), ('market', 'team_total'), ('score_basis', 'first_half')]:
            bad = self.quote(); bad[field] = value
            r['payload']['total'] = {'quote': bad, 'reference_direction': '小'}; seal(r)
            self.assertEqual(self.run_eval([r])['independent_direction_denominators'][0]['total_n'], 0)

    def test_all_five_total_settlements(self):
        for total, line, direction, expected in [(3, 2.5, '大', '全赢'), (3, 2.75, '大', '半赢'), (3, 3, '大', '走盘'), (3, 3.25, '大', '半输'), (3, 3.5, '大', '全输')]:
            self.assertEqual(total_settlement(total, line, direction), expected)
        with self.assertRaises(ValueError):
            total_settlement(2, 2.3, '大')

    def test_cluster_bootstrap_deterministic_and_one_day_uncertain(self):
        rows = [{'delta': 1, 'kickoff_at': stamp(3)}, {'delta': -1, 'kickoff_at': stamp(27)}]
        a = paired_uncertainty(rows)
        self.assertEqual(a, paired_uncertainty(rows)); self.assertEqual(a['net_hits'], 0)
        self.assertEqual(a['bootstrap95'], [-1., 1.])
        self.assertIsNone(paired_uncertainty(rows[:1])['bootstrap95'])


if __name__ == '__main__':
    unittest.main()
