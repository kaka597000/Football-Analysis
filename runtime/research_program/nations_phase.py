"""Evidence-backed men's Nations League phases; no activity/model registration.

Provider group-stage and league-A/B/C/D are one league-phase with a separate
league_level. Unknown grades, playoff pairings and phases remain unclassified.
"""
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

CODE = 'uefa.nations'
PHASES = {'league-phase', 'quarterfinals', 'semifinals', 'final', 'third-place',
          'promotion-relegation-playoffs'}
LEVELS = {'A', 'B', 'C', 'D', 'A-B', 'B-C', 'C-D'}
TERMINAL = {'STATUS_FULL_TIME','STATUS_FINAL_AET','STATUS_FINAL_PEN'}


def _score_integer(value):
    if value is None or value=='':return None
    if isinstance(value,dict):
        numbers=[_score_integer(value[k]) for k in ('value','displayValue') if k in value]
        if not numbers:return None
        if len(set(numbers))!=1:raise ValueError('score value/displayValue conflict')
        return numbers[0]
    if isinstance(value,bool):raise ValueError('boolean score')
    number=float(value)
    if not number.is_integer() or not 0<=number<=99:raise ValueError('invalid integer score')
    return int(number)


def verify_period_scores(competitor,status):
    """Verified regulation/ET split or None when explicit period proof is absent.

    Completed/post FT with exactly two parts can prove their combined 90-minute
    total without inferring half order. If supplied, IDs must be 1,2. AET/PEN
    requires explicit 1,2,3,4. PEN may additionally have explicit period 5, which
    must equal shootoutScore; this period is never added to match goals.
    Contradictions raise; incomplete evidence is retained as unknown by importers.
    """
    if not isinstance(status,dict):return None
    state=status.get('type',status);name=state.get('name')
    if name not in TERMINAL or state.get('completed') is not True or state.get('state')!='post':return None
    total=_score_integer(competitor.get('score'))
    periods=competitor.get('linescores')
    if not isinstance(periods,list):return None
    expected=[1,2] if name=='STATUS_FULL_TIME' else [1,2,3,4]
    if any(not isinstance(p,dict) for p in periods):raise ValueError('malformed period score')
    explicit=any(p.get('period') is not None for p in periods)
    if name=='STATUS_FULL_TIME':
        if state.get('shortDetail')!='FT' and state.get('detail')!='FT':return None
        if status.get('period') not in (None,2):raise ValueError('FT status contradicts extra-time period')
        if any((_score_integer(competitor.get(key)) or 0)>0 for key in ('shootoutScore','extraTimeScore')):
            raise ValueError('FT status contradicts shootout/extra-time score')
        if len(periods)!=2:return None
    elif not explicit:return None
    if explicit:
        if any(p.get('period') is None for p in periods):return None
        if any(type(p['period']) is not int for p in periods):raise ValueError('invalid period identity')
        ids=[p['period'] for p in periods]
        if name=='STATUS_FINAL_PEN' and ids==[1,2,3,4,5]:expected=ids
        if ids!=expected:
            if len(ids)!=len(set(ids)) or ids!=sorted(ids) or any(x not in expected for x in ids):
                raise ValueError('period order or identity conflict')
            return None
    values=[_score_integer(p) for p in periods]
    if total is None or any(value is None for value in values):return None
    shootout=_score_integer(competitor.get('shootoutScore')) if name=='STATUS_FINAL_PEN' else None
    if name=='STATUS_FINAL_PEN':
        if shootout is None:return None
        if len(values)==5:
            if values[-1]!=shootout:raise ValueError('period 5 differs from shootout score')
            values=values[:4]
    if sum(values)!=total:raise ValueError('final total conflicts with period scores')
    return {'regulation':sum(values[:2]),'extra_time':sum(values[2:]),'total':total,'shootout':shootout,
            'score_basis_evidence':'explicit_period_ids_and_total' if explicit else 'explicit_full_time_status_and_two_period_sum'}


def _labels(payload):
    event=payload.get('header',payload)
    fields=[]
    def add(path,value):
        if isinstance(value,str) and value.strip():fields.append({'path':path,'value':value})
    season=event.get('season') or {}
    for key in ('slug','name','displayName'):add('season.'+key,season.get(key))
    if isinstance(season.get('type'),dict):
        for key in ('name','text','abbreviation'):add('season.type.'+key,season['type'].get(key))
    competitions=event.get('competitions') or []
    if len(competitions)!=1:return fields
    comp=competitions[0]
    add('competitions[0].altGameNote',comp.get('altGameNote'))
    for name in ('group','type','series'):
        item=comp.get(name) or {}
        if isinstance(item,dict):
            for key in ('name','text','title','abbreviation'):add('competitions[0].'+name+'.'+key,item.get(key))
    for i,note in enumerate(comp.get('notes') or []):
        if isinstance(note,dict):add(f'competitions[0].notes[{i}].headline',note.get('headline'))
    return fields


