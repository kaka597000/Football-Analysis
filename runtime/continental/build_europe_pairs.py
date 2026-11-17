"""Fit fixed baseline/team-Poisson pairs from retained, verified ESPN results."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
from automation.trainer import _engine, _verify_raw_result
from europe_pair_adapter import sha, aware, source_hashes


def write(path, obj):
    with Path(path).open('x') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)


def verified_rows(rows, root, comp):
    good = {}; conflicts = set(); rejected = Counter(); cache = {}
    for row in rows:
        if row.get('competition') != comp:
            continue
        try:
            if row.get('stage') != 'league-phase' or row.get('score_basis') != 'regulation_90':
                raise ValueError('SCOPE')
            if type(row.get('neutral')) is not bool:
                raise ValueError('VENUE')
            if _verify_raw_result(row, Path(root), cache) != 'VERIFIED_RAW_REGULATION_90':
                raise ValueError('NO_RAW')
            ref = row['raw_reference']; p = Path(root) / ref['path']
            raw = gzip.decompress(p.read_bytes()) if p.suffix == '.gz' else p.read_bytes()
            summary = json.loads(raw); season = summary['header']['season']
            native_neutral = summary['header']['competitions'][0].get('neutralSite')
            if type(native_neutral) is not bool or native_neutral != row['neutral']:
                raise ValueError('NATIVE_VENUE')
            # Exact source-native phase wording, no knockout or old group-stage pooling.
            if (summary['header']['league']['slug'] != comp or season['year'] < 2024
                    or not season['name'].endswith(', League Phase')):
                raise ValueError('NATIVE_PHASE')
            if aware(row['known_at']) > datetime.now(timezone.utc):
                raise ValueError('FUTURE_KNOWN_AT')
            if row['date'] >= datetime.now(timezone.utc).date().isoformat():
                raise ValueError('FUTURE_RESULT')
            old = good.get(row['match_id'])
            if old and any(old[k] != row[k] for k in ('home','away','home_goals','away_goals','date')):
                conflicts.add(row['match_id'])
                raise ValueError('CONFLICTING_RESULT')
            good[row['match_id']] = row
        except (ValueError, KeyError, TypeError, OSError) as e:
            rejected[str(e)] += 1
    return sorted([r for k,r in good.items() if k not in conflicts], key=lambda r:(r['date'],r['match_id'])), dict(rejected)


def baseline(rows):
    return {'constant': [float(np.mean([r[k] for r in rows])) for k in ('home_goals','away_goals')],
            'fit_end': rows[-1]['date'], 'fit_n':len(rows)}


def metrics(engine, core, rows, role, teams):
    records=[]
    for r in rows:
        if any(r[k] not in teams for k in ('home','away')):
            records.append({'match_id':r['match_id'],'status':'ABSTAIN_COLD_START'});continue
        mu=np.asarray(core['constant']) if role=='current' else engine.rates(core,[r])[0]
        m=engine.matrix(mu);pick=tuple(map(int,np.unravel_index(m.argmax(),m.shape)))
        records.append({'match_id':r['match_id'],'status':'PREDICTED','primary':list(pick),
                        'actual':[r['home_goals'],r['away_goals']], 'hit':pick==(r['home_goals'],r['away_goals'])})
    return {'population':len(rows),'predicted':sum(x['status']=='PREDICTED' for x in records),
            'top1_hits':sum(x.get('hit',False) for x in records),'records':records}


def build(rows, root, out, comp):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    good,rejected=verified_rows(rows,root,comp)
    if len(good)<120:raise ValueError(f'insufficient verified history: {len(good)}')
    dates=sorted({r['date'] for r in good});split=dates[int(len(dates)*.75)]
    fit=[r for r in good if r['date']<split];hold=[r for r in good if r['date']>=split]
    if len(fit)<80 or len(hold)<20:raise ValueError('insufficient chronological split')
    source_hash=hashlib.sha256(json.dumps(good,sort_keys=True).encode()).hexdigest()
    protocol={'competition':comp,'stage':'league-phase','fit_end':fit[-1]['date'],'holdout_start':hold[0]['date'],
              'half_life':365,'ridge':20,'selection':'FIXED_NO_TUNING','historical_asof_certified':False,
              'source_rows_sha256':source_hash,'created_at':datetime.now(timezone.utc).isoformat()}
    write(out/'protocol.json',protocol);write(out/'verified-rows.json',good)
    engine=_engine();teams={r[k] for r in fit for k in ('home','away')}
    report={'rows':len(good),'rejected':rejected,'fit_n':len(fit),'holdout_n':len(hold),
            'historical_development_diagnostic_only':True,'automatic_promotion':False}
    for role in ('current','test'):
        core=baseline(fit) if role=='current' else engine.fit_core(fit,half_life=365,ridge=20)
        report[role]=metrics(engine,core,hold,role,teams)
    index={'competition':comp,'stage':'league-phase','source_files_sha256':source_hashes(),'created_at':datetime.now(timezone.utc).isoformat(),
           'fit_end':good[-1]['date'],'teams':sorted({r[k] for r in good for k in ('home','away')}),
           'source_rows_sha256':source_hash,'rows_file_sha256':sha(out/'verified-rows.json'),'automatic_promotion':False}
    for role in ('current','test'):
        core=baseline(good) if role=='current' else engine.fit_core(good,half_life=365,ridge=20)
        artifact={'schema':'WCPP-EUROPE-PAIR-ARTIFACT-1','role':role,'version':f'WCPP-EUROPE-{role.upper()}-RESEARCH-1','competition':comp,
                  'stage':'league-phase','fit_end':good[-1]['date'],'core':core,'automatic_promotion':False}
        name=role+'-artifact.json';write(out/name,artifact);index[role]={'file':name,'sha256':sha(out/name)}
    index['created_at'] = datetime.now(timezone.utc).isoformat()
    write(out/'index.json',index);write(out/'report.json',report)
    return {'engine':'europe','index_path':str(out/'index.json'),'index_sha256':sha(out/'index.json'),
            'stages':['league-phase'],'status':'READY_RESEARCH_PAIR','evidence':str(out/'report.json')}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--rows',required=True);p.add_argument('--raw-root',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    rows=json.loads(Path(a.rows).read_text());routes={}
    for c in ('uefa.europa','uefa.europa.conf'):
        routes[c]=build(rows,a.raw_root,Path(a.out)/c,c)
    write(Path(a.out)/'routes.json',routes)
