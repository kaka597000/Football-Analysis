"""Capture-safe feature replay, explicit phases and immutable snapshot storage."""
from collections import Counter
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from competition_scope import require
from v2_test.data_contract import aware,digest,validate,public_url
from publication import deliver


def snapshot(rows,target,evidence_root,*,provider,last_n=6,lookback_days=180,now=None):
    require(target)
    now=now or datetime.now(timezone.utc)
    cutoff,kickoff=aware(target['cutoff_at']),aware(target['kickoff_at'])
    if not cutoff<kickoff or cutoff>now:raise ValueError('valid actual cutoff required')
    if not target.get('stage') or type(last_n) is not int or last_n<1 or lookback_days<=0:
        raise ValueError('explicit phase and positive predeclared windows required')
    entities={target['home_id'],target['away_id']}
    if len(entities)!=2:raise ValueError('distinct team identities required')
    excluded=Counter();latest={};seen=set()
    target_ids={target['match_id'],*target.get('provider_match_ids',[])}
    for row in rows:
        captured=validate(row,evidence_root,now=now);rid=digest(row)
        if rid in seen:raise ValueError('duplicate observation')
        seen.add(rid)
        if row['provider']!=provider or row['cohort']!=target['cohort']:
            excluded['provider_or_cohort']+=1;continue
        if row.get('stage')!=target['stage']:
            excluded['unknown_or_different_phase']+=1;continue
        if captured>cutoff:
            excluded['captured_after_cutoff']+=1;continue
        if row['match_id'] in target_ids or aware(row['event_kickoff_at'])>=cutoff:
            excluded['target_or_future_event']+=1;continue
        if row['field'] in ('market_quote','player_availability'):
            excluded['current_match_evidence_separate_contract']+=1;continue
        if row['entity_id'] not in entities or aware(row['event_kickoff_at'])<cutoff-timedelta(days=lookback_days):
            excluded['outside_entity_or_window']+=1;continue
        key=(row['match_id'],row['entity_id'],row['field'],row.get('player_id'))
        previous=latest.get(key)
        if previous and captured==aware(previous['observed_at']) and digest(previous)!=rid:
            raise ValueError('conflicting simultaneous facts')
        if previous is None or captured>aware(previous['observed_at']):latest[key]=row
    features={};selected=[]
    for entity in sorted(entities):
        features[entity]={}
        for field in ('xg','xga','npxg','shots','shots_on_target'):
            group=sorted([r for r in latest.values() if r['entity_id']==entity and r['field']==field],
                         key=lambda r:(aware(r['event_kickoff_at']),r['match_id']),reverse=True)[:last_n]
            features[entity][field]={'n':len(group),'requested_n':last_n,
                'mean':sum(r['value'] for r in group)/len(group) if group else None}
            selected.extend(group)
    body={'schema':'WCPP-PHASE-ASOF-SNAPSHOT-1','target':target,'provider':provider,
          'created_at':now.isoformat(),'features':features,'observations':selected,'excluded':dict(excluded),
          'policy':{'last_n':last_n,'lookback_days':lookback_days},
          'claims':{'capture_available_by_cutoff':True,'source_first_known_inferred':False,
                    'prospective_forecast':False,'model_changed':False}}
    body['snapshot_sha256']=digest(body)
    return body


def save_snapshot(value,folder):
    expected=value['snapshot_sha256'];body={k:v for k,v in value.items() if k!='snapshot_sha256'}
    if digest(body)!=expected:raise ValueError('snapshot checksum mismatch')
    path=Path(folder)/(expected+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    deliver(path,json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8'))
    return str(path)


def _finished_espn_label(row,evidence,path):
    """Verify retained event identity, full-time state and regulation scores."""
    if urlsplit(evidence['url']).hostname not in {'site.api.espn.com','site.web.api.espn.com'}:
        raise ValueError('label source adapter unavailable')
    raw=json.loads(path.read_text(encoding='utf-8'));event_id=row['match_id'].removeprefix('espn:')
    if 'header' in raw:
        event=raw['header']
    else:
        matches=[event for event in raw.get('events',[]) if str(event.get('id'))==event_id]
        if len(matches)!=1:raise ValueError('retained result identity conflict')
        event=matches[0]
    if str(event.get('id'))!=event_id or len(event.get('competitions',[]))!=1:
        raise ValueError('retained result identity conflict')
    competition=event['competitions'][0]
    source_kickoff=competition.get('date') or event.get('date')
    if source_kickoff is None or aware(source_kickoff)!=aware(row['kickoff_at']):
        raise ValueError('retained kickoff conflict')
    status=competition.get('status') or event.get('status') or {}
    if (status.get('type',{}).get('completed') is not True
            or status.get('type',{}).get('name')!='STATUS_FULL_TIME' or status.get('period')!=2):
        raise ValueError('retained source is not finished regulation time')
    for side in ('home','away'):
        competitors=[c for c in competition.get('competitors',[]) if c.get('homeAway')==side]
        if len(competitors)!=1:raise ValueError('retained team identity')
        team=competitors[0]
        if row[side]!='espn:'+str(team['team']['id']):raise ValueError('retained team identity')
        score=team.get('score')
        if isinstance(score,dict):score=score.get('displayValue')
        if not isinstance(score,(str,int)) or str(score)!=str(row[side+'_goals']):
            raise ValueError('retained source score conflict')


def eligible_labels(rows,target,evidence_root=None):
    """Label availability, not result day alone, decides eligibility."""
    require(target);cutoff=aware(target['cutoff_at']);seen=set();accepted=[];excluded=Counter()
    if not target.get('stage'):raise ValueError('explicit target phase required')
    for row in rows:
        if row['match_id'] in seen:raise ValueError('duplicate result identity')
        seen.add(row['match_id'])
        if row['cohort']!=target['cohort'] or row.get('stage')!=target.get('stage'):
            excluded['scope_or_phase']+=1;continue
        if row['match_id']==target['match_id']:
            excluded['target_label']+=1;continue
        if row.get('event_status')!='FT':
            excluded['not_explicitly_finished']+=1;continue
        if row.get('score_basis')!='regulation_90' or not row.get('observed_at'):
            excluded['uncertified_label']+=1;continue
        if aware(row['observed_at'])>cutoff or aware(row['kickoff_at'])+timedelta(minutes=90)>cutoff:
            excluded['not_available_at_cutoff']+=1;continue
        captured=aware(row['observed_at']);event=aware(row['kickoff_at'])
        if captured<event+timedelta(minutes=90):raise ValueError('label captured before regulation completed')
        evidence=row.get('evidence')
        if evidence_root is None or not evidence:
            excluded['missing_retained_label_evidence']+=1;continue
        public_url(evidence['url'])
        base=Path(evidence_root).resolve();path=(base/evidence['file']).resolve()
        if (not path.is_relative_to(base) or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest()!=evidence['sha256']
                or evidence.get('identity_verified') is not True
                or aware(evidence['query_time'])!=captured):
            raise ValueError('label evidence identity, capture or checksum mismatch')
        if any(type(row.get(k)) is not int or row[k]<0 for k in ('home_goals','away_goals')):
            raise ValueError('invalid regulation score')
        _finished_espn_label(row,evidence,path)
        accepted.append(row)
    return accepted,dict(excluded)
