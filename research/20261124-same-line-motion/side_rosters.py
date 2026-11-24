"""Project the other registered original cup and international slates, retaining their gaps."""
import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.parse import parse_qs, urlsplit

from research.analysis_freeze import aware, verify
from early_boards import project_quote
from study import RULES, packed, write


ARCHIVES = {'20261111-jleague-cup-prematch': 'evidence-and-freezes.tar.gz',
            '20261115-evening-scope': 'evening-scope-evidence.tar.gz',
            '20261117-internationals-prematch': None,
            '20261117-tonight-analysis': 'tonight-evidence.tar.gz'}


def slots(repo, name, archive):
    folder = 'workspace-updates/' + name + '/'
    root = ['git', '-C', str(repo)]
    def blob(path):
        return subprocess.check_output(root + ['show', RULES['data_snapshot']+':'+folder+path])
    if archive:
        data = blob(archive)
        manifest = json.loads(blob('manifest.json'))
        if 'archive_sha256' in manifest and hashlib.sha256(data).hexdigest() != manifest['archive_sha256']:
            raise ValueError('archive checksum differs from source manifest')
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            files = {m.name: t.extractfile(m).read() for m in t if m.isfile() and m.name.endswith('.json')}
    else:
        names = subprocess.check_output(root+['ls-tree', '-r', '--name-only', RULES['data_snapshot'],folder], text=True)
        files = {n[len(folder):]: blob(n[len(folder):]) for n in names.splitlines()
                 if n.startswith(folder+'publication/freezes/') and n.endswith('.json')}
    bodies = {hashlib.sha256(raw).hexdigest(): raw for raw in files.values()}
    versions = defaultdict(dict)
    for path, raw in files.items():
        if 'issued-analyses/' not in path and 'publication/freezes/' not in path:
            continue
        r = json.loads(raw)
        if r.get('stage') != 'prematch' or r.get('track') != 'comprehensive':
            continue
        verify(r)
        versions[r['match_id']][r['record_sha256']] = r
    for mid, stamps in versions.items():
        records = list(stamps.values())
        firsts = [r for r in records if r['evaluation_slot'] == 'FIRST_PREMATCH' and r['revision'] == 1]
        r = min(firsts or records, key=lambda a: aware(a['recorded_at']))
        f = r['fixture']
        row = dict(id=mid, batch=name, competition=f['competition'], source_ids=f.get('source_ids'),
                   date=f['kickoff_at'][:10], home_id=f.get('home'), away_id=f.get('away'), provider='DraftKings',
                   stage=f.get('stage'), cutoff_at=f['cutoff_at'] if firsts else None, kickoff_at=f['kickoff_at'],
                   split='test', freeze_sha256=r['record_sha256'] if firsts else None, recorded_at=r['recorded_at'])
        if len(firsts) != 1:
            row['excluded'] = 'unavailable_unambiguous_original_first'
        elif f['competition'] == 'fifa.friendly':
            row['excluded'] = 'outside_competition_scope'
        elif f['competition'] == 'jpn.league_cup':
            row['excluded'] = 'no_bound_ESPN_full_match_market'
        elif f['competition'] == 'uefa.nations':
            evidence = f.get('phase_evidence') or {}
            digest = evidence.get('source_sha256')
            url = urlsplit(evidence.get('source_url', ''))
            if (digest not in bodies or url.hostname != 'site.api.espn.com'
                    or not url.path.endswith('/soccer/uefa.nations/summary')
                    or parse_qs(url.query).get('event') != [mid.removeprefix('espn:')]
                    or not evidence.get('query_time') or aware(evidence['query_time']) >= aware(f['cutoff_at'])):
                row['excluded'] = 'missing_bound_original_source'
            else:
                body = json.loads(bodies[digest])
                h = body['header']
                comp, = h['competitions']
                teams = {c['homeAway']: 'espn:'+str(c['team']['id']) for c in comp['competitors']}
                if (str(h['id']) != mid.removeprefix('espn:') or comp['id'] != h['id']
                        or not f.get('identity_verified') or f.get('gender') not in ('men','male')
                        or f.get('age') != 'senior' or f.get('score_basis') != 'regulation_90'
                        or not aware(f['cutoff_at']) <= aware(r['recorded_at']) < aware(f['kickoff_at'])
                        or h['league']['slug'] != 'uefa.nations' or comp['status']['type']['state'] != 'pre'
                        or aware(comp['date']) != aware(f['kickoff_at']) or teams != {k:f[k] for k in ('home','away')}
                        or not re.fullmatch(r'uefa\.nations:[ABCD]:league-phase', f['cohort'])
                        or f['stage'] != 'league-phase' or not h['season']['name'].casefold().endswith(', league phase')):
                    row['excluded'] = 'unverified_native_identity_or_stage'
                else:
                    row.update(project_quote(body.get('odds')), capture_at=evidence['query_time'], raw_sha256=digest)
        else:
            row['excluded'] = 'no_bound_ESPN_full_match_market'
        yield row


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    args = p.parse_args()
    rows = [r for name, archive in ARCHIVES.items() for r in slots(args.data, name, archive)]
    write(args.out, b''.join(packed(r) for r in rows))
    from collections import Counter
    from research_program.same_line_motion import observation
    print(len(rows), dict(Counter((r['batch'], observation(r)[0]) for r in rows)))
