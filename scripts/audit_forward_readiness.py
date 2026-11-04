"""Read-only upper-bound check; never calls this a historical backtest."""
import argparse
import collections
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from research_program.replay import eligible_labels


def audit(rows, evidence_root):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row['cohort']].append(row)
    result = []
    for cohort, group in sorted(groups.items()):
        counts, reasons = [], collections.Counter()
        for row in group:
            kickoff = datetime.fromisoformat(row['kickoff_at'].replace('Z', '+00:00'))
            if kickoff.tzinfo is None or kickoff.utcoffset() is None:
                raise ValueError('timezone-aware kickoff required')
            target = {'match_id':row['match_id'], 'cohort':cohort, 'stage':row['stage'],
                      'cutoff_at':(kickoff - timedelta(seconds=1)).isoformat()}
            accepted, excluded = eligible_labels(group, target, evidence_root)
            counts.append(len(accepted)); reasons.update(excluded)
        result.append({'cohort':cohort, 'targets':len(group),
                       'targets_with_any_certified_prior_labels':sum(n > 0 for n in counts),
                       'maximum_eligible_labels':max(counts, default=0),
                       'excluded_reason_counts':dict(reasons)})
    return {'fixture_cutoff_assumption':'hypothetical kickoff minus one second; optimistic upper bound, not a historical prediction',
            'results':result, 'predictions_made':0, 'historical_asof_certified':False,
            'scope_notice':'Only supplied rows are checked; other sources may contain additional eligible history.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', required=True)
    parser.add_argument('--evidence-root', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    path = Path(args.rows); raw = path.read_bytes(); result = audit(json.loads(raw), args.evidence_root)
    result['source_sha256'] = hashlib.sha256(raw).hexdigest()
    out = Path(args.out)
    with out.open('x', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(json.dumps({'cohorts':len(result['results']), 'targets':sum(x['targets'] for x in result['results']),
                      'predictions_made':0, 'out':str(out)}))


if __name__ == '__main__':main()
