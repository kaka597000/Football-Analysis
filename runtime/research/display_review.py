"""Shared, opt-in display contract and retained-source settlement; no live writes.

Matrix ranks, compared_scores and other tracks are never display evidence.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from research.analysis_freeze import aware, verify
from research_program.three_arm_review import _certify

SCHEMA='WCPP-ACTUAL-DISPLAY-1'

def valid_score(value):
    return isinstance(value,str) and re.fullmatch(r'(?:0|[1-9][0-9]?)-(?:0|[1-9][0-9]?)',value) is not None

def render(first, alternatives):
    return f'第一比分：{first}；备选一：{alternatives[0]}；备选二：{alternatives[1]}'

def attach_display(payload, first=None, alternatives=(), *, abstain=False):
    """Return the exact text to display and freeze together in an existing payload."""
    out=deepcopy(payload)
    if 'displayed_scores' in out:raise ValueError('display already present; append a new real prematch version instead')
    if abstain:
        if first is not None or alternatives:raise ValueError('abstention cannot carry scores')
        display={'schema':SCHEMA,'status':'ABSTAIN','first':None,'alternatives':[], 'text':'第一比分待定；两项备选待定'}
    else:
        values=[first,*alternatives]
        if len(values)!=3 or not all(map(valid_score,values)) or len(set(values))!=3:raise ValueError('exactly three distinct ordered scores required')
        display={'schema':SCHEMA,'status':'ISSUED','first':first,'alternatives':list(alternatives),'text':render(first,alternatives)}
    out['displayed_scores']=display
    return out,display['text']

def certify_retained(provider, fixture, meta, state, *, at=None):
    """Derive scores from retained provider bytes, not caller-supplied scores."""
    at=at or datetime.now(timezone.utc);root=Path(state).resolve();p=(root/meta['path']).resolve()
    if not p.is_relative_to(root):raise ValueError('evidence outside retained root')
    raw=gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('raw checksum mismatch')
    stamp=aware(meta['captured_at'])
    if not aware(fixture['kickoff_at'])+timedelta(minutes=90)<=stamp<=at:raise ValueError('early/future result observation')
    data=json.loads(raw);u=urlsplit(meta['source_url'])
    if provider=='espn':
        if u.hostname not in ('site.api.espn.com','site.web.api.espn.com'):raise ValueError('unapproved ESPN source')
        comp=data['header']['competitions'][0];sides={c['homeAway']:c for c in comp['competitors']}
        def score(side):
            value=sides[side]['score'];return int(value['displayValue'] if isinstance(value,dict) else value)
        row={**fixture,'home_goals':score('home'),'away_goals':score('away'),'score_basis':'regulation_90','known_at':meta['captured_at'],'raw_reference':meta,'source_url':meta['source_url']}
        _certify(row,fixture,root,at)
        h,a=row['home_goals'],row['away_goals']
    elif provider=='football-data.org':
        if u.scheme!='https' or u.hostname!='api.football-data.org' or u.path!='/v4/matches/'+str(data['id']):raise ValueError('wrong football-data endpoint')
        if fixture['match_id']!='fdorg:'+str(data['id']) or fixture['home']!='fdorg:'+str(data['homeTeam']['id']) or fixture['away']!='fdorg:'+str(data['awayTeam']['id']):raise ValueError('provider identity mismatch')
        if data['competition']['code']!=fixture.get('provider_competition') or data.get('stage')!=fixture.get('provider_stage'):raise ValueError('competition/phase mismatch')
        if aware(data['utcDate'])!=aware(fixture['kickoff_at']) or data['status']!='FINISHED' or data['score']['duration']!='REGULAR':raise ValueError('not original finished regulation fixture')
        h,a=data['score']['fullTime']['home'],data['score']['fullTime']['away']
    else:raise ValueError('unsupported result adapter')
    if any(type(x)is not int or x<0 for x in (h,a)):raise ValueError('invalid goals')
    return {'match_id':fixture['match_id'],'home_goals':h,'away_goals':a,'score_basis':'regulation_90','evidence':meta,'adapter':provider}

def settle(record, fixture, track, provider=None, meta=None, state=None, *, at=None):
    """One first-issued track, with separate Top1/Top3 coverage and denominators."""
    result=certify_retained(provider,fixture,meta,state,at=at) if meta is not None else None
    row={'match_id':fixture['match_id'],'track':track,'verified_completed':result is not None,'top1_issued':False,'top3_issued':False,'top1_hit':None,'top3_hit':None,'display_status':'MISSING','result':result}
    if record is None:return row
    r=verify(record)
    if r['stage']!='prematch' or r['revision']!=1 or r['track']!=track or r['evaluation_slot']!='FIRST_PREMATCH':raise ValueError('wrong track/version/stage')
    f=r['fixture']
    if any(f[k]!=fixture[k] for k in ('match_id','cohort','stage','home','away','kickoff_at')):raise ValueError('fixture mismatch')
    if not aware(f['cutoff_at'])<=aware(r['recorded_at'])<aware(f['kickoff_at']):raise ValueError('not original prematch display')
    d=r['payload'].get('displayed_scores')
    if not isinstance(d,dict) or d.get('schema')!=SCHEMA:return row
    row['freeze_sha256']=r['record_sha256'];row['display_status']=d.get('status','MISSING')
    if d.get('status')!='ISSUED':return row
    first=d.get('first');alts=d.get('alternatives');row['top1_issued']=valid_score(first)
    row['top3_issued']=row['top1_issued'] and isinstance(alts,list) and len(alts)==2 and all(map(valid_score,alts)) and len(set([first,*alts]))==3
    # A stored rank list without its actual displayed text is not display evidence.
    text=d.get('text')
    if not isinstance(text,str) or not text.startswith(f'第一比分：{first}；'):
        row['top1_issued']=row['top3_issued']=False
    if row['top3_issued'] and text!=render(first,alts):row['top3_issued']=False
    if result:
        actual=f"{result['home_goals']}-{result['away_goals']}"
        if row['top1_issued']:row['top1_hit']=actual==first
        if row['top3_issued']:row['top3_hit']=actual in [first,*alts]
    return row

def summarize(rows):
    if len({(r['match_id'],r['track']) for r in rows})!=len(rows):raise ValueError('duplicate evaluation slot')
    if len({r['track'] for r in rows})>1:raise ValueError('tracks must be reported separately')
    n=len(rows);out={'registered_n':n,'verified_completed_n':sum(r['verified_completed'] for r in rows),'pending_n':sum(not r['verified_completed'] for r in rows),'missing_display_n':sum(r['display_status']=='MISSING' for r in rows),'abstain_n':sum(r['display_status']=='ABSTAIN' for r in rows)}
    for k in ('top1','top3'):
        issued=sum(r[k+'_issued'] for r in rows);settled=[r for r in rows if r[k+'_hit'] is not None];hits=sum(r[k+'_hit'] for r in settled)
        out[k]={'issued_n':issued,'coverage':issued/n if n else None,'settled_n':len(settled),'hits':hits if settled else None,'accuracy':hits/len(settled) if settled else None}
    return out
