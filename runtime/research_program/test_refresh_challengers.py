import copy,unittest
from research_program.refresh_challengers import merge_rows
class MergeTests(unittest.TestCase):
 def row(self,mid='old'):
  return {'match_id':mid,'cohort':'eng.1','date':'2026-01-01','home':'A','away':'B','home_goals':1,'away_goals':0,'neutral':False,'observed_at':'2026-01-02T00:00:00Z','existing_xg':1.2,'evidence':{'file':'synthetic'},'season':None}
 def test_provider_alias_duplicate_preserves_old_features_and_source(self):
  old=self.row();before=copy.deepcopy(old);new=self.row('espn:new');new.update(observed_at='2026-02-01T00:00:00Z',season='source:2026',season_evidence={'source':'synthetic'})
  merged,audit=merge_rows([old],[new]);self.assertEqual(old,before);self.assertEqual(len(merged),1);self.assertEqual(merged[0]['existing_xg'],1.2);self.assertEqual(audit['duplicate_provider_rows_merged'],1);self.assertEqual(merged[0]['match_id'],'old')
 def test_conflicting_scores_and_near_day_duplicates_are_blocked(self):
  for field,value in [('home_goals',2),('date','2026-01-02')]:
   row=self.row('other');row[field]=value
   with self.assertRaises(ValueError):merge_rows([self.row()],[row])
if __name__=='__main__':unittest.main()
