"""Read-only canonical reconciliation; explicit venue and 90-minute scores required."""
import copy
import csv
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'database'))
from registry import normalize_name


def export(registry_path, db_path, out, xg_root=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    registry=json.loads(Path(registry_path).read_text())
    c=sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    redirects=dict(c.execute('select old_id,new_id from team_redirects'))
    def resolve(t):
        seen=set()
        while t in redirects:
            if t in seen:raise ValueError('cyclic team redirects')
            seen.add(t);t=redirects[t]
        return t
    aliases={}
    for scope,norm,tid in c.execute('select scope,norm,team_id from aliases'):
        aliases[(scope,norm)]=resolve(tid)
    def team(name):return aliases.get(('ENG',normalize_name(name)))
    xg_index={};xg_conflicts=set();now=datetime.now(timezone.utc).isoformat()
    # All these values are retrospective observations, never original first-known times.
    if xg_root:
        synonyms={'Man Utd':'Man United','Spurs':'Tottenham',"Nott'm Forest":'Nottm Forest'}
        for p in sorted(Path(xg_root).glob('*/results.csv')):
            for raw in csv.DictReader(p.open()):
                h=team(synonyms.get(raw['home_team'],raw['home_team']));a=team(synonyms.get(raw['away_team'],raw['away_team']))
                if not h or not a:continue
                key=('eng.1',raw['date'],h,a,int(raw['home_score']),int(raw['away_score']))
                v=(float(raw['home_xg']),float(raw['away_xg']))
                if not all(math.isfinite(x) and 0<=x<=30 for x in v):continue
                if key in xg_index and xg_index[key][0]!=v:xg_conflicts.add(key)
                xg_index[key]=(v,str(p))
    all_summary=[]
    for cohort,entry in registry['models'].items():
        code=cohort.split(':')[0]
        artifact=json.loads(Path(entry.get('test_artifact_path',entry['artifact_path'])).read_text())
        # Preserve the original test universe and IDs; only extend earlier history.
        seed=copy.deepcopy(artifact['history']);first=min(r['date'] for r in seed)
        original={(r['date'],resolve(r['home']),resolve(r['away'])):r for r in seed}
        match_rows=c.execute('select * from matches where competition=? and training_eligible=1 and result_status!=? order by date,match_id',(code,'conflict')).fetchall()
        audit={'cohort':cohort,'original_rows':len(seed),'added_prior_rows':0,'neutral_unknown_excluded':0,
               'phase_unknown_excluded':0,'reconciled_stats_rows':0,'xg_rows':0,'score_conflicts':0}
        for m in match_rows:
            key=(m['date'],resolve(m['home_id']),resolve(m['away_id']))
            existing=original.get(key)
            if existing is None and m['date']>=first:continue
            if existing and (existing['home_goals'],existing['away_goals'])!=(m['h90'],m['a90']):
                audit['score_conflicts']+=1;continue
            observations=c.execute('select o.*,s.url from match_evidence me join observations o on o.obs_id=me.obs_id join sources s on s.source_id=o.source_id where me.match_id=? order by o.observed_at,o.obs_id',(m['match_id'],)).fetchall()
            explicit=[];fd=[]
            for o in observations:
                if o['score_scope']!='regulation_90' or (o['h90'],o['a90'])!=(m['h90'],m['a90']):continue
                if o['date']!=m['date'] or (resolve(o['home_id']),resolve(o['away_id']))!=(key[1],key[2]):continue
                raw=json.loads(o['raw_json'])
                neutral=raw.get('neutral')
                if type(neutral) is not bool and isinstance(raw.get('competition'),dict):
                    neutral=raw['competition'].get('neutralSite')
                if type(neutral) is bool:explicit.append((o,raw,neutral))
                if o['provider']=='football-data' and o['url'] and o['url'].startswith('https://www.football-data.co.uk/'):
                    fd.append((o,raw))
            if existing is None:
                if not explicit:audit['neutral_unknown_excluded']+=1;continue
                if m['stage'] not in ('regular','regular-season'):audit['phase_unknown_excluded']+=1;continue
                o,raw,neutral=explicit[0]
                url=raw.get('source_url') or o['url']
                if not url or not url.startswith('https://'):continue
                existing={'match_id':m['match_id'],'cohort':cohort,'date':m['date'],
                          'stage':m['stage'],
                          'home':resolve(m['home_id']),'away':resolve(m['away_id']),'home_goals':m['h90'],'away_goals':m['a90'],
                          'neutral':neutral,'score_basis':'regulation_90','source_url':url,
                          'observed_at':o['observed_at'],'first_known_at':None,
                          'venue_evidence':{'source':'canonical_explicit_observation','obs_id':o['obs_id']}}
                seed.append(existing);original[key]=existing;audit['added_prior_rows']+=1
            # FD shots are not the same definition as 5Dollar shots_off_target.
            # Preserve full-match shots separately; do not manufacture possession.
            if fd:
                o,raw=fd[-1]
                for keys,label in [(('HST','AST'),'shots_on_target'),(('HS','AS'),'shots_total')]:
                    try:values=[float(raw[k]) for k in keys]
                    except (KeyError,ValueError,TypeError):continue
                    if all(math.isfinite(v) and 0<=v<=100 for v in values):
                        existing.setdefault('additional_process',{})[label]=values
                if existing.get('additional_process'):
                    existing['additional_process_evidence']={'source_url':o['url'],'query_time':o['observed_at'],'obs_id':o['obs_id']}
                    existing['observed_at']=max(existing['observed_at'],o['observed_at'])
                try:values=[float(raw[k]) for k in ('HxG','AxG')]
                except (KeyError,ValueError,TypeError):values=[]
                if len(values)==2 and all(math.isfinite(v) and 0<=v<=30 for v in values):
                    existing.update(home_xg=values[0],away_xg=values[1],
                                    xg_evidence={'source_url':o['url'],'query_time':o['observed_at'],'obs_id':o['obs_id'],'origin':'football-data.co.uk HxG/AxG'})
                    existing['observed_at']=max(existing['observed_at'],o['observed_at'])
            if not existing.get('xg_evidence'):
                xkey=(code,existing['date'],resolve(existing['home']),resolve(existing['away']),existing['home_goals'],existing['away_goals'])
                if xkey in xg_index and xkey not in xg_conflicts:
                    values,p=xg_index[xkey]
                    existing.update(home_xg=values[0],away_xg=values[1],observed_at=max(existing['observed_at'],now),
                                    xg_evidence={'source_url':'https://github.com/callumfm/dixon-coles/tree/dea06c6e4b38085d6a4b84186cb9ead321a73ce4/data/premier-league',
                                                 'query_time':now,'origin':'FPL/Understat historical research snapshot; retrospective crosschecked score identity',
                                                 'source_path':p,'mixed_provider_scale':True,'first_known_at':None})
        seed.sort(key=lambda r:(r['date'],r['match_id']))
        audit['total_rows']=len(seed)
        audit['xg_rows']=sum(bool(r.get('xg_evidence')) for r in seed)
        audit['reconciled_stats_rows']=sum(bool(r.get('additional_process')) for r in seed)
        path=out/(cohort.replace(':','-')+'-history.json');path.write_text(json.dumps(seed,ensure_ascii=False,indent=2,allow_nan=False))
        audit['history_path']=str(path);audit['history_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        all_summary.append(audit)
    (out/'data-audit.json').write_text(json.dumps(all_summary,ensure_ascii=False,indent=2))
    return all_summary
