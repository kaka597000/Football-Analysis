"""Research forecast accounting; no backdating or formal Champion KPI credit."""
import math
from datetime import datetime, timezone, timedelta
from main_model import VERSION, aware, digest, url


def interval(hits,n):
    if not n: return None
    z=1.959963984540054; p=hits/n; den=1+z*z/n
    mid=(p+z*z/(2*n))/den; half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,mid-half),min(1,mid+half)]


def evaluate(freezes,results,population):
    if type(population) is not int or population < len(freezes): raise ValueError('invalid full fixture population')
    by_id={}; seen=set(); rows=[]; pending=[]; totals={k:0 for k in ('win','half_win','push','half_loss','loss')}
    now=datetime.now(timezone.utc)
    for r in results:
        if r['match_id'] in by_id: raise ValueError('duplicate result')
        by_id[r['match_id']]=r
    for f in freezes:
        mid=f['input']['match_id']; kickoff=aware(f['input']['kickoff_at'])
        if mid in seen: raise ValueError('duplicate forecast')
        seen.add(mid)
        if f.get('version') != VERSION or f.get('freeze_status') != 'NEW_MAIN_RESEARCH_FREEZE': raise ValueError('invalid forecast')
        if digest({k:v for k,v in f.items() if k!='record_hash'}) != f.get('record_hash'): raise ValueError('tampered forecast')
        if not aware(f['input']['cutoff_at']) <= aware(f['generated_at']) <= aware(f['frozen_at']) < kickoff:
            raise ValueError('invalid forecast chronology')
        if aware(f['frozen_at']) > now: raise ValueError('future freeze')
        r=by_id.get(mid)
        if r is None: pending.append(mid); continue
        if r.get('score_basis') != 'regulation_90' or not url(r.get('source_url')): raise ValueError('90-minute sourced result required')
        if not kickoff+timedelta(minutes=90) <= aware(r['observed_at']) <= now: raise ValueError('invalid result timing')
        h,a=r['home_goals'],r['away_goals']
        if any(type(g) is not int or not 0 <= g <= 30 for g in (h,a)): raise ValueError('invalid result goals')
        actual='主胜' if h>a else '平局' if h==a else '客胜'
        settlement=None
        if f['total']:
            line=f['total']['quote']['line']; legs=[line] if line*2==int(line*2) else [line-.25,line+.25]
            net=sum(1 if h+a>leg else -1 if h+a<leg else 0 for leg in legs)/len(legs)
            if f['total']['reference_direction']=='under': net=-net
            settlement={1:'win',.5:'half_win',0:'push',-.5:'half_loss',-1:'loss'}[net]; totals[settlement]+=1
        rows.append({'match_id':mid,'primary_hit':f['primary']==f'{h}-{a}',
                     'wdl_hit':f['wdl_reference']==actual,'total_settlement':settlement})
    n=len(rows); sh=sum(r['primary_hit'] for r in rows); wh=sum(r['wdl_hit'] for r in rows)
    decisive=totals['win']+totals['loss']
    return {'version':VERSION,'research_only':True,'formal_kpi_added':0,'population':population,
            'frozen':len(freezes),'coverage':len(freezes)/population if population else None,'pending':pending,'settled':n,
            'primary':{'hits':sh,'n':n,'accuracy':sh/n if n else None,'wilson95':interval(sh,n)},
            'wdl':{'hits':wh,'n':n,'accuracy':wh/n if n else None,'wilson95':interval(wh,n)},
            'total':{'quoted_settled':sum(totals.values()),'settlements':totals,
                     'full_win_accuracy_excluding_push_and_half':totals['win']/decisive if decisive else None,
                     'n_full_win_or_loss':decisive,'wilson95':interval(totals['win'],decisive)},
            'rows':rows,'target_attainment_proven':False}
