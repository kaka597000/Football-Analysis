import copy
from datetime import date,timedelta
import unittest
import numpy as np
from research_program.walk_forward import splits,predict_block


class WalkForwardTests(unittest.TestCase):
    def rows(self):
        return [{'match_id':str(i),'cohort':'eng.1','stage':'regular',
                 'date':(date(2020,1,1)+timedelta(days=i)).isoformat(),
                 'home':'A','away':'B','home_goals':i%3,'away_goals':i%2,
                 'score_basis':'regulation_90','neutral':False,
                 'source_url':'https://example.test/synthetic',
                 'observed_at':'2026-01-01T00:00:00Z'} for i in range(150)]

    def test_target_labels_never_fit_or_change_prediction(self):
        rows=self.rows();prior=rows[:90];targets=rows[91:95]
        original=copy.deepcopy(prior);a=predict_block(prior,targets)
        changed=copy.deepcopy(targets)
        for r in changed:r['home_goals']=9;r['away_goals']=8
        b=predict_block(prior,changed)
        self.assertEqual(prior,original)
        for x,y in zip(a,b):
            for name in x[1]:np.testing.assert_array_equal(x[1][name],y[1][name])
        with self.assertRaises(ValueError):predict_block(prior+[targets[0]],targets)

    def test_month_boundaries_exclude_same_month_and_future(self):
        for origin,prior,targets in splits(self.rows()):
            self.assertTrue(all(r['date']<origin for r in prior))
            self.assertTrue(all(r['date'][:7]==origin[:7] for r in targets))
        rows=self.rows();duplicate=dict(rows[0],match_id='alias');rows.append(duplicate)
        with self.assertRaises(ValueError):list(splits(rows))


if __name__=='__main__':unittest.main()
