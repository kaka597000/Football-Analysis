"""Deduplicate certified regulation results for a sealed native roster."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from research.analysis_freeze import aware
from study import indexed, observed_score, packed, sha, write


def join(inputs, lockpath, paths, out):
    lock = json.loads(lockpath.read_bytes())
    forecast_path = lockpath.parent/'forecasts.jsonl'
    if lock['panel'] != 'native' or sha(inputs) != lock['input_sha256'] or sha(forecast_path) != lock['forecasts_sha256']:
        raise ValueError('expected the sealed native roster')
    roster = indexed(inputs)
    eligible = {k for k,r in indexed(forecast_path).items() if r.get('forecasts')}
    results = defaultdict(list)
    read = Counter()
    for i,path in enumerate(paths):
        for mid,label in indexed(path).items():
            if mid not in eligible:
                raise ValueError('result source contains a fixture without a sealed forecast')
            if observed_score(roster[mid],label,'native',False) is None:
                raise ValueError('certified source returned an empty score')
            stamp = aware(label['observed_at'])
            if stamp >= aware(lock['result_cutoff_at']):
                raise ValueError('result source is later than the evidence snapshot')
            record = {k:label[k] for k in ('id','home_id','away_id','kickoff_at','score','observed_at','evidence_sha256')}
            results[mid].append(record)
            read[i] += 1
    selected = []
    for mid, group in sorted(results.items()):
        if len({tuple(r['score']) for r in group}) != 1:
            raise ValueError('conflicting certified regulation results for '+mid)
        selected.append(min(group,key=lambda r:(aware(r['observed_at']),r['evidence_sha256'],packed(r))))
    write(out,b''.join(packed(row) for row in selected))
    return dict(eligible=len(eligible), unique=len(selected), missing=len(eligible)-len(selected),
                source_rows=dict(read), sha256=sha(out))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','lock','out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('label_files', type=Path, nargs='+')
    a=p.parse_args()
    print(json.dumps(join(a.inputs,a.lock,a.label_files,a.out),sort_keys=True))
