"""Settle the locked R28 comparisons and ranked seats from verified regulation results."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from research.analysis_freeze import aware
from . import goal_state_forward as ledger
from . import j1_process_forward as r28
from .paired_kpi_inference import compare
from .ranked_score_study import sweep
from .three_arm_review import _results


BEIJING = ZoneInfo('Asia/Shanghai')


def review(data, issued, result_state, *, at=None):
    now = aware(at) if at is not None else datetime.now(timezone.utc)
    original = r28.prepare(data)
    r28.verify_issue(original, issued)
    index = r28.read(issued / 'receipts.json')
    plan = ledger._read(issued, index['registration'])
    if any(aware(ref['recorded_at']) > now for ref in index['matches'].values()):
        raise ValueError('review time precedes the original research issues')
    fixtures = plan['payload']['fixtures']
    identity = plan['payload']['identity']
    state = result_state.resolve()
    if (state / 'normalized-results.jsonl').is_file():
        actuals, issues = _results(state, fixtures, now)
    else:
        actuals, issues = {}, {mid: ['normalized result archive missing'] for mid in fixtures}

    rows = []
    evidence = {}
    for mid, frozen in original['rows'].items():
        result = actuals.get(mid)
        actual = [result['home'], result['away']] if result else None
        day = aware(fixtures[mid]['kickoff_at']).astimezone(BEIJING).date().isoformat()
        rows.append(dict(id=mid, date=day, cohort=fixtures[mid]['cohort'], actual=actual,
                         line=frozen['line'], forecast=frozen['forecasts']))
        evidence[mid] = result['source'] if result else None
    comparisons = {key: compare(rows, baseline=arms[0], candidate=arms[1])
                   for key, arms in identity['pairs'].items()}
    scores = {mid: f"{r['home']}-{r['away']}" for mid, r in actuals.items()}
    selected = {}
    for arm, retained in identity['selected'].items():
        seats = [dict(match_id=mid, **original['rows'][mid]['forecasts'][arm]) for mid in retained]
        batch = sweep(seats, scores)
        selected[arm] = dict(matches=retained, seats=seats, **batch)
    return dict(schema='WCPP-R28-J1-FIRST-RESEARCH-REVIEW-1', at=now.isoformat(),
                original_registration=plan['freeze_id'], record_sha256=plan['record_sha256'],
                registered=len(fixtures), settled=len(actuals), pending=len(fixtures)-len(actuals),
                comparisons=comparisons, selected=selected, per_match=rows, result_evidence=evidence,
                result_issues=issues)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--issued', type=Path, required=True)
    parser.add_argument('--result-state', type=Path, required=True)
    parser.add_argument('--at')
    args = parser.parse_args()
    print(json.dumps(review(args.data, args.issued, args.result_state, at=args.at),
                     ensure_ascii=False, indent=2, allow_nan=False))
