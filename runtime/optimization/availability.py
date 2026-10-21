"""Three time tracks. Retrospective lag is an assumption, never first-known evidence."""
from datetime import date
from schema import aware

VERIFIED = 'VERIFIED_AS_OF'
RETRO = 'RETROSPECTIVE_PRIOR_ONLY'

def eligible(fact, target_date, mode=RETRO, lag_days=7, cutoff=None):
    # Day-precision facts exclude the entire target day, regardless of kickoff.
    delta = (date.fromisoformat(target_date) - date.fromisoformat(fact['date'])).days
    if delta <= 0:
        return False
    if mode == RETRO:
        # End-of-source-day plus N complete natural days must precede cutoff day.
        return delta > lag_days
    if mode != VERIFIED:
        raise ValueError('unknown time track')
    if cutoff is None:
        raise ValueError('verified track requires cutoff')
    known = fact.get('first_known_at')
    return known is not None and aware(known) <= aware(cutoff)

def select_revision(facts, target_date, mode=RETRO, lag_days=7, cutoff=None):
    allowed = [f for f in facts if eligible(f, target_date, mode, lag_days, cutoff)]
    if mode == VERIFIED:
        allowed.sort(key=lambda f: (f['first_known_at'], f.get('revision', 0)))
    else:
        # Research uses the archived revision, explicitly not a historical as-of assertion.
        allowed.sort(key=lambda f: (f.get('fetched_at', ''), f.get('revision', 0)))
    return allowed[-1] if allowed else None
