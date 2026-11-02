"""Save verified pre-match evidence, supplied by the data collector, without secrets."""
import json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlparse,parse_qsl

def aware(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if not d.tzinfo:raise ValueError('timezone required')
 return d

def record(snapshot,root):
 required=['match_id','cohort','sex','age_band','competition_type','phase','kickoff_at','captured_at','sources','facts','missing_fields']
 if any(k not in snapshot for k in required):raise ValueError('explicit identity, scope, facts, missingness and evidence required')
 if snapshot['sex'] not in ('male','female') or not snapshot['age_band'] or not snapshot['phase']:raise ValueError('known competition scope required')
 now=datetime.now(timezone.utc);captured=aware(snapshot['captured_at']);kickoff=aware(snapshot['kickoff_at'])
 if not captured<=now<kickoff:raise ValueError('actual pre-match capture only')
 for s in snapshot['sources']:
  u=urlparse(s['url'])
  if u.scheme!='https' or not u.netloc or u.username or u.password:raise ValueError('public HTTPS evidence required')
  if any(k.lower() in ('key','api_key','apikey','api-key','token','password') for k,v in parse_qsl(u.query)):raise ValueError('secret-bearing URL prohibited')
  if aware(s['query_time'])>captured or s.get('published_at') and aware(s['published_at'])>captured:raise ValueError('future evidence')
 # Prevent accidental credential records. Collector must supply sanitized public football facts.
 text=json.dumps(snapshot,sort_keys=True,ensure_ascii=False,allow_nan=False)
 if any('"'+k+'"' in text.lower() for k in ('authorization','api_key','apikey','password','access_token')):raise ValueError('credential fields prohibited')
 identity=hashlib.sha256((snapshot['match_id']+'|'+snapshot['captured_at']).encode()).hexdigest();out=Path(root);out.mkdir(parents=True,exist_ok=True)
 value={'scope':'PREMATCH_EVIDENCE_ONLY_NOT_A_FORECAST','snapshot_sha256':hashlib.sha256(text.encode()).hexdigest(),'snapshot':snapshot}
 with (out/(identity+'.json')).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
 return str(out/(identity+'.json'))
