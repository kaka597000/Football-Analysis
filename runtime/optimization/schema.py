"""Independent research contracts; no active-model imports or side effects."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'WCPP-SYSTEMATIC-RESEARCH-0.1'

def now():
    return datetime.now(timezone.utc).isoformat()

def aware(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timezone required')
    return d.astimezone(timezone.utc)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def save(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())

def code_hash():
    return digest({p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py')) if not p.name.startswith('test_')})

def fixture(row):
    # Explicit allow-list: target labels/process/postmatch fields never cross.
    return {k: row.get(k) for k in ('match_id', 'cohort', 'competition', 'season', 'date', 'home', 'away', 'neutral', 'stage')}

def validate_fixture(row):
    if row['home'] == row['away'] or not row['home'] or not row['away']:
        raise ValueError('invalid team identities')
    datetime.strptime(row['date'], '%Y-%m-%d')
    if row.get('neutral') not in (True, False, None):
        raise ValueError('neutral must be three-state')
    forbidden = {'home_goals', 'away_goals', 'home_xg', 'away_xg', 'result', 'postmatch', 'lineup'}
    if forbidden.intersection(row):
        raise ValueError('target contains label or postmatch information')
    return row