def classify_source_phase(payload):
    """Parse a retained ESPN summary or single scoreboard event, never labels.

    This pure function only resolves phase. Callers must independently verify
    the source bytes, tournament, event identity and regulation-score basis.
    """
    if not isinstance(payload,dict):
        return dict(complete=False,stage=None,league_level=None,source_fields=[],reasons=['source object required'])
    event=payload.get('header',payload)
    if (not isinstance(event,dict) or not isinstance(event.get('season'),dict)
            or not isinstance(event.get('competitions'),list) or len(event['competitions'])!=1
            or not isinstance(event['competitions'][0],dict)):
        return dict(complete=False,stage=None,league_level=None,source_fields=[],reasons=['one source event with season and competition required'])
    try:fields=_labels(payload)
    except (TypeError,AttributeError):
        return dict(complete=False,stage=None,league_level=None,source_fields=[],reasons=['malformed source phase fields'])
    stages=set();levels=set();league_hint=False
    for field in fields:
        text=re.sub(r'[_–—-]+',' ',field['value'].lower())
        pairs=re.findall(r'\bleagues?\s+([abcd])\s*(?:/|\band\b|\bto\b|\s)\s*([abcd])\b',text)
        for first,second in pairs:
            levels.add('-'.join(sorted((first.upper(),second.upper()))))
        if not pairs:
            levels.update(x.upper() for x in re.findall(r'\bleague\s+([abcd])\b',text))
        # In this competition GROUP D1 means League D, group 1. GROUP D alone
        # is insufficient: a group letter must never become an assumed grade.
        levels.update(x.upper() for x in re.findall(r'\bgroup\s+([abcd])\s*[1-4]\b',text))
        if re.search(r'\b(?:promotion|relegation)\b.*\bplay\s*offs?\b',text):
            stages.add('promotion-relegation-playoffs')
        elif re.search(r'\bquarter\s*finals?\b',text):stages.add('quarterfinals')
        elif re.search(r'\bsemi\s*finals?\b',text):stages.add('semifinals')
        elif re.search(r'\b(?:third|3rd)\s+place\b',text):stages.add('third-place')
        elif re.search(r'\bfinal\b',text):stages.add('final')
        elif re.search(r'\b(?:group\s+stage|league\s+phase)\b',text):stages.add('league-phase')
        elif re.search(r'\b(?:league\s+[abcd]|group\s+[abcd]\s*[1-4])\b',text):
            league_hint=True
    # League-grade fields accompany knockouts too; they do not create a second
    # league-phase assertion when an explicit knockout label is present.
    if not stages and league_hint:stages.add('league-phase')
    stage=next(iter(stages)) if len(stages)==1 else None
    level=next(iter(levels)) if len(levels)==1 else None
    reasons=[]
    if not stage:reasons.append('unknown or conflicting source phase')
    if level not in LEVELS:reasons.append('unknown or conflicting source league level')
    if stage!='promotion-relegation-playoffs' and level and '-' in level:
        reasons.append('cross-grade identity only supported for promotion/relegation playoffs')
    # Historic League C relegation play-outs may have one proven grade; modern
    # A/B or B/C playoffs require a proven pair. Never invent either from teams.
    return dict(complete=not reasons,stage=stage,league_level=level if level in LEVELS else None,
                source_fields=fields,reasons=reasons,phase_conflict=len(stages)>1,
                league_level_conflict=len(levels)>1 or bool(level and level not in LEVELS))


