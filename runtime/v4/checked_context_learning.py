"""Checked training entry; retain hash-locked legacy context inference contracts."""
from datetime import datetime, timezone
from context_learning import prepare, baseline_contract, CONTRACT
from training_inputs import validate_context


def train_context(artifact, samples):
    # Preserve the original freeze/hash/identity/source audit before the shared
    # UTC and complete-scope validation. Never rewrite stored source forecasts.
    rows = prepare(samples, artifact)
    names = sorted(rows[0]['features'])
    if any(sorted(row['features']) != names for row in rows):
        raise ValueError('context schema mismatch')
    learned = validate_context(rows, names, min_rows=200)
    learned.update(feature_contract=CONTRACT, baseline_contract=baseline_contract(artifact))
    return dict(artifact, context_coefficients=learned,
                built_at=datetime.now(timezone.utc).isoformat())
