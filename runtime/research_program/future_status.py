"""Append a review of every preregistered cohort, preserving pending fixtures.

This runs existing evidence gates; it neither polls providers nor invents results.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
from research_program.future_validation import read, review, sha, write_new
from research.analysis_freeze import aware, verify
from research_program.future_identity import validate_plan_packages


def run(plan_receipts, packages_path, results_path, out, evidence_root=None):
    receipts = read(plan_receipts)
    packages, results = read(packages_path), read(results_path)
    reviews, plans = [], []
    identities, seen_plans, fixture_keys = {}, set(), set()
    for receipt in receipts['plans']:
        plan = verify(read(receipt['path']))
        if plan['record_sha256'] != receipt.get('sha256'):
            raise ValueError('plan receipt checksum mismatch')
        if plan.get('track') != 'research-preregistration' or plan.get('stage') != 'review':
            raise ValueError('preregistration plan receipt required')
        if plan['record_sha256'] in seen_plans:
            raise ValueError('duplicate plan receipt')
        seen_plans.add(plan['record_sha256'])
        payload = plan['payload']
        if ('protocol_sha256' in receipts
                and payload.get('protocol_sha256') != receipts['protocol_sha256']):
            raise ValueError('plan receipt protocol mismatch')
        ids = set(payload['eligible_match_ids'])
        population = payload['fixtures_identity']
        if (not ids or len(ids) != len(payload['eligible_match_ids'])
                or len(population) != len(ids)
                or {f['match_id'] for f in population} != ids):
            raise ValueError('plan identity population mismatch')
        if ids.intersection(identities):
            raise ValueError('overlapping registered match IDs across plans')
        for fixture in population:
            if fixture['cohort'] != payload['cohort'] or fixture['stage'] != payload['stage']:
                raise ValueError('plan identity population scope mismatch')
            key = (fixture['cohort'], fixture['stage'], aware(fixture['kickoff_at']),
                   fixture['home'], fixture['away'])
            if key in fixture_keys:
                raise ValueError('duplicate registered fixture under different IDs')
            fixture_keys.add(key)
            identities[fixture['match_id']] = fixture
        plans.append(plan)
    # Validate the complete manifest before reporting any cohort. Repeated
    # plans or identity aliases must not inflate the aggregate denominator.
    for plan in plans:
        ids = set(plan['payload']['eligible_match_ids'])
        matching = [p for p in packages if p['match_id'] in ids]
        labels = [r for r in results if r['match_id'] in ids]
        validate_plan_packages(plan, matching)
        value = review(plan, matching, labels, evidence_root)
        reviews.append(value)
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
