import copy
import unittest
from datetime import datetime,timedelta
import numpy as np
import continued_model as m


class Tests(unittest.TestCase):
    def rows(self,n=200):
        return [{'match_id':str(i),'cohort':'eng.1','home':'A','away':'B',
                 'date':(datetime(2020,1,1)+timedelta(days=i)).date().isoformat(),
                 'home_goals':i%3,'away_goals':i%2,'neutral':False,
                 'score_basis':'regulation_90','source_url':'https://example.test/software-only',
                 'observed_at':'2025-01-01T00:00:00Z'} for i in range(n)]

    def test_partial_shots_without_possession_can_enter_features(self):
        rows=self.rows(90);r=rows[74]
        r['additional_process']={'shots_on_target':[4.,2.],'shots_total':[12.,6.]}
        r['additional_process_evidence']={'source_url':'https://example.test','query_time':r['observed_at']}
        f=m.feature_matrix(rows,rows[75],(1.4,1.))
        self.assertEqual(f[0,m.FEATURES.index('own_recent_shots_on_target')],4.)
        self.assertEqual(f[0,m.FEATURES.index('own_shots_total')],12.)
        self.assertAlmostEqual(f[0,m.FEATURES.index('own_shots_total_coverage')],1/6)
        self.assertTrue(np.isnan(f[0,m.FEATURES.index('own_recent_possession')]))

    def test_target_and_future_partial_process_never_enter_features(self):
        rows=self.rows(90);target=rows[75]
        before=m.feature_matrix(rows,target,(1.4,1.))
        for r in rows[75:]:
            r.update(home_goals=25,additional_process={'shots_total':[99.,99.]},
                     additional_process_evidence={'source_url':'https://example.test','query_time':r['observed_at']})
        np.testing.assert_equal(before,m.feature_matrix(rows,target,(1.4,1.)))

    def test_test_labels_do_not_select_parameters_or_blend(self):
        # Real optimizer on a synthetic software fixture, never benchmark evidence.
        rows=self.rows();initial,selection,test=m.previous.chronological_split(rows)
        stats=m.legacy.score_matrices([(r,m.legacy.matrix(1.4,1.),{'unseen_teams':[]}) for r in test])
        old={'test':{'current_baseline':stats,'ml':stats}}
        a,ra=m.train(rows,rows,old)
        changed=copy.deepcopy(rows);start=test[0]['date']
        for r in changed:
            if r['date']>=start:r['home_goals']=(r['home_goals']+1)%3
        b,rb=m.train(changed,changed,old)
        self.assertEqual(a['params'],b['params']);self.assertEqual(a['alpha'],b['alpha'])
        self.assertEqual(ra['selection_candidates'],rb['selection_candidates'])
        for fold in ra['folds']['fit']+ra['folds']['selection']+ra['folds']['test']:
            self.assertLess(fold['fit_end'],fold['first_target'])

    def test_wrong_artifact_version_is_rejected(self):
        with self.assertRaises(ValueError):m.predict({'version':'wrong'},{},{})


if __name__=='__main__':unittest.main()
