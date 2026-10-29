"""Build an isolated, scoped single-leg cup research pair from accepted 90m rows.

Does not modify the active registry. Retrospective diagnostics are not promotion.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'optimization'))
from models import fit_core
sys.path.insert(0, str(ROOT))
from build_active_routes_20261027 import generic_artifact, metrics
from optimization.schema import file_hash

STAGES = ('first-round', 'second-round', 'third-round', 'fourth-round', 'quarterfinals')


def save(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)


def build(db, out, cutoff, family='efl', supplement=None):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    c = sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    selected = [dict(x) for x in c.execute(
        "SELECT * FROM matches WHERE competition='eng.league_cup' AND training_eligible=1 "
        "AND result_status='accepted' AND date<? ORDER BY date,match_id", (cutoff,))
        if x['stage'] in STAGES]
    cohort='eng.league_cup:single_leg';competition='eng.league_cup'; stages=STAGES
    if family == 'afc2':
        source=Path(db).parents[2]/'reports/afc-history-20261013/afc-training-90.json'
        selected=[x for x in json.loads(source.read_text()) if x['competition']=='afc.cup'
                  and x['era']=='CHAMPIONS_TWO' and x['phase']=='group_stage'
                  and x['training_eligible']==1 and x['date']<cutoff]
        cohort='afc.cup:CHAMPIONS_TWO:group_stage';competition='afc.cup';stages=('group_stage',)
        for x in selected:x['stage']='group_stage'
    if supplement:
        additions=json.loads(Path(supplement).read_text())
        selected.extend(additions)
        if any(x['date']>=cutoff or x['stage'] not in stages for x in selected):
            raise ValueError('supplement outside cutoff/stage scope')
    selected.sort(key=lambda x:(x['date'],x['match_id']))
    redirects = dict(c.execute('SELECT old_id,new_id FROM team_redirects'))
    def team(value):
        seen = set()
        while value in redirects:
            if value in seen: raise ValueError('team redirect cycle')
            seen.add(value); value = redirects[value]
        return value
    rows = []; seen = {}
    for x in selected:
        if type(x['h90']) is not int or type(x['a90']) is not int or min(x['h90'],x['a90'])<0:
            raise ValueError('invalid 90-minute label')
        h,a = team(x['home_id']),team(x['away_id'])
        key = (x['date'],h,a)
        if key in seen:
            if seen[key] != (x['h90'],x['a90']): raise ValueError('duplicate label conflict')
            continue
        seen[key] = (x['h90'],x['a90'])
        rows.append({'match_id':x['match_id'], 'date':x['date'], 'season':x['season'],
            'cohort':cohort, 'competition':competition,
            'home':h, 'away':a, 'home_goals':x['h90'], 'away_goals':x['a90'],
            'stage':x['stage'], 'neutral':x.get('neutral'), 'gender':'male', 'age':'senior'})
    c.close()
    if len(rows)<120: raise ValueError('minimum research history unavailable')
    dates=sorted({x['date'] for x in rows}); boundary=dates[int(len(dates)*.75)]
    train=[x for x in rows if x['date']<boundary]
    audit=[x for x in rows if x['date']>=boundary]
    if len(train)<80 or len(audit)<20: raise ValueError('temporal split unavailable')
    def constant(items):
        return {'constant':[float(np.mean([x[k] for x in items]))
                            for k in ('home_goals','away_goals')],
                'fit_end':items[-1]['date'],'fit_n':len(items)}
    base=metrics(constant(train),audit,'constant')
    candidate=metrics(fit_core(train,half_life=365,ridge=20),audit,'team_poisson')
    save(out/'history.json',rows)
    save(out/'manifest.json',{'created_at':datetime.now(timezone.utc).isoformat(),
         'source_database':str(db),'rows':len(rows),'history_sha256':file_hash(out/'history.json'),
         'cutoff_exclusive':cutoff,'score_basis':'accepted_regulation_90_only',
         'neutral_policy':'UNKNOWN_PRESERVED_NOT_ASSUMED_HOME_GROUND',
         'first_known_at':'MISSING_RETROSPECTIVE_ONLY',
         'excluded':['semifinals_two_leg','final_neutral','unaccepted_or_ineligible','today_or_future']})
    save(out/'validation.json',{'mode':'FIXED_CONFIG_RETROSPECTIVE_NOT_UNSEEN_OR_PROMOTION',
         'train_n':len(train),'audit_start':boundary,'audit_n':len(audit),
         'current_constant':base,'test_team_poisson':candidate,
         'top1_paired_hit_gain':candidate['top1_hits']-base['top1_hits'],
         'configuration_selected_using_audit':False,'promotion_proven':False})
    scope={'cohort':cohort,'competition':competition,
           'gender':'men','age':'senior','stages':list(stages)}
    for name,kind,core in [('current','constant',constant(rows)),
                            ('test','team_poisson',fit_core(rows,half_life=365,ridge=20))]:
        a=generic_artifact(version='WCPP-'+family.upper()+'-CUP-'+name.upper()+'-0.1',
            role=name.upper()+'_CUP_RESEARCH',kind=kind,core=core,
            dataset_manifest=out/'manifest.json',scope=scope,
            source_evidence={'accepted_rows':len(rows),'stages':dict(Counter(x['stage'] for x in rows))})
        save(out/(name+'-artifact.json'),a)
    route={'current_engine':'generic_poisson','test_engine':'generic_poisson',
           'test_version':'WCPP-'+family.upper()+'-CUP-TEST-0.1'}
    for prefix,name in [('', 'current'),('test_','test')]:
        path=out/(name+'-artifact.json')
        route[prefix+'artifact_path']=str(path.resolve());route[prefix+'artifact_sha256']=file_hash(path)
    save(out/'research-registry.json',{'version':'WCPP-EFL-CUP-RESEARCH-1',
        'status':'ACTIVE_RESEARCH','models':{scope['cohort']:route}})
    return {'n':len(rows),'train_end':rows[-1]['date'],'audit_n':len(audit),
            'baseline_hits':base['top1_hits'],'test_hits':candidate['top1_hits'],
            'registry':str(out/'research-registry.json')}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',required=True);p.add_argument('--out',required=True);p.add_argument('--cutoff',required=True)
    p.add_argument('--family',choices=('efl','afc2'),default='efl');p.add_argument('--supplement')
    print(json.dumps(build(**vars(p.parse_args())),ensure_ascii=False))
