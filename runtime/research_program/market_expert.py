"""Independent as-of market probability baseline; prices are not trading volume."""
from datetime import datetime,timezone
import numpy as np
from competition_scope import require
from v2_test.data_contract import aware,digest,validate


def baseline(rows,target,evidence_root,*,bookmaker,max_age_seconds=60,now=None):
    require(target);now=now or datetime.now(timezone.utc)
    cutoff,kickoff=aware(target['cutoff_at']),aware(target['kickoff_at'])
    if cutoff>=kickoff or cutoff>now or max_age_seconds<=0 or not target.get('stage'):
        raise ValueError('explicit valid target cutoff, phase and quote age required')
    groups={};excluded={};seen=set()
    def reject(reason):excluded[reason]=excluded.get(reason,0)+1
    for row in rows:
        captured=validate(row,evidence_root,now=now)
        if row['field']!='market_quote':reject('not_market');continue
        if digest(row) in seen:raise ValueError('duplicate observation')
        seen.add(digest(row));quote=row['value']
        if (row['cohort']!=target['cohort'] or row.get('stage')!=target['stage']
                or row['match_id']!=target['match_id'] or aware(row['event_kickoff_at'])!=kickoff):
            reject('identity_or_phase');continue
        if captured>cutoff or aware(quote['quoted_at'])>cutoff:reject('unavailable_at_cutoff');continue
        if quote['bookmaker']!=bookmaker:reject('other_bookmaker');continue
        key=(row['provider'],quote['bookmaker'],quote['market'],quote.get('line'))
        group=groups.setdefault(key,{})
        quote_time=aware(quote['quoted_at'])
        previous=group.get(quote_time)
        if previous and previous['value']['odds']!=quote['odds']:
            raise ValueError('conflicting same-book same-line source quote')
        group[quote_time]=row
    comparisons=[];h2h=[]
    for key,group in groups.items():
        ordered=sorted(group.values(),key=lambda r:aware(r['value']['quoted_at']))
        first,last=ordered[0]['value'],ordered[-1]['value']
        age=(cutoff-aware(last['quoted_at'])).total_seconds()
        comparisons.append({'provider':key[0],'bookmaker':key[1],'market':key[2],'line':key[3],
                            'snapshots':len(ordered),'first_quoted_at':first['quoted_at'],'last_quoted_at':last['quoted_at'],
                            'first_odds':first['odds'],'last_odds':last['odds'],
                            'price_change':{s:last['odds'][s]-first['odds'][s] for s in first['odds']} if len(ordered)>1 else None,
                            'current_at_cutoff':0<=age<=max_age_seconds})
        if key[2]=='h2h' and 0<=age<=max_age_seconds:
            inverse=np.array([1/last['odds'][s] for s in ('home','draw','away')])
            h2h.append({'provider':key[0],'probabilities':(inverse/inverse.sum()).tolist(),
                        'method':'PROPORTIONAL_DEVIG_UNTRAINED_BASELINE',
                        'overround':float(inverse.sum()-1),'quoted_at':last['quoted_at']})
    # Independent providers are not silently averaged or selected after results.
    if len(h2h)>1:raise ValueError('predeclare one provider per market expert')
    return {'schema':'WCPP-MARKET-EXPERT-BASELINE-1','match_id':target['match_id'],
            'status':'WDL_BASELINE_AVAILABLE' if h2h else 'NO_VERIFIED_CURRENT_H2H',
            'wdl':h2h[0] if h2h else None,'comparisons':comparisons,'excluded':excluded,
            'score_matrix':None,'score_model_trained':False,'goal_line_probabilities_inferred':False,
            'flow_or_volume_claimed':False,'prediction_modified':False,
            'notice':'Same-provider/book/line captured prices only. Totals with push/half settlement are not binary WDL or inferred score probabilities.'}


def paired_wdl(rows):
    seen=set();pairs=[];missing=0
    for row in rows:
        if row['match_id'] in seen:raise ValueError('duplicate match')
        seen.add(row['match_id'])
        if row.get('market_wdl') is None or row.get('current_wdl') is None:missing+=1;continue
        actual=row['actual_wdl']
        if type(actual) is not int or actual not in (0,1,2):raise ValueError('verified WDL class required')
        values={}
        for key in ('market_wdl','current_wdl'):
            p=np.asarray(row[key],float)
            if p.shape!=(3,) or not np.isfinite(p).all() or (p<0).any() or not np.isclose(p.sum(),1):raise ValueError('probability vector')
            values[key]={'hit':int(p.argmax())==actual,'brier':float(np.square(p-np.eye(3)[actual]).sum()),
                         'log_loss':float(-np.log(max(p[actual],1e-15)))}
        pairs.append(values)
    return {'population':len(rows),'paired_n':len(pairs),'missing':missing,
            'coverage':len(pairs)/len(rows) if rows else 0.,
            'net_wdl_hits_market_vs_current':sum(int(r['market_wdl']['hit'])-int(r['current_wdl']['hit']) for r in pairs),
            'metrics':{key:{metric:float(np.mean([r[key][metric] for r in pairs])) if pairs else None for metric in ('hit','brier','log_loss')} for key in ('market_wdl','current_wdl')},
            'historical_asof_certification':'INPUT_PROVENANCE_MUST_BE_VERIFIED_SEPARATELY',
            'incremental_score_value_proven':False,'promotion':'NEVER_AUTOMATIC'}
