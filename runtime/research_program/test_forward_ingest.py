import json,hashlib,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from research_program.ingest_forward_history import normalize
class ForwardIngestTests(unittest.TestCase):
 def test_source_scores_venue_date_and_capture_are_bound(self):
  now=datetime.now(timezone.utc);kickoff=now-timedelta(days=2)
  raw={'format':{'regulation':{'periods':2}},'header':{'id':'1','league':{'slug':'eng.1','isTournament':False},'season':{'year':2026,'name':'2026-27'},'competitions':[{'date':kickoff.isoformat(),'neutralSite':False,'status':{'type':{'completed':True,'name':'STATUS_FULL_TIME'}},'competitors':[{'homeAway':'home','team':{'id':'A'},'score':'1','linescores':[{'displayValue':'0'},{'displayValue':'1'}]},{'homeAway':'away','team':{'id':'B'},'score':'0','linescores':[{'displayValue':'0'},{'displayValue':'0'}]}]}]}}
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'source.json';path.write_text(json.dumps(raw),encoding='utf-8')
   r={'match_id':'espn:1','cohort':'eng.1','stage':'regular','stage_basis':'SYNTHETIC reviewed regular identity','source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary?event=1','score_evidence':'source.json','score_evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'neutral_site':False,'date':kickoff.date().isoformat(),'observed_at':now.isoformat(),'query_time':now.isoformat(),'first_known_at':None,'identity_verified':True,'home':'espn:A','away':'espn:B','home_score':1,'away_score':0}
   result=normalize([r],d);self.assertEqual(result[0]['home_goals'],1);self.assertIsNone(result[0]['first_known_at']);self.assertEqual(result[0]['season'],'espn:eng.1:2026')
   r['neutral_site']=True
   with self.assertRaises(ValueError):normalize([r],d)
   r['neutral_site']=False;r['home_score']=2
   with self.assertRaises(ValueError):normalize([r],d)
   r['home_score']=1;raw['header']['league']['slug']='aus.1'
   path.write_text(json.dumps(raw),encoding='utf-8');r['score_evidence_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
   with self.assertRaisesRegex(ValueError,'league identity'):normalize([r],d)
if __name__=='__main__':unittest.main()
