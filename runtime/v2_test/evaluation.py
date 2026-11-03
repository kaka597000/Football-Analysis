"""Shared score-distribution diagnostics. No fitting, filtering or selection."""
import math
import numpy as np


def evaluate(rows):
    if not rows:raise ValueError('nonempty held-out rows required')
    stats=[];market=[];tails=[];bins=[[] for _ in range(10)];primary=[]
    for row in rows:
        m=np.asarray(row['matrix'],dtype=float);h,a=row['home_goals'],row['away_goals']
        if m.ndim!=2 or type(h) is not int or type(a) is not int or min(h,a)<0 or h>=m.shape[0] or a>=m.shape[1]:raise ValueError('score support')
        if not np.isfinite(m).all() or (m<0).any() or not np.isclose(m.sum(),1,atol=1e-9,rtol=0):raise ValueError('probability matrix')
        flat=m.ravel();target=h*m.shape[1]+a;order=np.argsort(-flat,kind='stable');rank=int(np.where(order==target)[0][0])+1
        score_brier=float(np.square(flat).sum()-2*flat[target]+1)
        wdl=np.array([np.tril(m,-1).sum(),np.trace(m),np.triu(m,1).sum()]);wdl_y=0 if h>a else 1 if h==a else 2;one=np.eye(3)[wdl_y]
        stats.append([rank<=1,rank<=3,rank<=5,-math.log(max(float(flat[target]),1e-15)),score_brier,float(np.square(wdl-one).sum()),float(np.square(np.cumsum(wdl)[:-1]-np.cumsum(one)[:-1]).sum()/2),int(wdl.argmax())==wdl_y])
        conf=float(flat[order[0]]);bins[min(int(conf*10),9)].append((conf,rank==1))
        hg,ag=np.indices(m.shape)
        for name,mask,event in [('home3+',hg>=3,h>=3),('home4+',hg>=4,h>=4),('away3+',ag>=3,a>=3),('away4+',ag>=4,a>=4),('total4+',hg+ag>=4,h+a>=4),('total5+',hg+ag>=5,h+a>=5)]:
            p=float(m[mask].sum());tails.append((name,p,bool(event)))
        if row.get('primary_score') is not None:
            score=row['primary_score']
            if not isinstance(score,list) or len(score)!=2 or any(type(x) is not int or x<0 for x in score):raise ValueError('primary')
            primary.append((score==[h,a],rank==1))
        if row.get('market_wdl') is not None:
            p=np.asarray(row['market_wdl'],dtype=float)
            if p.shape!=(3,) or not np.isfinite(p).all() or (p<0).any() or not np.isclose(p.sum(),1,atol=1e-9,rtol=0):raise ValueError('market probabilities')
            market.append({'model_brier':stats[-1][5],'market_brier':float(np.square(p-one).sum())})
    columns=['exact_top1','exact_top3','exact_top5','score_log_loss','score_brier','wdl_brier','wdl_rps','wdl_accuracy']
    result={'n':len(rows),'metrics':dict(zip(columns,map(float,np.mean(stats,axis=0)))),
       'calibration_bins':[{'n':len(b),'mean_probability':float(np.mean([x[0] for x in b])),'hit_rate':float(np.mean([x[1] for x in b]))} for b in bins if b],
       'primary':{'n':len(primary),'hits':sum(x[0] for x in primary),'coverage':len(primary)/len(rows),'accuracy':sum(x[0] for x in primary)/len(primary) if primary else None,'full_population_accuracy':sum(x[0] for x in primary)/len(rows),'net_hits_vs_probability_top1_same_subset':sum(int(x[0])-int(x[1]) for x in primary)},
       'market_paired_n':len(market),'market_paired_brier':{k:float(np.mean([r[k] for r in market])) for k in ['model_brier','market_brier']} if market else None,
       'tail':{},'notice':'Inputs must already be time-held-out, source-verified first-freeze records; this function cannot certify their provenance. No filtered subset replaces full-population metrics. Total-line five-way settlement uses existing paired_review, not a guessed line.'}
    for name in sorted(set(x[0] for x in tails)):
        r=[x for x in tails if x[0]==name];positives=sum(x[2] for x in r)
        result['tail'][name]={'brier':sum((x[1]-x[2])**2 for x in r)/len(r),'observed_events':positives,'recall_at_preregistered_half':sum(x[2] and x[1]>=.5 for x in r)/positives if positives else None}
    return result
