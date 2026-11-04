"""Append a review of every preregistered cohort, preserving pending fixtures.

This runs existing evidence gates; it neither polls providers nor invents results.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
from research_program.future_validation import read, review, sha, write_new


def run(plan_receipts, packages_path, results_path, out, evidence_root=None):
    receipts = read(plan_receipts)
    packages, results = read(packages_path), read(results_path)
    reviews = []
    identities = {}
    for receipt in receipts['plans']:
        plan = read(receipt['path'])
        payload = plan['payload']
        ids = set(payload['eligible_match_ids'])
        matching = [p for p in packages if p['match_id'] in ids]
        labels = [r for r in results if r['match_id'] in ids]
        value = review(plan, matching, labels, evidence_root)
        reviews.append(value)
        for fixture in payload['fixtures_identity']:
            identities[fixture['match_id']] = fixture
    value = {'schema': 'WCPP-FUTURE-POPULATION-STATUS-1',
             'reviewed_at': datetime.now(timezone.utc).isoformat(),
             'inputs_sha256': {str(Path(p).resolve()): sha(p) for p in (plan_receipts, packages_path, results_path)},
             'population': sum(r['population'] for r in reviews),
             'completed': sum(r['completed'] for r in reviews),
             'pending_results': [identities[mid] for r in reviews for mid in r['pending_results']],
             'missing_first_pairs': [mid for r in reviews for mid in r['missing_first_pairs']],
             'cohort_reviews': reviews, 'automatic_promotion': False,
             'notice': 'All cohorts reviewed; pending labels and uncaptured fixtures retained. No scheduler or result acquisition implied.'}
    write_new(out, value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('plan-receipts', 'packages', 'results', 'out'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--evidence-root')
    a = p.parse_args()
    run(a.plan_receipts, a.packages, a.results, a.out, a.evidence_root)


if __name__ == '__main__':
    main()
