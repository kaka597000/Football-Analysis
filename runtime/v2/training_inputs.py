"""Validate new research training inputs without changing frozen model contracts.

Legacy model/evidence modules are hashed into active artifacts. Normalize copies
at training entry points so their string timestamp comparisons are chronological.
This does not certify historical first-known availability or promote a candidate.
"""
from copy import deepcopy
from datetime import date, timedelta, timezone
import math

from model import aware, validate_history
from evidence import fit_context as _fit_context, validate_context as _validate_context


def utc(value):
    return aware(value).astimezone(timezone.utc).isoformat()


def history_inputs(rows):
    normalized = deepcopy(rows)
    for row in normalized:
        # These four-way splits are calendar-date splits, not arbitrary strings.
        day = date.fromisoformat(row['date'])
        row['date'] = day.isoformat()
        row['observed_at'] = utc(row['observed_at'])
    return validate_history(normalized)


def context_inputs(samples, feature_names):
    if not feature_names or len(set(feature_names)) != len(feature_names):
        raise ValueError('unique nonempty context features required')
    normalized = deepcopy(samples)
    cohorts = {row['cohort'] for row in normalized}
    if len(cohorts) != 1:
        raise ValueError('context cohorts must not mix')
    for row in normalized:
        for name in ('kickoff_at', 'cutoff_at', 'baseline_observed_at', 'result_observed_at'):
            row[name] = utc(row[name])
        if aware(row['result_observed_at']) < aware(row['kickoff_at']) + timedelta(minutes=90):
            raise ValueError('90-minute result observed before regulation completion')
        for name in ('home_goals', 'away_goals'):
            if type(row[name]) is not int or not 0 <= row[name] <= 30:
                raise ValueError('invalid regulation score')
        for name in ('baseline_home', 'baseline_away'):
            if not math.isfinite(row[name]) or row[name] <= 0:
                raise ValueError('invalid baseline')
        if not all(math.isfinite(float(row['features'][name])) for name in feature_names):
            raise ValueError('nonfinite context features')
    return sorted(normalized, key=lambda row: row['kickoff_at'])


def fit_context(samples, feature_names, min_rows=100):
    return _fit_context(context_inputs(samples, feature_names), feature_names, min_rows)


def validate_context(samples, feature_names, min_rows=200):
    return _validate_context(context_inputs(samples, feature_names), feature_names, min_rows)
