"""Normalize new retained ESPN summaries for forward-only research fitting."""
from datetime import datetime,timedelta,timezone
import hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
from competition_scope import require
from research_program.replay import _finished_espn_label
from v2_test.data_contract import aware,public_url

LEAGUES={'eng.1','esp.1','ger.1','ita.1','fra.1','swe.1','nor.1'}

def normalize(rows,root):
    root=Path(root).resolve();now=datetime.now(timezone.utc);out=[];ids=set()
    for r in rows:
        require(r)
        if r['cohort'] not in LEAGUES or r.get('stage')!='regular' or not r.get('stage_basis'):
            raise ValueError('reviewed regular-league phase required')
        if r['match_id'] in ids:raise ValueError('duplicate event')
        ids.add(r['match_id']);public_url(r['source_url'])
        if '/soccer/'+r['cohort']+'/' not in urlsplit(r['source_url']).path:
            raise ValueError('source league conflict')
        relative=Path(r['score_evidence']);path=(root/relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file():raise ValueError('retained response path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=r['score_evidence_sha256']:raise ValueError('retained checksum')
        raw=json.loads(path.read_text(encoding='utf-8'))
        league=raw['header'].get('league') or {}
        if league.get('slug')!=r['cohort'] or league.get('isTournament') is not False:
            raise ValueError('retained league identity or regular format conflict')
        comp=raw['header']['competitions'][0]
        neutral=comp.get('neutralSite')
        if type(neutral) is not bool or r.get('neutral_site') is not neutral:raise ValueError('explicit matching venue state required')
        kickoff=comp['date'];captured=aware(r['observed_at'])
        if (aware(kickoff).date().isoformat()!=r['date'] or r['date']>=now.date().isoformat()
                or not aware(kickoff)+timedelta(minutes=90)<=captured<=now
                or aware(r['query_time'])!=captured or r.get('first_known_at') is not None):
            raise ValueError('actual capture and UTC match date required; no backdating')
        evidence={'url':r['source_url'],'file':r['score_evidence'],'sha256':r['score_evidence_sha256'],
                  'query_time':r['query_time'],'identity_verified':r.get('identity_verified')}
        if evidence['identity_verified'] is not True:raise ValueError('reviewed identity required')
        row={'match_id':r['match_id'],'cohort':r['cohort'],'competition':r['cohort'],'stage':'regular',
             'stage_basis':r['stage_basis'],'date':r['date'],'kickoff_at':kickoff,
             'home':r['home'],'away':r['away'],'home_goals':r['home_score'],'away_goals':r['away_score'],
             'gender':'men','age':'senior','neutral':neutral,'event_status':'FT','score_basis':'regulation_90',
             'observed_at':r['observed_at'],'first_known_at':None,'source_url':r['source_url'],'evidence':evidence}
        if any(type(row[k]) is not int or not 0<=row[k]<=40 for k in ('home_goals','away_goals')):raise ValueError('regulation score')
        _finished_espn_label(row,evidence,path)
        season=raw['header'].get('season') or {}
        row['season']='espn:'+r['cohort']+':'+str(season['year']) if type(season.get('year')) is int else None
        row['season_evidence']={'source_file':r['score_evidence'],'source_name':season.get('name'),'source_year':season.get('year')}
        out.append(row)
    return out