def _aware(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('source query timezone required')
    return result.astimezone(timezone.utc)


def _event_identity(event):
    """Exact provider identity and kickoff for joining complementary sources."""
    event_id=str(event['id']);comps=event['competitions']
    if len(comps)!=1 or str(comps[0].get('id'))!=event_id:raise ValueError('ambiguous supplemental competition identity')
    comp=comps[0];kickoff=_aware(comp['date'])
    if event.get('date') and _aware(event['date'])!=kickoff:raise ValueError('raw event/competition kickoff conflict')
    teams={item['homeAway']:str(item['team']['id']) for item in comp['competitors']}
    if len(comp['competitors'])!=2 or set(teams)!={'home','away'} or len(set(teams.values()))!=2:
        raise ValueError('ambiguous supplemental teams')
    return event_id,teams['home'],teams['away'],kickoff


def resolve_phase_evidence(summary,supplemental=None,context=None):
    """Join a verified summary with retained same-event scoreboard phase proof.

    Caller remains responsible for verifying primary summary bytes and scores.
    context optionally supplies observed_at, cutoff_at and allowed raw_root.
    The supplement never supplies scores or independently authorizes training.
    """
    primary=summary.get('header',summary);base=classify_source_phase(primary)
    if supplemental is None:return base
    context=context or {}
    if primary.get('league',{}).get('slug')!=CODE:raise ValueError('primary summary competition mismatch')
    identity=_event_identity(primary)
    path=Path(supplemental['source_path'])
    if not path.is_absolute():raise ValueError('absolute supplemental source path required')
    if context.get('raw_root') and not path.resolve().is_relative_to(Path(context['raw_root']).resolve()):
        raise ValueError('supplemental source outside retained root')
    body=path.read_bytes()
    if hashlib.sha256(body).hexdigest()!=supplemental['source_sha256']:raise ValueError('supplemental source hash mismatch')
    url=urlparse(supplemental['source_url'])
    if (url.scheme!='https' or url.hostname!='site.api.espn.com' or url.port not in (None,443)
            or url.path!=f'/apis/site/v2/sports/soccer/{CODE}/scoreboard'):
        raise ValueError('supplemental Nations League scoreboard URL required')
    query=_aware(supplemental['query_time'])
    if query>datetime.now(timezone.utc):raise ValueError('future supplemental query')
    for key in ('observed_at','cutoff_at'):
        if context.get(key) and query>_aware(context[key]):raise ValueError('supplemental evidence after '+key)
    raw=json.loads(gzip.decompress(body) if path.suffix=='.gz' else body);raw=raw.get('data',raw)
    if {x.get('slug') for x in raw.get('leagues',[])}!={CODE}:raise ValueError('supplemental competition mismatch')
    events=[event for event in raw['events'] if str(event['id'])==identity[0]]
    if len(events)!=1:raise ValueError('supplemental event missing or duplicated')
    other=events[0]
    if _event_identity(other)!=identity:raise ValueError('supplemental event/team/kickoff mismatch')
    years=[event.get('season',{}).get('year') for event in (primary,other)]
    if None not in years and years[0]!=years[1]:raise ValueError('supplemental season mismatch')
    extra=classify_source_phase(other)
    for parsed in (base,extra):
        if parsed.get('phase_conflict') or parsed.get('league_level_conflict'):
            raise ValueError('conflicting raw phase/league evidence')
    joined={}
    for key in ('stage','league_level'):
        if base.get(key) is not None and extra.get(key) is not None and base[key]!=extra[key]:
            raise ValueError('supplemental '+key+' conflicts with summary')
        joined[key]=base.get(key) or extra.get(key)
    reasons=[]
    if joined['stage'] not in PHASES:reasons.append('unknown joined phase')
    if joined['league_level'] not in LEVELS:reasons.append('unknown joined league level')
    if joined['stage']!='promotion-relegation-playoffs' and joined['league_level'] and '-' in joined['league_level']:
        reasons.append('cross-grade identity only supported for promotion/relegation playoffs')
    proof={key:supplemental[key] for key in ('source_path','source_sha256','source_url','query_time')}
    return {**joined,'complete':not reasons,'reasons':reasons,'source_fields':base['source_fields'],
            'supplemental_phase_evidence':proof,'supplemental_source_fields':extra['source_fields'],
            'phase_join':'EXACT_EVENT_TEAMS_KICKOFF; summary remains the score source',
            'phase_conflict':False,'league_level_conflict':False}


def classify(row):
    """Require actual retained source evidence for a phase-specific train route.

    phase_evidence: source_path, source_sha256, source_url, query_time. The raw
    source must be a completed/post summary (JSON or JSON.gz). FT needs exactly
    two parts summing to its total; IDs, when provided, must be 1,2. AET/PEN
    needs explicit regulation and ET period IDs. A supplemental_phase_evidence
    scoreboard may prove missing phase fields for the exact same event, but can
    never replace the primary summary or independently authorize training.
    """
    result=dict(complete=False,classification=None,route_key=None,reasons=[],
                status='RESEARCH_ONLY_NOT_ACTIVITY_ROUTE',active_route_gap=True)
    try:
        if not isinstance(row,dict):raise ValueError('fixture object required')
        if row.get('competition',CODE)!=CODE or row.get('cohort','').split(':',1)[0]!=CODE:
            raise ValueError('Nations League identity mismatch')
        if row.get('gender') not in ('men','male') or row.get('age')!='senior':
            raise ValueError('explicit adult-men identity required')
        if row.get('score_basis')!='regulation_90':raise ValueError('regulation-90 target required')
        e=row.get('phase_evidence') or {};path=Path(e['source_path'])
        if not path.is_absolute():raise ValueError('absolute retained phase source path required')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=e['source_sha256']:raise ValueError('phase source hash mismatch')
        payload=json.loads(gzip.decompress(raw) if path.suffix=='.gz' else raw)
        payload=payload.get('data',payload)
        url=urlparse(e['source_url'])
        if url.scheme!='https' or url.hostname!='site.api.espn.com' or url.port not in (None,443):
            raise ValueError('ESPN HTTPS phase source required')
        prefix=f'/apis/site/v2/sports/soccer/{CODE}/'
        event_id=str(row.get('source_event_id') or row.get('event_id') or '')
        if not event_id:raise ValueError('explicit source event id required')
        if url.path==prefix+'summary':
            event=payload['header'];league=event['league']['slug']
            if parse_qs(url.query).get('event')!=[event_id]:raise ValueError('summary URL event mismatch')
        elif url.path==prefix+'scoreboard':
            raise ValueError('scoreboard is phase inventory only; completed summary required for training')
        else:raise ValueError('unsupported phase source endpoint')
        if league!=CODE or str(event['id'])!=event_id:raise ValueError('raw competition/event mismatch')
        query=_aware(e['query_time'])
        if query>datetime.now(timezone.utc):raise ValueError('future source query')
        if row.get('cutoff_at') and query>_aware(row['cutoff_at']):raise ValueError('phase evidence after cutoff')
        if row.get('observed_at') and query>_aware(row['observed_at']):raise ValueError('phase evidence after observation')
        comps=event['competitions']
        if len(comps)!=1:raise ValueError('ambiguous raw competition')
        comp=comps[0]
        if str(comp.get('id'))!=event_id:raise ValueError('raw competition event mismatch')
        state=comp.get('status',{}).get('type',{})
        status=state.get('name')
        if state.get('completed') is not True or state.get('state')!='post' or status not in TERMINAL:
            raise ValueError('completed terminal summary required for training')
        kickoff=_aware(comp['date'])
        if kickoff+timedelta(minutes=90)>query:raise ValueError('completed summary queried before regulation time elapsed')
        if type(comp.get('neutralSite')) is not bool or type(row.get('neutral')) is not bool or row['neutral']!=comp['neutralSite']:
            raise ValueError('raw neutral venue missing or conflicts with training row')
        sides={x['homeAway']:str(x['team']['id']) for x in comp['competitors']}
        if len(comp['competitors'])!=2 or set(sides)!={'home','away'} or len(set(sides.values()))!=2:
            raise ValueError('ambiguous raw teams')
        if any(row.get(side)!='espn:'+tid for side,tid in sides.items()):raise ValueError('raw team identity mismatch')
        if row.get('date')!=_aware(comp['date']).date().isoformat():raise ValueError('raw match date mismatch')
        proven_scores={}
        for competitor in comp['competitors']:
            side=competitor['homeAway'];scores=verify_period_scores(competitor,comp['status'])
            if scores is None:raise ValueError('explicit complete regulation/ET period evidence required')
            if type(row.get(side+'_goals')) is not int or row[side+'_goals']!=scores['regulation']:
                raise ValueError('training regulation score differs from raw periods')
            proven_scores[side]=scores
        if status=='STATUS_FINAL_PEN':
            if proven_scores['home']['total']!=proven_scores['away']['total']:
                raise ValueError('pre-shootout match total must be level')
            if proven_scores['home']['shootout']==proven_scores['away']['shootout']:
                raise ValueError('completed shootout must have a winner')
        parsed=resolve_phase_evidence(event,row.get('supplemental_phase_evidence'),row)
        if not parsed['complete']:raise ValueError('; '.join(parsed['reasons']))
        if parsed['stage']=='league-phase' and status!='STATUS_FULL_TIME':
            raise ValueError('league-phase cannot have extra-time or shootout result')
        stage='league-phase' if row.get('stage')=='group-stage' else row.get('stage')
        level=row.get('league_level',row.get('league_grade'))
        if row.get('league_level') and row.get('league_grade') and row['league_level']!=row['league_grade']:
            raise ValueError('conflicting supplied league levels')
        if stage!=parsed['stage'] or level!=parsed['league_level']:raise ValueError('supplied phase/level differs from raw source')
        classification=dict(competition=CODE,gender='men',age='senior',entity='national',
            kind='national_competitive',league_level=level,stage=stage)
        result.update(complete=True,classification=classification,route_key='|'.join(classification.values()),
                      provenance={'source_path':str(path),'source_url':e['source_url'],'source_sha256':e['source_sha256'],'source_event_id':event_id,'query_time':e['query_time'],
                                  'phase_fields':parsed['source_fields']})
        if parsed.get('supplemental_phase_evidence'):
            result['provenance'].update(supplemental_phase_evidence=parsed['supplemental_phase_evidence'],
                                       supplemental_phase_fields=parsed['supplemental_source_fields'],phase_join=parsed['phase_join'])
    except (KeyError,ValueError,TypeError,AttributeError,OSError) as exc:
        result['reasons']=[str(exc)]
    return result
