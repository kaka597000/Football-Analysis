import copy,unittest
from datetime import date,timedelta
import numpy as np
from ensemble import train,mix

class Tests(unittest.TestCase):
 def rows(self):
  return [dict(match_id=str(i),cohort='TEST_ONLY',date=(date(2020,1,1)+timedelta(days=i)).isoformat(),
    base_fit_ends=['2019-01-01']*2,matrices=[[[.4,.1],[.2,.3]],[[.1,.2],[.3,.4]]],
    home_goals=i%2,away_goals=(i//2)%2) for i in range(120)]
 def test_future_diagnostic_labels_do_not_fit_gate_or_temperature(self):
  rows=self.rows();a=train(rows);changed=copy.deepcopy(rows)
  for r in changed:
   if r['date']>=a['split']['temperature_fit_before']:r['home_goals']=1-r['home_goals']
  b=train(changed)
  self.assertEqual(a['beta'],b['beta']);self.assertEqual(a['temperature'],b['temperature'])
 def test_normalization_and_invalid_weights(self):
  m=mix([[[.5,.5]],[[.8,.2]]],[.3,.7],1.2)
  self.assertAlmostEqual(m.sum(),1.)
  with self.assertRaises(ValueError):mix([[[1.]]],[float('nan')])
 def test_leakage_and_mixed_leagues_and_duplicates_rejected(self):
  for mode in ('fit','cohort','duplicate'):
   r=self.rows()
   if mode=='fit':r[-1]['base_fit_ends'][0]=r[-1]['date']
   elif mode=='cohort':r[-1]['cohort']='OTHER'
   else:r[-1]['match_id']=r[0]['match_id']
   with self.assertRaises(ValueError):train(r)
if __name__=='__main__':unittest.main()
