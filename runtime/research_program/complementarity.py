"""Describe same-match frozen expert differences without fitting fusion weights."""
from datetime import datetime,timedelta,timezone
import math
import re
import numpy as np
from research.analysis_freeze import verify,aware
from v2_test.data_contract import digest,public_url
from v2_test.evaluation import evaluate


def compare(pairs,results):
    actual={};seen=set();fixture_keys=set();groups={}
    for result in results:
        mid=result['match_id']
        if mid in actual:raise ValueError('duplicate result')
        if result.get('status') not in ('completed','FT') or result.get('score_basis')!='regulation_90':
            raise ValueError('explicit completed regulation result required')
        public_url(result['source_url'])
        if any(type(result.get(k)) is not int or not 0<=result[k]<=40 for k in ('home_goals','away_goals')):
            raise ValueError('invalid regulation score')
        actual[mid]=result
    for item in pairs:
        record=verify(item);fixture=record['fixture'];mid=fixture['match_id']
        if mid in seen:raise ValueError('revisions cannot inflate population')
        seen.add(mid)
        if record['track']!='current-test-pair' or record['stage']!='prematch' or record['evaluation_slot']!='FIRST_PREMATCH':
            raise ValueError('first frozen prematch pair required')
        kickoff=aware(fixture['kickoff_at'])
        fixture_key=(fixture['cohort'],fixture.get('stage'),kickoff,fixture['home'],fixture['away'])
        if fixture_key in fixture_keys:raise ValueError('duplicate fixture under different IDs')
        fixture_keys.add(fixture_key)
        if not aware(fixture['cutoff_at'])<=aware(record['recorded_at'])<kickoff:
            raise ValueError('late pair freeze')
        models=[record['payload'][key] for key in ('current_model','test_model')]
        identities=[]
        for role,p in zip(('current','test'),models):
            artifact=record['payload'].get('execution_audit',{}).get(role,{}).get('artifact_sha256')
            if p.get('matrix') is not None and (not isinstance(artifact,str) or not re.fullmatch('[0-9a-f]{64}',artifact)):
                raise ValueError('frozen artifact identity required for available matrix')
            identities.extend([p.get('version') or 'UNAVAILABLE',artifact or 'UNAVAILABLE'])
        key=(fixture['cohort'],fixture.get('stage') or 'UNKNOWN_PHASE',*identities)
        g=groups.setdefault(key,{'population':0,'settled':0,'pending':0,'available':[0,0],
            'both_available':0,'same_primary':0,'different_primary':0,'settled_both_available':0,
            'only_current_right':0,'only_test_right':0,'both_right':0,'both_wrong':0,
            'evaluation_rows':[[],[]],'paired_probability_rows':[[],[]],'matrix_available':[0,0],'settled_available':[0,0],'hits':[0,0]})
        g['population']+=1;scores=[];probability_rows=[]
        result=actual.get(mid)
        if result:
            if not kickoff+timedelta(minutes=90)<=aware(result['observed_at'])<=datetime.now(timezone.utc):
                raise ValueError('invalid result capture chronology')
            if result.get('kickoff_at') and aware(result['kickoff_at'])!=kickoff:
                raise ValueError('result kickoff identity')
        g['pending']+=result is None;g['settled']+=result is not None
        for i,p in enumerate(models):
            primary=p.get('primary');scores.append(primary)
            if p.get('matrix') is None and primary is None and p.get('status','').startswith('ABSTAIN'):
                probability_rows.append(None)
                continue  # Frozen execution failure remains in every population denominator.
            if p.get('input')!=fixture:raise ValueError('same actual input required')
            if not aware(fixture['cutoff_at'])<=aware(p['generated_at'])<=aware(record['recorded_at']):
                raise ValueError('prediction chronology')
            evaluation_row=None
            if p.get('matrix') is not None:
                m=np.asarray(p['matrix'],float)
                if m.ndim!=2 or not np.isfinite(m).all() or (m<0).any() or not np.isclose(m.sum(),1.,atol=1e-9,rtol=0) or digest(p['matrix'])!=p['matrix_hash']:
                    raise ValueError('matrix integrity')
                g['matrix_available'][i]+=1
                if result:evaluation_row={'matrix':p['matrix'],'home_goals':result['home_goals'],'away_goals':result['away_goals']}
            if primary is not None:
                if not isinstance(primary,str) or not re.fullmatch(r'\d{1,2}-\d{1,2}',primary):raise ValueError('unique Primary required')
                ph,pa=map(int,primary.split('-'))
                if primary!=f'{ph}-{pa}':raise ValueError('canonical Primary required')
                if p.get('matrix') is None or ph>=m.shape[0] or pa>=m.shape[1]:raise ValueError('Primary support')
                g['available'][i]+=1
                if evaluation_row is not None:evaluation_row['primary_score']=[ph,pa]
            if result:
                h,a=result['home_goals'],result['away_goals']
                g['settled_available'][i]+=primary is not None;g['hits'][i]+=primary==f'{h}-{a}'
                if evaluation_row is not None:g['evaluation_rows'][i].append(evaluation_row)
            probability_rows.append(evaluation_row)
        if all(row is not None for row in probability_rows):
            for i,row in enumerate(probability_rows):g['paired_probability_rows'][i].append(row)
        if all(score is not None for score in scores):
            g['both_available']+=1;g['same_primary']+=scores[0]==scores[1];g['different_primary']+=scores[0]!=scores[1]
            if result:
                g['settled_both_available']+=1;score=f"{result['home_goals']}-{result['away_goals']}"
                a,b=[s==score for s in scores]
                g['only_current_right']+=a and not b;g['only_test_right']+=b and not a
                g['both_right']+=a and b;g['both_wrong']+=not a and not b
    output=[]
    for key,g in sorted(groups.items()):
        rows=g.pop('evaluation_rows');paired_rows=g.pop('paired_probability_rows');n=g['settled_both_available'];net=g['only_test_right']-g['only_current_right']
        # Distribution-free bound for independent match-level differences in [-1,1].
        radius=math.sqrt(2*math.log(40)/n) if n else None
        g.update(cohort=key[0],stage=key[1],current_version=key[2],current_artifact_sha256=key[3],test_version=key[4],test_artifact_sha256=key[5],paired_net_test_hits=net,all_settled_net_test_hits=g['hits'][1]-g['hits'][0],
            same_primary_rate=g['same_primary']/g['both_available'] if g['both_available'] else None,
            coverage=[v/g['population'] for v in g['available']],
            full_settled_accuracy=[v/g['settled'] if g['settled'] else None for v in g['hits']],
            paired_hit_rate_delta=net/n if n else None,
            paired_delta_95pct_hoeffding=[max(-1.,net/n-radius),min(1.,net/n+radius)] if n else None,
            probability_metrics=[evaluate(r) if r else None for r in rows],
            paired_probability_n=len(paired_rows[0]),paired_probability_metrics=[evaluate(r) if r else None for r in paired_rows])
        output.append(g)
    return {'schema':'WCPP-FROZEN-COMPLEMENTARITY-1','population':len(seen),'groups':output,
        'fusion_fitted':False,'activation_modified':False,
        'notice':'Fixed first-freeze pairs grouped by cohort and exact versions. Supplied results require independent source verification. The interval assumes independent matches and is descriptive, not a promotion test. Disagreement or hindsight opportunity is not achievable fusion gain.'}
