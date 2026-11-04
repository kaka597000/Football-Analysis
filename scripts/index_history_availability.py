"""Index recorded capture bounds without inventing first-known timestamps."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def aware(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None or d.utcoffset() is None:raise ValueError('timezone required')
    return d


def main():
    p=argparse.ArgumentParser();p.add_argument('--histories',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    records=[];groups=[];files=sorted(Path(a.histories).glob('*/merged-history.json'))
    if not files:raise ValueError('no history inputs')
    for path in files:
        raw=path.read_bytes();rows=json.loads(raw);co=path.parent.name;keys=set();group=[]
        for r in rows:
            if r['cohort']!=co or r['match_id'] in keys:raise ValueError('scope or identity conflict')
            keys.add(r['match_id'])
            times=[r[k] for k in ('observed_at','query_time') if r.get(k)]
            for k in ('evidence','score_evidence','neutral_evidence','api_evidence'):
                if isinstance(r.get(k),dict) and r[k].get('query_time'):times.append(r[k]['query_time'])
            bound=max(times,key=aware) if times else None
            first=r.get('first_known_at')
            if first:aware(first)
            group.append({'cohort':co,'stage':r.get('stage'), 'match_id':r['match_id'],
                          'match_date':r['date'],'recorded_capture_bound':bound,
                          'first_known_at':first,'neutral':r.get('neutral')})
        records.extend(group)
        groups.append({'cohort':co,'rows':len(group),'history_sha256':hashlib.sha256(raw).hexdigest(),
                       'capture_missing':sum(r['recorded_capture_bound'] is None for r in group),
                       'first_known_missing':sum(r['first_known_at'] is None for r in group)})
    with Path(a.out).open('x',encoding='utf-8') as f:
        json.dump({'records':records,'groups':groups,'notice':'Latest recorded evidence capture is not proof of historical first-known time. This index does not independently verify response provenance.'},f,ensure_ascii=False,indent=2)
    print(json.dumps({'rows':len(records),'cohorts':len(groups),'out':a.out}))


if __name__=='__main__':main()
