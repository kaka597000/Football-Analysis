import copy
from datetime import date
from pathlib import Path
import sys
import unittest
import numpy as np

from research_program import score_form as sf, live_score_form as live
from research.analysis_freeze import digest
from research import score_distribution_audit
from research_program.selection_review import compare

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from train_score_form import examples


class ScoreFormTests(unittest.TestCase):
    def setUp(self):
        self.fixture={'match_id':'espn:1','cohort':'eng.1','stage':'regular','score_basis':'regulation_90',
                      'neutral':False,'home':'espn:h','away':'espn:a','kickoff_at':'2026-04-20T12:00:00Z',
                      'cutoff_at':'2026-04-20T10:00:00Z'}
        self.game={'id':'p','cohort':'eng.1','score_basis':'regulation_90','kickoff_at':'2026-04-16T12:00:00Z',
                   'observed_at':'2026-04-19T18:00:00Z','gf':2,'ga':0,'xgf':1.4,'xga':0.2,
                   'source_sha256':'a'*64,'source_url':'https://example.org/match/p'}
        self.snap={'match_id':'espn:1','cohort':'eng.1','home':{'team_id':'espn:h','matches':[self.game]},
                   'away':{'team_id':'espn:a','matches':[{**self.game,'id':'other','gf':0,'ga':1}]}}

    def test_partial_and_missing_genuine_xg(self):
        self.snap['home']['matches'][0].update(xgf=None,xga=None)
        x=live.side_form(self.fixture,self.snap,'home')
        self.assertEqual((x['n'],x['xg_n'],x['gap']),(1,0,4))
        self.assertAlmostEqual(x['gf'],(2+4*1.3)/5)
        self.assertEqual(x['gf'],x['xgf'])
        self.assertIsNone(x['raw_xgf'])

    def test_future_data_identity_and_orientation(self):
        for key,val in [('observed_at','2026-04-20T10:00:01Z'),('kickoff_at','2026-04-20T11:00:00Z'),
                        ('cohort','fra.1'),('score_basis','extra_time'),('source_sha256',''),('xgf',float('nan'))]:
            snap=copy.deepcopy(self.snap);snap['home']['matches'][0][key]=val
            with self.subTest(key=key),self.assertRaises(ValueError):live.side_form(self.fixture,snap,'home')
        self.snap['home']['matches'][0]['xg_observed_at']='2026-04-20T15:00:00Z'
        with self.assertRaises(ValueError):live.side_form(self.fixture,self.snap,'home')

    def test_training_excludes_same_day_and_target(self):
        def match(i,day,hg):return {'id':i,'date':day,'home':'a','away':str(i),'goals':[hg,0],'xg':[2.,1.]}
        items=examples([match(1,'2024-01-01',6),match(2,'2024-01-01',0),match(3,'2024-01-08',0)])
        np.testing.assert_array_equal(items[0][1],items[1][1])
        self.assertAlmostEqual(items[2][1][0,1],np.log((6+4*1.3)/6))

    def test_score_categories_keep_zero_and_high_cells(self):
        m=sf.poisson_matrix([2.5,1.1],(21,21))
        np.testing.assert_allclose(sf.calibrate(m,[1]*25,1),m)
        self.assertEqual(sf.cell_index(m.shape)[5,1],sf.cell_index(m.shape)[11,1])
        highlighted=np.zeros_like(m); highlighted[0,0]=.45;highlighted[4,0]=.35;highlighted[2,1]=.2
        pool,s=sf.selection_inputs(highlighted,highlighted)
        self.assertEqual(s['pooled_top3'],['0-0','4-0','2-1'])
        self.assertEqual(sf.select_scores(s,'2-1')['primary'],'0-0')
        self.assertAlmostEqual(pool.sum(),1)

    def test_overrides_and_set_preservation(self):
        candidate={'pooled_top3':['0-0','1-1','1-0'],'expert_primaries':['0-0','2-1']}
        supported=sf.select_scores(candidate,'2-1')
        self.assertEqual([supported['primary'],*supported['alternates']],['2-1','0-0','1-1'])
        original=['2-1','1-1','2-0']
        self.assertEqual(sf.rerank_issued(original,'0-0'),original)
        self.assertEqual(sf.rerank_issued(original,'1-1'),['1-1','2-1','2-0'])
        self.assertEqual(original,['2-1','1-1','2-0'])
        self.assertIsNone(sf.rerank_issued(['0-0','1-1'],'0-0'))

    def test_invalid_input_leaves_native_outputs_available(self):
        out={'test_model':{'version':'unsupported'}}
        live.attach_candidate(out,self.fixture,{},self.snap)
        self.assertEqual(out['score_form_candidate']['status'],'UNSUPPORTED_TEST_ROUTE')
        self.assertEqual(out['test_model'],{'version':'unsupported'})

    def test_selection_audit_and_review_with_pending_result(self):
        # Comparison here only needs an authenticated pair and its numerical distributions.
        matrix=[[.30,.12,.05],[.14,.20,.04],[.08,.04,.03]]
        model={'matrix':matrix,'matrix_hash':digest(matrix),'version':'fixture-engine','primary':'0-0',
               'wdl':{'主胜':.3,'平局':.4,'客胜':.3}}
        candidate={'scope':'PREMATCH_RESEARCH','version':sf.VERSION,'status':'AVAILABLE','snapshot_sha256':'a'*64,
                   'pooled_top3':['0-0','1-1','1-0'],'expert_primaries':['0-0','1-1']}
        pair={'current_model':model,'test_model':model,'score_form_candidate':candidate}
        proposal={'primary':'2-1','alternates':['1-1','0-0']}
        audit=score_distribution_audit.comprehensive_audit(proposal,pair)
        self.assertEqual(audit['score_form_comparison']['within_issued_three'],['0-0','2-1','1-1'])
        def record(track,payload,when,**extra):
            rec={'track':track,'stage':'prematch','evaluation_slot':'FIRST_PREMATCH','freeze_id':track,
                 'revision':1,'fixture':self.fixture,'recorded_at':when,'payload':payload,**extra}
            rec['record_sha256']=digest(rec)
            return rec
        pr=record('current-test-pair',pair,'2026-04-20T10:00:01Z')
        comp=record('comprehensive',{**proposal,'current_test_reference':{'sha256':pr['record_sha256'],'freeze_id':pr['freeze_id']}},
                    '2026-04-20T10:00:02Z',selection_audit=audit)
        entries=[{'pair':pr,'comprehensive':comp}]
        self.assertEqual(compare(entries,[])['score_form_review']['rerank_settled'],0)
        res={**self.fixture,'status':'FT','home_goals':0,'away_goals':0,'observed_at':'2026-04-20T15:00:00Z','source_url':'https://example.org/final'}
        review=compare(entries,[res])['score_form_review']
        self.assertEqual((review['rerank_settled'],review['first_gained'],review['first_lost'],review['three_hits']),(1,1,0,1))
