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
from automation.collector import regulation_row, PHASED_RESEARCH

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

def _retained_json(meta, root):
    path=(root/meta['path']).resolve()
    if not path.is_relative_to(root):raise ValueError('evidence outside retained root')
    raw=gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('raw checksum mismatch')
    return json.loads(raw)


def _score_pair(value):
    if not isinstance(value,dict):raise ValueError('explicit regulation score required')
    scores=[]
    for side in ('home','away'):
        values=[value[k] for k in (side,side+'Team') if k in value]
        if not values or any(type(v)is not int or v<0 for v in values) or len(set(values))!=1:
            raise ValueError('missing, invalid or conflicting regulation score')
        scores.append(values[0])
    return tuple(scores)


def certify_retained(provider, fixture, meta, state, *, at=None):
    """Derive scores from retained provider bytes, not caller-supplied scores."""
    at=at or datetime.now(timezone.utc);root=Path(state).resolve()
    data=_retained_json(meta,root)
    stamp=aware(meta['captured_at'])
    if not aware(fixture['kickoff_at'])+timedelta(minutes=90)<=stamp<=at:raise ValueError('early/future result observation')
    u=urlsplit(meta['source_url'])
    if provider=='espn':
        if u.hostname not in ('site.api.espn.com','site.web.api.espn.com'):raise ValueError('unapproved ESPN source')
        comp=data['header']['competitions'][0]
        scope={'competition':fixture['cohort'].split(':')[0],'cohort':fixture['cohort'],'stages':[fixture['stage']]}
        event={**data['header'],'date':comp['date']}
        board_meta=meta.get('scoreboard_reference')
        if board_meta is not None:
            board=_retained_json(board_meta,root);url=urlsplit(board_meta['source_url'])
            if (url.scheme!='https' or url.hostname not in ('site.api.espn.com','site.web.api.espn.com')
                    or url.path!='/apis/site/v2/sports/soccer/'+scope['competition']+'/scoreboard'
                    or aware(board_meta['captured_at'])>at):raise ValueError('scoreboard source/time mismatch')
            if [x.get('slug') for x in board.get('leagues',[])]!=[scope['competition']]:raise ValueError('scoreboard league mismatch')
            matches=[e for e in board.get('events',[]) if str(e.get('id'))==fixture['match_id'].removeprefix('espn:')]
            if len(matches)!=1 or len(matches[0].get('competitions',[]))!=1:raise ValueError('scoreboard event mismatch')
            event=matches[0]
            if (not event.get('date') or aware(event['date'])!=aware(fixture['kickoff_at'])
                    or aware(event['competitions'][0].get('date',event['date']))!=aware(fixture['kickoff_at'])):raise ValueError('scoreboard kickoff mismatch')
            native=event.get('season',{});header=data['header'].get('season',{})
            for key in ('slug','year','type'):
                if native.get(key) is not None and header.get(key) is not None and native[key]!=header[key]:
                    raise ValueError('scoreboard/summary season conflict')
            if scope['competition'] in PHASED_RESEARCH and native.get('slug')!=fixture['stage']:
                raise ValueError('missing or conflicting native phase')
        row=regulation_row(scope,event,data,meta['captured_at'],meta)
        _certify(row,fixture,root,at)
        h,a=row['home_goals'],row['away_goals']
    elif provider=='football-data.org':
        if u.scheme!='https' or u.hostname!='api.football-data.org' or u.path!='/v4/matches/'+str(data['id']):raise ValueError('wrong football-data endpoint')
        if fixture['match_id']!='fdorg:'+str(data['id']) or fixture['home']!='fdorg:'+str(data['homeTeam']['id']) or fixture['away']!='fdorg:'+str(data['awayTeam']['id']):raise ValueError('provider identity mismatch')
        if data['competition']['code']!=fixture.get('provider_competition') or data.get('stage')!=fixture.get('provider_stage'):raise ValueError('competition/phase mismatch')
        if aware(data['utcDate'])!=aware(fixture['kickoff_at']) or data['status']!='FINISHED':raise ValueError('not original finished fixture')
        score=data['score'];duration=score.get('duration')
        if duration=='REGULAR':
            h,a=_score_pair(score.get('fullTime'))
            regular=score.get('regularTime')
            if isinstance(regular,dict) and not any(v is not None for v in regular.values()):regular=None
            if regular is not None and _score_pair(regular)!=(h,a):raise ValueError('conflicting regular-time fields')
        elif duration in ('EXTRA_TIME','PENALTY_SHOOTOUT'):
            h,a=_score_pair(score.get('regularTime'))
        else:raise ValueError('unsupported match duration')
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
    if any(f.get(k)!=fixture.get(k) for k in ('provider_competition','provider_stage')):raise ValueError('frozen provider scope mismatch')
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
