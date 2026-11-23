"""Run an isolated candidate and market control and freeze the first prospective batch."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE/'runtime'), str(HERE/'runtime/v5'), str(HERE)]
from research.analysis_freeze import digest, verify
from research_program import kpi_score_candidate, kpi_validation, market_baseline
from research_program.future_validation import read, sha, write_new


def issue(data, artifact, out):
    out.mkdir(parents=True, exist_ok=True)
    candidate_id = {'version':artifact['version'], 'artifact_sha256':artifact['artifact_sha256']}
    baseline_id = {'version':market_baseline.VERSION,
                   'artifact_sha256':digest({'formula':sha(market_baseline.__file__),
                                            'method':market_baseline.METHOD})}
    batch = kpi_validation.register(data, candidate_id, baseline_id)
    plan = verify(read(batch['path']))
    receipts = {}
    for row in data:
        fixture = dict(row)
        index = fixture['match_id'].replace(':','-')
        saved = out/(index+'-receipt.json')
        if saved.is_file():
            receipt=read(saved)
            record=verify(read(receipt['path']))
            if record['record_sha256']!=receipt['sha256'] or record['payload']['plan_sha256']!=plan['record_sha256']:
                raise ValueError('existing prospective receipt does not match plan')
            receipts[row['match_id']]=receipt
            continue
        details=out/(index+'-forecast.json')
        if details.is_file():
            evidence=read(details)
            fixture=evidence['fixture']
        else:
            fixture['cutoff_at'] = datetime.now(timezone.utc).isoformat()
            candidate = kpi_score_candidate.predict(artifact, fixture)
            baseline = market_baseline.predict(fixture)
            evidence={'fixture':fixture,'candidate':candidate,'baseline':baseline,
                      'generated_at':datetime.now(timezone.utc).isoformat()}
            write_new(details,evidence)
        def selection(spec,value,total,wdl):
            return {**spec,'generated_at':evidence['generated_at'],'primary':value.get('primary'),
                    'top3':value.get('top3'),'wdl':wdl,'total_line':total.get('line'),
                    'total_side':total.get('direction'),'forecast_evidence_sha256':sha(details)}
        c,m=evidence['candidate'],evidence['baseline']
        frow=selection(candidate_id,c,c.get('total') or {},c.get('wdl'))
        mrow=selection(baseline_id,m,m.get('full_win_total') or {},m.get('lowest_price_1x2'))
        receipt=kpi_validation.capture(plan,fixture,frow,mrow)
        write_new(saved,receipt)
        receipts[row['match_id']]=receipt
    result={'register':batch,'matches':receipts}
    location=out/'receipts.json'
    if not location.exists():
        write_new(location,result)
    elif read(location)!=result:
        raise ValueError('output path conflicts with saved prospective batch')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures',required=True,type=Path)
    parser.add_argument('--artifact',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    a=read(args.artifact)
    result=issue(read(args.fixtures),a,args.out)
    print(result['register']['freeze_id'], len(result['matches']))
