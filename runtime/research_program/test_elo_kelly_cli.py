"""Real evidence validators and CLI, with synthetic sources and no network."""
import copy
from datetime import datetime,timedelta,timezone
import hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
RUNTIME=Path(__file__).resolve().parents[1]

class EvidenceCliTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  now=datetime.now(timezone.utc);cutoff=now-timedelta(minutes=1);historic=now-timedelta(days=3);captured=historic+timedelta(hours=3)
  target={'match_id':'SYNTHETIC_TARGET','cohort':'eng.1','competition':'eng.1','stage':'regular','home':'espn:1','away':'espn:2','neutral':False,'cutoff_at':cutoff.isoformat(),'kickoff_at':(now+timedelta(days=1)).isoformat()}
  raw={'header':{'id':'123','league':{'slug':'eng.1'},'competitions':[{'date':historic.isoformat(),'neutralSite':False,'status':{'period':2,'type':{'completed':True,'name':'STATUS_FULL_TIME'}},'competitors':[{'homeAway':'home','team':{'id':'1'},'score':'2'},{'homeAway':'away','team':{'id':'2'},'score':'0'}]}]}}
  label={'match_id':'espn:123','cohort':'eng.1','stage':'regular','home':'espn:1','away':'espn:2','neutral':False,'kickoff_at':historic.isoformat(),'observed_at':captured.isoformat(),'event_status':'FT','score_basis':'regulation_90','home_goals':2,'away_goals':0,'evidence':self.evidence('label.json',raw,captured.isoformat(),'https://site.api.espn.com/synthetic')}
  observations=[]
  for book,odds in [('TARGET',[2,3.5,4]),('P1',[2.1,3.4,3.9]),('P2',[2.2,3.3,3.8])]:
   quoted=(cutoff-timedelta(seconds=30)).isoformat();observed=(cutoff-timedelta(seconds=10)).isoformat()
   value={'bookmaker':book,'market':'h2h','quoted_at':quoted,'odds':dict(zip(('home','draw','away'),odds))}
   observations.append({'provider':'SYNTHETIC','cohort':'eng.1','stage':'regular','match_id':target['match_id'],'entity_id':'market','field':'market_quote','period':'regulation_90','event_kickoff_at':target['kickoff_at'],'observed_at':observed,'value':value,'evidence':self.evidence(book+'.json',{'synthetic':True,'value':value},observed,'https://example.test/'+book)})
  self.request={'target':target,'history':[label],'observations':observations,'elo_config':{'initial_rating':1500.,'k_factor':20.,'scale':400.,'home_advantage':0.},'kelly_config':{'provider':'SYNTHETIC','bookmaker':'TARGET','reference_bookmakers':['P1','P2'],'max_age_seconds':60}}
 def evidence(self,name,body,observed,url):
  p=self.root/name;p.write_text(json.dumps(body),encoding='utf-8')
  return {'url':url,'file':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'query_time':observed,'identity_verified':True}
 def execute(self,request=None):
  inp=self.root/'request.json';inp.write_text(json.dumps(request or self.request),encoding='utf-8')
  return subprocess.run([sys.executable,'-m','research_program.elo_kelly_shadow','--request',str(inp),'--evidence-root',str(self.root),'--out',str(self.root/'result.json')],cwd=self.root,env=dict(os.environ,PYTHONPATH=str(RUNTIME),OPENBLAS_NUM_THREADS='1'),capture_output=True,text=True,timeout=30)
 def output(self):return json.loads((self.root/'result.json').read_text(encoding='utf-8'))
 def test_valid_sources_produce_features_not_trained_predictions(self):
  result=self.execute();self.assertEqual(result.returncode,0,result.stderr);out=self.output()
  self.assertEqual(out['elo']['history_n'],1);self.assertEqual(out['elo']['home_rating'],1510.)
  self.assertEqual(out['kelly']['status'],'RESEARCH_INDEX_AVAILABLE');self.assertFalse(out['score_prediction_generated']);self.assertFalse(out['trained_fusion'])
 def test_missing_market_preserves_elo(self):
  req=copy.deepcopy(self.request);req['observations']=[];result=self.execute(req);self.assertEqual(result.returncode,0,result.stderr)
  out=self.output();self.assertEqual(out['elo']['history_n'],1);self.assertIsNone(out['kelly']['indices'])
 def test_tampered_raw_cannot_produce_report(self):
  (self.root/'label.json').write_text('{}',encoding='utf-8');self.assertNotEqual(self.execute().returncode,0);self.assertFalse((self.root/'result.json').exists())
 def test_late_capture_cannot_be_backdated(self):
  req=copy.deepcopy(self.request);t=(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat()
  req['observations'][-1]['observed_at']=req['observations'][-1]['evidence']['query_time']=t
  result=self.execute(req);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(self.output()['kelly']['missing_books'],['P2'])
 def test_old_output_is_not_overwritten(self):
  out=self.root/'result.json';out.write_text('ORIGINAL',encoding='utf-8');self.assertNotEqual(self.execute().returncode,0);self.assertEqual(out.read_text(encoding='utf-8'),'ORIGINAL')

if __name__=='__main__':unittest.main()
