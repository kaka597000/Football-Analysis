"""Read-only Canonical inventory, including anomalous cardinalities and stages."""
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from schema import save, now

def connect(path):
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('BEGIN')
    return c

def csv_write(path, rows):
    rows = list(rows)
    if not rows:
        return
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def audit(db, registry, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    c = connect(db)
    matches = [dict(r) for r in c.execute('SELECT * FROM matches ORDER BY competition,season,date,match_id')]
    groups = defaultdict(list)
    for m in matches:
        groups[(m['competition'], m['season'], m['stage'])].append(m)
    seasons = []; anomalies = []
    for (code, season, stage), rows in groups.items():
        pairs = Counter((r['home_id'], r['away_id']) for r in rows)
        duplicates = [r for r in rows if pairs[(r['home_id'], r['away_id'])] > 1]
        # A repeated oriented pair is a review flag; never silently merge dates.
        known = [r for r in rows if r['training_eligible']]
        seasons.append(dict(competition=code, season=season, stage=stage, matches=len(rows), completed=len(known),
                            duplicate_oriented_pair_rows=len(duplicates), first_date=rows[0]['date'], last_date=rows[-1]['date']))
        for r in duplicates:
            anomalies.append(dict(r, reason='REPEATED_ORIENTED_PAIR_REQUIRES_STAGE_OR_RESCHEDULE_REVIEW'))
    providers = [dict(r) for r in c.execute('SELECT provider,count(*) n,sum(first_known_at IS NOT NULL) first_known_n FROM observations GROUP BY provider')]
    comps = [dict(r) for r in c.execute('SELECT competition,count(*) matches,sum(training_eligible) eligible FROM matches GROUP BY competition')]
    reg = json.loads(Path(registry).read_text())
    routes = []
    for cohort, entry in reg['models'].items():
        code = cohort.split(':')[0]
        routes.append(dict(cohort=cohort, canonical_n=sum(r['competition'] == code for r in matches),
                           current_artifact=entry.get('artifact_path'), test_artifact=entry.get('test_artifact_path'),
                           status='AVAILABLE_FOR_QUALITY_REVIEW' if any(r['competition'] == code for r in matches) else 'NO_CANONICAL_COMPETITION'))
    save(out/'audit.json', dict(actual_generated_at=now(), canonical_path=str(db), read_only=True, historical_verified_asof_n=0,
                               competition_count=len(comps), match_count=len(matches), eligible_count=sum(r['training_eligible'] for r in matches),
                               providers=providers, active_routes=routes, groups=seasons))
    save(out/'anomalies.json', anomalies)
    csv_write(out/'coverage-by-cohort.csv', comps)
    csv_write(out/'season-stage-coverage.csv', seasons)
    csv_write(out/'active-routes.csv', routes)
    c.close()
    return {'competitions': len(comps), 'matches': len(matches), 'anomaly_rows': len(anomalies)}
