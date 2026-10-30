import copy
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
import numpy as np
from layered_selector import select, compare_pair
from layered_replay import evaluate
from analysis_freeze import digest


class SelectorTests(unittest.TestCase):
    def example(self):
        # A deliberately constructed math example, not a real prediction.
        return [[0, .21, 0], [.25, .30, 0], [.24, 0, 0]]

    def test_direction_conditional_band_can_sacrifice_joint_top1(self):
        m=self.example();before=copy.deepcopy(m);r=select(m)
        self.assertEqual(r['joint_top1'],[1,1]);self.assertEqual(r['layered_pick'],[1,0])
        self.assertAlmostEqual(r['direction_probabilities']['home'],.49)
        self.assertAlmostEqual(sum(r['conditional_band_probabilities']),1)
        self.assertLess(r['layered_probability'],r['joint_probability'])
        self.assertEqual(m,before)

    def test_tail_rectangular_axes_and_deterministic_ties(self):
        m=np.zeros((6,3));m[5,0]=.6;m[0,2]=.4
        self.assertEqual(select(m)['layered_pick'],[5,0])
        self.assertEqual(select(m)['selected_band'],[4,None])
        m=np.array([[0,.5],[.5,0]])
        self.assertEqual(select(m)['layered_pick'],[1,0])  # Home direction tie wins.
        self.assertEqual(select(m)['joint_top1'],[0,1])  # Row-major exact-score tie.

    def test_invalid_matrix_and_unresolved_tail_rejected(self):
        for m in ([[.9]],[[float('nan')]],[[1.1,-.1]],[],[1,0]):
            with self.subTest(m=m),self.assertRaises(ValueError):select(m)
        with self.assertRaises(ValueError):select([[1]],overflow=.001)

    def test_pair_never_overrides_primary_or_uses_abstained_model(self):
        pair={'current_model':{'version':'A','matrix':self.example(),'primary':'2-0'},
              'test_model':{'status':'ABSTAIN_UNKNOWN_TEAM','matrix':[[1]],'primary':None}}
        before=copy.deepcopy(pair);r=compare_pair(pair)
        self.assertEqual(pair,before);self.assertEqual(r['models']['current_model']['original_primary'],'2-0')
        self.assertEqual(r['models']['test_model']['status'],'ABSTAIN_SOURCE_MODEL')

    def test_replay_uses_first_freeze_preserves_sources_and_counts_both_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);now=datetime.now(timezone.utc)
            for i,actual in enumerate(([1,0],[1,1])):
                d=root/str(i);d.mkdir();r={'match_id':str(i),'track':'current-test-pair','stage':'prematch',
                    'revision':1,'recorded_at':(now-timedelta(days=2)).isoformat(),
                    'freeze_id':str(i),'fixture':{'cohort':'SYNTHETIC_ONLY',
                    'cutoff_at':(now-timedelta(days=2,minutes=1)).isoformat(),
                    'kickoff_at':(now-timedelta(days=1)).isoformat()},
                    'payload':{k:{'matrix':self.example(),'primary':'1-1','version':'SYNTHETIC_ONLY'}
                               for k in ('current_model','test_model')}}
                r['record_sha256']=digest(r);(d/'000001.json').write_text(json.dumps(r))
            snapshots={p:p.read_bytes() for p in root.glob('*/*.json')}
            results=[{'match_id':str(i),'score':a,'score_basis':'regulation_90',
                      'observed_at':now.isoformat(),'source_url':'https://example.test/synthetic'}
                     for i,a in enumerate(([1,0],[1,1]))]
            report=evaluate(root,results)
            for g in report['reports']:
                self.assertEqual(g['changed_wrong_to_right'],1)
                self.assertEqual(g['changed_right_to_wrong'],1)
                self.assertEqual(g['net_top1_gain'],0)
            self.assertEqual(snapshots,{p:p.read_bytes() for p in snapshots})
            results.append(results[0])
            with self.assertRaises(ValueError):evaluate(root,results)


if __name__=='__main__':unittest.main()
