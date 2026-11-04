"""Software invariants using synthetic matrices, not performance evidence."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from research_program.diagnostics import inspect_matrix, binary, compare, run
from v2_test.data_contract import digest


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.array([[.05,.1,.05],[.25,.3,0],[.2,.05,0]])

    def test_mode_and_independent_direction_can_disagree(self):
        result = inspect_matrix(self.matrix, [2,0], [1,1])
        self.assertFalse(result['primary_hit'])
        self.assertTrue(result['wdl_hit'])
        self.assertTrue(result['mode_miss_direction_hit'])
        self.assertFalse(result['primary_direction_hit'])
        self.assertEqual(result['actual_rank'], 3)
        self.assertAlmostEqual(result['events']['home_zero'][0], .2)
        self.assertAlmostEqual(result['events']['away_zero'][0], .5)
        self.assertEqual(result['events']['total_ge_3'][1], 0)
        self.assertAlmostEqual(result['total_mean_residual'], -.4)

    def test_invalid_probability_or_false_primary_rejected(self):
        for matrix, primary in [(self.matrix*2,[1,1]),(self.matrix,[2,0]),(np.array([[float('nan')]]),[0,0])]:
            with self.assertRaises(ValueError): inspect_matrix(matrix,[2,0],primary)

    def test_calibration_measures_probability_not_selected_score(self):
        result = binary([[.2,0],[.4,1]])
        self.assertAlmostEqual(result['bias'],-.2)
        self.assertAlmostEqual(result['brier'],.2)
        self.assertIsNone(binary([])['bias'])

    def test_paired_correct_only_is_symmetric(self):
        rows = [{'models': {'current_recipe': {'primary':[0,0],'primary_hit':True,'wdl_hit':False},
                            'candidate': {'primary':[1,0],'primary_hit':False,'wdl_hit':True}}}]
        result = compare(rows,'candidate')
        self.assertEqual(result['current_only'],1)
        self.assertEqual(result['net_primary_hits'],-1)

    def test_retained_matrix_file_tampering_cannot_be_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source'; folder=source/'eng.1';folder.mkdir(parents=True)
            path=folder/'2026-01-01.npz';np.savez_compressed(path,current_recipe=self.matrix[None,:,:])
            fold={'origin':'2026-01-01','matrix_file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            record={'match_id':'synthetic','date':'2026-01-02','cohort':'eng.1','stage':'regular','home':'A','away':'B',
                    'actual':[2,0],'fold':'2026-01-01','models':{'current_recipe':{'primary':[1,1],
                    'matrix_file':path.name,'matrix_index':0,'matrix_sha256':digest(self.matrix.tolist())}}}
            (folder/'report.json').write_text(json.dumps({'folds':[fold]}))
            (folder/'predictions.json').write_text(json.dumps([record]))
            report=run(source,Path(directory)/'good');self.assertEqual(report['n'],1)
            np.savez_compressed(path,current_recipe=self.matrix[None,:,:]*2)
            with self.assertRaisesRegex(ValueError,'checksum'):run(source,Path(directory)/'bad')


if __name__ == '__main__':unittest.main()
