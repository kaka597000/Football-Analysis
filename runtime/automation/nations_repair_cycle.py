"""Bounded result polling for an already frozen Nations repair population.

No fitting, new forecasts, market requests or promotion. Original frozen bytes
remain intact; result evidence and review runs are appended in a separate state.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ..nations_league import database, repair, repair_forward
from ..research.analysis_freeze import aware, digest, verify, save
from ..publication import atomic_json
from ..storage_lock import exclusive_lock
from .collector import ESPN, NoRedirect
from .result_queue import ResultQueue

ENDPOINT=ESPN+'uefa.nations/summary?event='


def fetch(url):
    if not url.startswith(ENDPOINT) or not url.removeprefix(ENDPOINT).isdigit():
        raise ValueError('fixed Nations summary endpoint required')
    with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'WCPP-research'}),timeout=8) as response:
        raw=response.read(8_000_001)
        if len(raw)>8_000_000:raise ValueError('response too large')
        return raw


def result_from_raw(fixture,path,url,observed_at):
    capture={'event_id':fixture['match_id'].removeprefix('espn:'),'source_kind':'summary',
             'raw_path':str(path),'raw_sha256':repair.sha(path),
             'source_url':url,'query_time':observed_at}
    _,facts=database.normalize_capture(capture,str(path.parent))
    if facts['status']!='STATUS_FULL_TIME':return None
    result={k:fixture[k] for k in ('match_id','cohort','stage','home','away','kickoff_at')}
    result.update(event_status='FT',score_basis='regulation_90',
                  home_goals=facts['home_90'],away_goals=facts['away_90'],
                  observed_at=observed_at,source_path=str(path),source_sha256=repair.sha(path),source_url=url)
    repair_forward.validate_result(result,fixture)
    return result


def resolve_results(state,rows):
    resolved=[]
    for row in rows:
        path=(state/row['source_path']).resolve()
        if not path.is_relative_to(state):raise ValueError('result raw path outside state')
        resolved.append({**row,'source_path':str(path)})
    return resolved


def _publish_review(bundle, state, plan, rows, assessment, polling):
    """Freeze the assessment once per completed evidence set. Log real polling separately."""
    from v5 import paired_review

    plan_hash = plan['record_sha256']
    assessment = dict(assessment, plan_sha256=plan_hash, mode='REVIEW_ONLY_NO_NEW_INFERENCE',
                      review_source_sha256=repair.sha(repair_forward.__file__),
                      paired_review_source_sha256=repair.sha(paired_review.__file__),
                      cycle_source_sha256=repair.sha(__file__))
    fixtures = plan['payload']['fixtures_identity']
    fixture = {'match_id': 'nations-repair-review:' + plan_hash,
               'kickoff_at': min((f['kickoff_at'] for f in fixtures), key=aware)}
    complete = not assessment['pending']
    key = None
    if complete:
        # The full review has just verified the raw scores and forecast bytes.
        # Bind those inputs too: a corrected source can produce identical metrics.
        forecast_hashes = {p.name: repair.sha(p) for p in (bundle / 'freezes').glob('*.json')}
        assessment['inputs_sha256'] = digest({'forecast_files': forecast_hashes,
                                              'results': sorted(rows, key=lambda r: r['match_id'])})
        fixture['cutoff_at'] = max((r['observed_at'] for r in rows), key=aware)
        payload = assessment
        key = 'completed:' + digest(payload)
    else:
        fixture['cutoff_at'] = polling['reviewed_at']
        payload = {**assessment, **polling}
    receipt = save(payload, fixture, stage='review', track='nations-repair-review',
                   root=state / 'freezes', idempotency_key=key)
    # The freeze store checks its chain on both first writes and retries. Using
    # its timestamp also recovers a missing run file after an interrupted write.
    stamp = aware(receipt['recorded_at']).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    run = state / 'runs' / (stamp + '.json')
    run.parent.mkdir(exist_ok=True)
    report = {**assessment, **polling}
    if complete and run.exists():
        previous = repair.read(run)
        if (previous['freeze_receipt']['sha256'] != receipt['sha256']
                or any(previous['report'].get(k) != v for k, v in assessment.items())):
            raise ValueError('completed run does not match verified freeze')
        publication = 'REUSED_COMPLETED_REVIEW'
    else:
        atomic_json(run, {'report': report, 'freeze_receipt': receipt})
        publication = 'RECORDED'
    return dict(report, review_publication=publication, review_freeze_id=receipt['freeze_id'])


def cycle(bundle,state,*,max_requests=6,max_seconds=40,fetcher=None,now=None):
    if not 0<=max_requests<=18 or not 8<=max_seconds<=50:
        raise ValueError('bounded request/time budget required')
    bundle=Path(bundle).resolve();state=Path(state).resolve();state.mkdir(parents=True,exist_ok=True)
    with exclusive_lock(state/'.cycle.lock'):
        started=time.monotonic();now=now or datetime.now(timezone.utc);fetcher=fetcher or fetch
        plan=verify(repair.read(bundle/'plan.json'))
        fixtures=plan['payload']['fixtures_identity']
        rows=repair.read(state/'results.json') if (state/'results.json').exists() else []
        initial_count = len(rows)
        # Verify all existing frozen and result evidence before transport.
        initial=repair_forward.review(bundle,resolve_results(state,rows))
        done={r['match_id'] for r in rows};polling=ResultQueue(state);calls=0;failures=[]
        due=[f for f in fixtures if f['match_id'] not in done and
             aware(f['kickoff_at'])+timedelta(hours=3)<=now]
        for fixture in polling.order(due):
            if calls>=max_requests or time.monotonic()-started>max_seconds-8:break
            mid=fixture['match_id'];polling.mark(mid);calls+=1
            url=ENDPOINT+mid.removeprefix('espn:')
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
            try:
                raw=fetcher(url)
                if not isinstance(raw,bytes):raise TypeError('raw HTTP bytes required')
                observed=datetime.now(timezone.utc).isoformat()
                path=state/'raw'/(mid.replace(':','-')+'-'+stamp+'.json');path.parent.mkdir(exist_ok=True)
                with path.open('xb') as handle:handle.write(raw)
                result=result_from_raw(fixture,path,url,observed)
                if result is None:continue
                portable={**result,'source_path':str(path.relative_to(state))}
                rows.append(portable);done.add(mid);atomic_json(state/'results.json',rows)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                failures.append({'match_id':mid,'status':'RESULT_NOT_CERTIFIED','error_type':type(exc).__name__})
                if isinstance(exc,HTTPError) and exc.code in (403,429):break
        assessment = repair_forward.review(bundle, resolve_results(state, rows)) if len(rows) != initial_count else initial
        poll = {'reviewed_at': datetime.now(timezone.utc).isoformat(), 'result_requests': calls,
                'attempted_ids': polling.attempted, 'failures': failures}
        return _publish_review(bundle, state, plan, rows, assessment, poll)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',required=True);p.add_argument('--state',required=True)
    p.add_argument('--max-requests',type=int,default=6);p.add_argument('--max-seconds',type=int,default=40)
    a=p.parse_args();r=cycle(a.bundle,a.state,max_requests=a.max_requests,max_seconds=a.max_seconds)
    print(json.dumps({k:r[k] for k in ('population','frozen','settled','pending','result_requests',
                                        'automatic_promotion','review_publication','review_freeze_id')}))


if __name__=='__main__':main()
