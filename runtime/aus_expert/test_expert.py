import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import runner as r
class ExpertTests(unittest.TestCase):
 def test_dc_probabilities_global_clamped_rates(self):
  for h in (.08,1.5,8.):
   for a in (.08,1.2,8.):
    for rho in (-.03,0,.015):
     m=r.dc_matrix([h,a],rho);self.assertTrue(np.isfinite(m).all());self.assertGreaterEqual(m.min(),0);self.assertAlmostEqual(m.sum(),1.,12)
 def test_feature_origin_excludes_same_day_and_future(self):
  fs=[dict(match_id=str(i),cohort='aus.1',competition='aus.1',date='2020-01-%02d'%i,season='2019/20',home='H',away='A',stage='regular',neutral=None) for i in range(1,10)];ys={x['match_id']:dict(home_goals=1,away_goals=0) for x in fs};target=fs[6];h=r.History(fs,ys,[],7);before,proof=h.vector(target,'2020-01-05');ys2=copy.deepcopy(ys)
  for key in ('5','6','7','8','9'):ys2[key]['home_goals']=99
  after,_=r.History(fs,ys2,[],7).vector(target,'2020-01-05');np.testing.assert_equal(before,after);self.assertEqual(proof['historical_match_ids'],['1','2','3','4'])
 @unittest.skipUnless(r.DB.is_file(), 'external Australian historical database required')
 def test_append_only_import_rollback_and_legal_repeat(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td);db=t/'canonical.sqlite';c=sqlite3.connect(db);src=r.dbconnect(readonly=True)
   for name in ('competitions','sources','teams','observations','matches','match_evidence','provider_team_ids','quarantine'):
    c.execute(src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()[0])
   src.close();c.execute("INSERT INTO competitions VALUES('aus.1','A-League','AUS','cross-year')");c.commit();c.close();raw=t/'raw.json';raw.write_text('{}');base=dict(competition='aus.1',gender='men',source_match_id='SYNTHETIC1',date='2010-01-01',kickoff_utc='2010-01-01T00:00Z',season_label='2009/10',home_provider_team_id='H',away_provider_team_id='A',home_team='Synthetic H',away_team='Synthetic A',stage='regular',status='played',training_eligible=True,score_scope='90_minutes',home_goals_90=1,away_goals_90=0,raw_path=str(raw),source_body_sha256=r.file_hash(raw),source_url='https://example.invalid/synthetic',observed_at_utc='2026-10-16T00:00:00Z');second={**base,'source_match_id':'SYNTHETIC2','date':'2010-02-01'};inp=t/'input.jsonl';inp.write_text('\n'.join(json.dumps(x) for x in (base,second)));report=t/'report';report.mkdir();original=r.dbconnect
   with patch.object(r,'REPORT',report),patch.object(r,'DB',db),patch.object(r,'dbconnect',lambda *args,**kw:original(db,**kw)):
    value=r.import_rows(inp);self.assertEqual(value['counts']['new_matches'],2)
    (report/'import-audit.json').unlink();conflict={**base,'home_goals_90':5};inp.write_text(json.dumps(conflict));value=r.import_rows(inp);self.assertEqual(value['counts']['EXISTING_ACCEPTED_SCORE_CONFLICT'],1)
    (report/'import-audit.json').unlink();inp.write_text(json.dumps(base)+'\n'+json.dumps(base))
    with self.assertRaises(AssertionError):r.import_rows(inp)
   c=sqlite3.connect(db);self.assertEqual(c.execute('SELECT count(*) FROM matches').fetchone()[0],2);self.assertEqual(c.execute('SELECT h90 FROM matches ORDER BY date LIMIT 1').fetchone()[0],1);self.assertEqual(c.execute('SELECT count(*) FROM quarantine').fetchone()[0],1);self.assertFalse(list(c.execute('PRAGMA foreign_key_check')))
if __name__=='__main__':unittest.main()
