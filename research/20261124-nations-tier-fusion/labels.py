"""Verify evidence from only the requested completed window; earlier labels must be known by fit time."""

import json
from urllib.parse import parse_qs, urlsplit

from archive_inputs import Bundle
from nations_league.elo_monitor import regulation_result, sides
from nations_league.elo_reference import ALIASES
from research.analysis_freeze import aware

ANCHOR = aware('2026-11-15T08:00:00Z')


def certified(repo, roster, *, training):
    bundles = ('r12','r13','r14') if training else ('r15','r16','r17')
    found, issues = {}, {}
    for key in bundles:
        arc = Bundle(repo, key)
        if training and arc.at > ANCHOR:
            raise ValueError('training label bundle did not exist by the fit origin')
        for name in arc.files:
            if not name.endswith('/verified-results.json'):
                continue
            for label in arc.value(name):
                mid = label['match_id']
                if mid not in roster:
                    continue
                f = roster[mid]['f']
                try:
                    source = urlsplit(label['source_url'])
                    observed = aware(label['observed_at'])
                    if (source.scheme != 'https' or source.hostname not in ('site.api.espn.com','site.web.api.espn.com')
                            or not source.path.endswith('/soccer/uefa.nations/summary')
                            or parse_qs(source.query).get('event') != [mid.removeprefix('espn:')]
                            or label['event_status'] != 'FT' or label['score_basis'] != 'regulation_90'
                            or not 5400 <= (observed-aware(f['kickoff_at'])).total_seconds()
                            or observed > arc.at):
                        raise ValueError('invalid label chronology, source or score basis')
                    raw = json.loads(arc.raw[label['source_sha256']])
                    header = raw['header']
                    if any('espn:' + str(team['team']['id']) != f[s] for s, team in sides(header).items()):
                        raise ValueError('regulation source team IDs differ')
                    named = dict(f, **{s: ALIASES.get(f[s+'_name'],f[s+'_name']) for s in ('home','away')})
                    score = regulation_result(raw, {'fixture':named})
                    if score is None or score != [label['home_goals'],label['away_goals']]:
                        raise ValueError('archived score disagrees with authenticated 90-minute source')
                except (ValueError, KeyError) as exc:
                    issues.setdefault(mid, []).append(str(exc))
                    continue
                if mid in found and found[mid]['score'] != score:
                    raise ValueError('conflicting certified labels ' + mid)
                found.setdefault(mid, dict(score=score, raw_sha=label['source_sha256'],
                                           observed_at=label['observed_at'], committed_at=arc.at.isoformat()))
    return found, issues
