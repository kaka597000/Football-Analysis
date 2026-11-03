"""Compare frozen comprehensive Primary with its actual referenced model pair."""
from datetime import datetime,timedelta,timezone
import re
import numpy as np
from research.analysis_freeze import verify,aware
from v2_test.data_contract import digest,public_url


def compare(entries,results):
    actual={};seen=set();rows=[];groups={};pending=[];pending_rows=[];selected=0;missing_pairs=0
    for result in results:
        if result['match_id'] in actual:raise ValueError('duplicate result')
        if result.get('status') not in ('completed','FT') and result.get('event_status')!='FT':
            raise ValueError('explicit completed result required')
        if result.get('score_basis')!='regulation_90':raise ValueError('regulation result required')
        public_url(result['source_url'])
        if any(type(result.get(k)) is not int or not 0<=result[k]<=40 for k in ('home_goals','away_goals')):raise ValueError('actual score')
        actual[result['match_id']]=result
    for entry in entries:
        comp=verify(entry['comprehensive']);fixture=comp['fixture'];mid=fixture['match_id']
        if mid in seen:raise ValueError('revisions cannot add another independent match')
        seen.add(mid)
        if comp['stage']!='prematch' or comp['track']!='comprehensive' or comp['evaluation_slot']!='FIRST_PREMATCH':
            raise ValueError('default first comprehensive prematch record required')
        kickoff=aware(fixture['kickoff_at'])
        if not aware(fixture['cutoff_at'])<=aware(comp['recorded_at'])<kickoff:raise ValueError('late comprehensive freeze')
        primary=comp['payload'].get('primary')
        if primary is not None and (not isinstance(primary,str) or not re.fullmatch(r'\d{1,2}-\d{1,2}',primary)):
            raise ValueError('unique frozen score or explicit abstention required')
        selected+=primary is not None
        result=actual.get(mid);score=None
        if result is not None:
            observed=aware(result['observed_at'])
            if not kickoff+timedelta(minutes=90)<=observed<=datetime.now(timezone.utc):raise ValueError('result capture time')
            score=f"{result['home_goals']}-{result['away_goals']}"
        row={'match_id':mid,'cohort':fixture['cohort'],'actual':score,'comprehensive_primary':primary,
             'comprehensive_hit':None if primary is None or score is None else primary==score,'models':{}}
        pair=entry.get('pair')
        if pair is None:missing_pairs+=1
        else:
            pair=verify(pair);reference=comp['payload'].get('current_test_reference') or {}
            if pair['freeze_id']!=reference.get('freeze_id') or pair['record_sha256']!=reference.get('sha256'):
                raise ValueError('actual frozen comprehensive pair reference required')
            if (pair['stage']!='prematch' or pair['track']!='current-test-pair'
                    or pair['fixture']['match_id']!=mid or pair['fixture']['cohort']!=fixture['cohort']
                    or aware(pair['fixture']['kickoff_at'])!=kickoff
                    or not aware(pair['fixture']['cutoff_at'])<=aware(pair['recorded_at'])<=aware(comp['recorded_at'])<kickoff):
                raise ValueError('pair chronology/identity')
            for name in ('current_model','test_model'):
                prediction=pair['payload'][name];matrix=prediction.get('matrix');stat=None
                if matrix is not None:
                    m=np.asarray(matrix,float)
                    if (m.ndim!=2 or not np.isfinite(m).all() or (m<0).any()
                            or not np.isclose(m.sum(),1.) or digest(matrix)!=prediction['matrix_hash']):
                        raise ValueError('frozen matrix integrity')
                    mode=np.unravel_index(m.argmax(),m.shape);stat=f'{mode[0]}-{mode[1]}'
                    if prediction['primary']!=stat:raise ValueError('statistical Primary does not match frozen matrix mode')
                key=name+':'+prediction['version']
                values=groups.setdefault(key,{'population':0,'settled':0,'pending':0,'model_available':0,
                    'comprehensive_selected':0,'comprehensive_abstained':0,'paired_n':0,
                    'statistical_hits_all_settled':0,'statistical_hits':0,'comprehensive_hits':0,
                    'changed_right':0,'changed_wrong':0,'unchanged_or_both_wrong':0})
                values['population']+=1;values['pending']+=result is None;values['settled']+=result is not None
                values['model_available']+=stat is not None;values['comprehensive_selected']+=primary is not None
                values['comprehensive_abstained']+=primary is None
                values['statistical_hits_all_settled']+=stat is not None and score is not None and stat==score
                if primary is not None and stat is not None and score is not None:
                    a,b=stat==score,primary==score
                    values['paired_n']+=1;values['statistical_hits']+=a;values['comprehensive_hits']+=b
                    values['changed_right']+=b and not a;values['changed_wrong']+=a and not b
                    values['unchanged_or_both_wrong']+=a==b
                row['models'][name]={'version':prediction['version'],'statistical_primary':stat,
                    'statistical_hit':None if stat is None or score is None else stat==score,
                    'pair_evaluation_slot':pair['evaluation_slot']}
        if result is None:pending.append(mid);pending_rows.append(row)
        else:rows.append(row)
    for group in groups.values():
        group['net_hits']=group['changed_right']-group['changed_wrong']
        group['model_coverage']=group['model_available']/group['population']
        group['comprehensive_coverage']=group['comprehensive_selected']/group['population']
        group['settled_pair_coverage']=group['paired_n']/group['settled'] if group['settled'] else None
    return {'schema':'WCPP-FROZEN-PRIMARY-NET-REVIEW-1','population':len(entries),'settled':len(rows),
            'pending':pending,'pending_rows':pending_rows,'missing_pair_population':missing_pairs,
            'comprehensive_selected':selected,'primary_coverage':selected/len(entries) if entries else 0.,
            'comprehensive_settled_n':sum(r['comprehensive_hit'] is not None for r in rows),
            'comprehensive_hits':sum(r['comprehensive_hit'] is True for r in rows),'by_model_version':groups,'rows':rows,
            'scope':'FIRST_COMPREHENSIVE_VS_ITS_REFERENCED_PAIR; pair revision explicitly shown; not replacing first-model KPI',
            'selection_model_trained':False,'retrospective_choice_of_best_version':False,'prediction_modified':False}
