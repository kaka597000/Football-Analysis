"""Edition/phase isolated small-sample Poisson research, with explicit missing venue."""
from collections import defaultdict
from datetime import datetime,timezone
import json,math,sys
from pathlib import Path
if __package__.startswith('runtime.'):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.optimize import minimize
from v2.model import matrix
from v2_test.evaluation import evaluate
from competition_scope import training_rows
from .database import rows,dump,key
from .evidence import sha

PROTOCOL={'schema':'WCPP-JCUP-MODEL-PROTOCOL-1','minimum_history':20,'minimum_fit':12,
          'holdout_date_fraction':.75,'minimum_acceptance_sample':30,'ridge':20.,
          'candidate_families':['constant_poisson','team_poisson'],
          'neutral_policy':'explicit unknown indicator; never fill unknown as false',
          'selection':'none; fixed current/test candidate roles before evaluation',
          'grouping':'edition, exact phase, tie format, leg; no J1 or cross-era pooling',
          'historical_asof_certified':False,'automatic_activation':False}

def write(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')

def design(rows,teams):
    n=len(teams);index={x:i for i,x in enumerate(teams)};x=np.zeros((2*len(rows),3+2*n))
    for i,r in enumerate(rows):
        for side,(t,o) in enumerate(((r['home'],r['away']),(r['away'],r['home']))):
            x[2*i+side,0]=1.
            # Home advantage is used only when venue is explicitly non-neutral.
            x[2*i+side,1]=float(side==0 and r.get('neutral') is False)
            x[2*i+side,2]=float(r.get('neutral') is None)
            if t in index:x[2*i+side,3+index[t]]=1.
            if o in index:x[2*i+side,3+n+index[o]]=1.
    return x

def fit(rows,role):
    teams=sorted({r[s] for r in rows for s in ('home','away')}) if role=='test' else []
    x=design(rows,teams);y=np.array([r[k] for r in rows for k in ('home_goals','away_goals')],float)
    penalty=np.full(x.shape[1],PROTOCOL['ridge']);penalty[:3]=[.001,1.,1.]
    def objective(b):
        raw=x@b;eta=np.clip(raw,-5,4);mu=np.exp(eta);active=(raw>-5)&(raw<4)
        return float(np.sum(mu-y*eta)+.5*np.sum(penalty*b*b)),x.T@((mu-y)*active)+penalty*b
    initial=np.zeros(x.shape[1]);initial[0]=math.log(max(.1,float(y.mean())))
    result=minimize(objective,initial,jac=True,method='L-BFGS-B',options={'maxiter':300,'ftol':1e-10})
    if not result.success:raise ValueError('Poisson optimization did not converge')
    return {'role':role,'teams':teams,'coefficients':result.x.tolist(),'fit_n':len(rows),
            'fit_end':max(r['date'] for r in rows),'unknown_venue_n':sum(r.get('neutral') is None for r in rows)}

def distribution(core,fixture):
    mu=np.clip(np.exp(design([fixture],core['teams'])@np.array(core['coefficients'])),.08,8.)
    return matrix(*mu),mu.tolist()

def train(db,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    protocol={**PROTOCOL,'created_at':datetime.now(timezone.utc).isoformat(),'database_sha256':sha(db),'engine_sha256':sha(__file__)}
    write(out/'protocol.json',protocol)
    groups=defaultdict(list)
    for r in rows(db):groups[r['cohort']].append(r)
    index={'schema':'WCPP-JCUP-CANDIDATES-1','routes':{},'untrained':[],'active_registry_modified':False}
    for cohort,history in sorted(groups.items()):
        history=sorted(history,key=lambda r:(r['date'],r['match_id']));training_rows(history)
        days=sorted({r['date'] for r in history});boundary=days[int(len(days)*PROTOCOL['holdout_date_fraction'])]
        prior=[r for r in history if r['date']<boundary];targets=[r for r in history if r['date']>=boundary]
        if len(history)<PROTOCOL['minimum_history'] or len(prior)<PROTOCOL['minimum_fit']:
            index['untrained'].append({'cohort':cohort,'n':len(history),'reason':'INSUFFICIENT_EDITION_PHASE_HISTORY'});continue
        folder=out/key(cohort)[:16];folder.mkdir()
        write(folder/'dataset.json',history);metrics={};artifacts={};predictions={}
        for role in ('current','test'):
            evaluation_core=fit(prior,role);evaluated=[];records=[]
            for r in targets:
                m,_=distribution(evaluation_core,r);mode=[int(v) for v in np.unravel_index(m.argmax(),m.shape)]
                evaluated.append({'matrix':m,'home_goals':r['home_goals'],'away_goals':r['away_goals'],'primary_score':mode})
                records.append({'match_id':r['match_id'],'date':r['date'],'primary':mode,'actual':[r['home_goals'],r['away_goals']],
                                'matrix':m.tolist(),'unseen_teams_using_fitted_prior':[r[k] for k in ('home','away') if role=='test' and r[k] not in evaluation_core['teams']]})
            metrics[role]=evaluate(evaluated);predictions[role]=records
            artifact={'schema':'WCPP-JCUP-POISSON-1','version':'WCPP-JCUP-'+role.upper()+'-0.1','cohort':cohort,
                      'role':role,'era':history[0]['era'],'stage':history[0]['stage'],'leg':history[0].get('leg'),
                      'tie_format':history[0]['tie_format'],'core':fit(history,role),'built_at':datetime.now(timezone.utc).isoformat(),
                      'protocol_path':str((out/'protocol.json').resolve()),'protocol_sha256':sha(out/'protocol.json'),
                      'dataset_path':str((folder/'dataset.json').resolve()),'dataset_sha256':sha(folder/'dataset.json'),
                      'engine_path':str(Path(__file__).resolve()),'engine_sha256':sha(__file__),
                      'status':'TRAINED_RESEARCH_NOT_ACTIVITY_ROUTE','formal_acceptance_passed':False,
                      'historical_asof_certified':False,'context_coefficients':None,'trained_xg':False,
                      'venue_status':'EXPLICIT_MISSING_INDICATOR_NOT_VERIFIED_HOME_ADVANTAGE'}
            path=folder/(role+'-artifact.json');write(path,artifact)
            artifacts[role]={'path':str(path.resolve()),'sha256':sha(path)}
            write(folder/(role+'-evaluation-core.json'),evaluation_core)
        report={'cohort':cohort,'total_n':len(history),'fit_n':len(prior),'holdout_n':len(targets),
                'fit_end':prior[-1]['date'],'holdout_start':targets[0]['date'],'metrics':metrics,
                'predictions':predictions,'formal_acceptance_passed':False,
                'blocking_reasons':['NO_PROSPECTIVE_FIRST_FREEZE_VALIDATION','NO_INDEPENDENT_SOURCE_COMPLETENESS_CERTIFICATION','VENUE_UNVERIFIED','NO_REAL_MARKET_LINE_AUDIT']}
        if len(targets)<PROTOCOL['minimum_acceptance_sample']:report['blocking_reasons'].append('INSUFFICIENT_HOLDOUT_SAMPLE')
        write(folder/'diagnostic.json',report)
        index['routes'][cohort]={'artifacts':artifacts,'report_path':str((folder/'diagnostic.json').resolve()),
                                 'n':len(history),'holdout_n':len(targets),'formal_acceptance_passed':False}
    write(out/'candidates.json',index);return index

def predict(path,fixture):
    artifact=json.loads(Path(path).read_text())
    if artifact.get('schema')!='WCPP-JCUP-POISSON-1':raise ValueError('cup artifact schema required')
    for k in ('protocol','dataset','engine'):
        if sha(artifact[k+'_path'])!=artifact[k+'_sha256']:raise ValueError('candidate dependency changed')
    for k in ('cohort','era','stage','leg','tie_format'):
        if fixture.get(k)!=artifact.get(k):raise ValueError('exact edition/phase/leg candidate required')
    if fixture.get('competition')!='jpn.league_cup' or fixture.get('gender') not in ('men','male') or fixture.get('age')!='senior':raise ValueError('explicit senior-men League Cup fixture required')
    if any(k in fixture for k in ('home_goals','away_goals','actual','result')):raise ValueError('target labels prohibited')
    if fixture['date']<=artifact['core']['fit_end']:raise ValueError('target before training end')
    if fixture.get('neutral') not in (True,False,None):raise ValueError('three-state venue required')
    m,mu=distribution(artifact['core'],fixture)
    mode=[int(v) for v in np.unravel_index(m.argmax(),m.shape)]
    return {'version':artifact['version'],'status':'RESEARCH_DIAGNOSTIC_NOT_VERIFIED_PREMATCH',
            'matrix':m.tolist(),'matrix_sha256':key(m.tolist()),'statistical_top1':mode,'expected_goals':mu,
            'formal_acceptance_passed':False,'formal_kpi_eligible':False,'comprehensive_primary_ready':False,
            'venue_unknown':fixture.get('neutral') is None}
