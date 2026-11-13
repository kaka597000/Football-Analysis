"""Read-only identity binding around source-locked future-review engines."""
import json
from pathlib import Path

from research.analysis_freeze import aware, verify


def fixture_identity(fixture):
    """Compare the registered event, treating equivalent kickoff offsets alike."""
    return (fixture['match_id'], fixture['cohort'], fixture['stage'],
            aware(fixture['kickoff_at']), fixture['home'], fixture['away'])


def validate_plan_packages(plan_record, packages):
    """Check resolved receipt paths against the plan; never mutate or infer.

    Model, matrix, result, first-slot and chronology validation remains with the
    existing review engine. Call this before that engine, including for pending
    packages with no results.
    """
    plan_record = verify(plan_record)
    plan = plan_record['payload']
    ids = plan['eligible_match_ids']
    population = plan['fixtures_identity']
    if (not ids or len(set(ids)) != len(ids) or len(population) != len(ids)
            or {f['match_id'] for f in population} != set(ids)):
        raise ValueError('plan identity population mismatch')
    identities = {f['match_id']: fixture_identity(f) for f in population}
    seen = set()
    for package in packages:
        mid = package['match_id']
        if (mid in seen or mid not in identities
                or package.get('plan_sha256') != plan_record['record_sha256']):
            raise ValueError('duplicate/outside/unbound identity package')
        seen.add(mid)
        for role in ('current', 'candidate'):
            receipt = package[role + '_receipt']
            record = verify(json.loads(Path(receipt['path']).read_text(encoding='utf-8')))
            if record['record_sha256'] != receipt.get('sha256'):
                raise ValueError('identity package receipt checksum mismatch')
            if (('match_id' in record and record['match_id'] != mid)
                    or fixture_identity(record['fixture']) != identities[mid]):
                raise ValueError('preregistered fixture identity mismatch')
