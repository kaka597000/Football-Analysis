"""Finite research identities; never modifies the hash-locked activity router."""
from urllib.parse import urlparse
from v4.competition_router import classify as activity_classify

PHASES = {
    'uefa.europa': {'league-phase', 'knockout-round-playoffs', 'round-of-16', 'quarterfinals', 'semifinals', 'final'},
    'uefa.europa.conf': {'league-phase', 'knockout-round-playoffs', 'round-of-16', 'quarterfinals', 'semifinals', 'final'},
    'fifa.world': {'group-stage', 'round-of-32', 'round-of-16', 'quarterfinals', 'semifinals', 'final', '3rd-place-match'},
}


def classify(row):
    code = row.get('cohort', '').split(':')[0]
    if code not in PHASES:
        return activity_classify(row)
    e = row.get('evidence') or {}
    source = urlparse(e.get('summary_url', ''))
    complete = (row.get('stage') in PHASES[code]
                and source.scheme == 'https' and source.hostname == 'site.api.espn.com'
                and source.path == f'/apis/site/v2/sports/soccer/{code}/summary'
                and e.get('status') == 'STATUS_FULL_TIME' and e.get('regulation_periods') == 2
                and isinstance(e.get('summary_sha256'), str) and len(e['summary_sha256']) == 64
                and type(row.get('neutral')) is bool)
    c = dict(competition=code, gender='men', age='senior',
             entity='national' if code == 'fifa.world' else 'club',
             kind='national_competitive' if code == 'fifa.world' else 'continental_cup',
             stage=row.get('stage'))
    return {'complete': complete, 'classification': c if complete else None,
            'route_key': '|'.join(str(v) for v in c.values()) if complete else None,
            'reasons': [] if complete else ['explicit phase and retained FT summary evidence required'],
            'status': 'RESEARCH_ONLY_NOT_ACTIVITY_ROUTE'}
