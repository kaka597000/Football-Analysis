"""Forward-only comparator using displayed exact-score quotes, then available same-match market heads."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path

from research.analysis_freeze import DEFAULT_ROOT,aware
from . import goal_state_forward as ledger
from .goal_state_study import sha,write_new

TRACK='all-scope-market-priority-20261123'


def run(source,direct,out,freeze_root=DEFAULT_ROOT):
    def records(folder):
        index=json.loads((folder/'receipts.json').read_bytes())
        parent=ledger._read(folder,index['registration'])
        matches={mid:ledger._read(folder,ref) for mid,ref in index['matches'].items()}
        if (set(matches)!=set(parent['payload']['fixtures']) or any(r['payload']['registration_sha256']!=parent['record_sha256'] for r in matches.values())):
            raise ValueError('first-issue population differs from registration')
        return parent,matches
    reg,base=records(source)
    direct_reg,quotes=records(direct)
    identity=dict(competitions=reg['payload']['identity']['competitions'],source_registration=reg['record_sha256'],
                  correct_score_registration=direct_reg['record_sha256'],source_receipts_sha256=sha(source/'receipts.json'),
                  pairs={'market_priority_vs_corrected':['corrected','market_priority'],
                         'displayed_vs_fallback':['fallback','market_priority']})
    plan=ledger.register([r['fixture'] for r in base.values()],identity,TRACK,out,freeze_root=freeze_root)
    receipts={};usage=Counter()
    for mid,record in base.items():
        f=dict(record['fixture'],cutoff_at=datetime.now(timezone.utc).isoformat())
        original=record['payload'];forecast=original['forecasts'];model=forecast['neighbor'];market=forecast['market']
        fallback=dict(model)
        for k in ('primary','top3','wdl','total_side'):
            if market.get(k) is not None:
                fallback[k]=market[k]
        pred=dict(fallback);source_detail=None
        quoted=quotes.get(mid)
        if quoted and quoted['payload']['forecasts'].get('direct',{}).get('primary'):
            qf=quoted['fixture']
            if (any(qf[k]!=f[k] for k in ('home','away','match_id','cohort')) or
                    aware(qf['kickoff_at'])!=aware(f['kickoff_at']) or aware(quoted['recorded_at'])>aware(f['cutoff_at'])):
                raise ValueError('direct price selection identity mismatch')
            heads=quoted['payload']['forecasts']['direct']
            if len(set(heads['top3']))!=3 or heads['primary']!=heads['top3'][0]:
                raise ValueError('direct score ranks invalid')
            pred.update(primary=heads['primary'],top3=heads['top3'])
            source_detail=quoted['record_sha256'];mode='displayed_correct_score'
        else:
            mode='market_implied_scores' if market.get('top3') else 'locked_competition_model'
        usage[mode]+=1
        payload=dict(line=original['line'],forecasts=dict(corrected=model,fallback=fallback,market_priority=pred),
                     score_source=mode,price_receipt=source_detail,input_receipt=record['record_sha256'])
        receipts[mid]=ledger.issue(plan,f,payload,TRACK,out,freeze_root=freeze_root)
    write_new(out/'receipts.json',dict(registration=plan,matches=receipts))
    write_new(out/'coverage.json',dict(usage))


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    for n in ('source','direct','out'):cli.add_argument('--'+n,type=Path,required=True)
    cli.add_argument('--freeze-root',type=Path,default=DEFAULT_ROOT)
    run(**vars(cli.parse_args()))
