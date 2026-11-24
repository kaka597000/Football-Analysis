"""Grade only the named club inventory after its price and model choices are locked."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import parse_qs, urlsplit

from research.analysis_freeze import aware
from research_program.replay import _finished_espn_label

DATA = 'a6d45024cf6cf622f05e4b378ab53cfe2d6ae329'
STUDY_DIR = 'research/20261124-nations-tier-fusion/complement-evidence/'
PANEL = 'club-first-source-lock.json'


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def verified_score(repo, frozen, result, work):
    ref = result['raw_reference']
    endpoint = urlsplit(ref['source_url'])
    if (result['match_id'] != frozen['id'] or result['competition'] != frozen['competition']
            or result['stage'] != frozen['stage'] or result['score_basis'] != 'regulation_90'
            or result['home'] != frozen['home_id'] or result['away'] != frozen['away_id']
            or aware(result['kickoff_at']) != aware(frozen['kickoff'])
            or ref['source_url'] != result['source_url']
            or parse_qs(endpoint.query).get('event') != [frozen['id'].split(':')[1]]
            or not aware(result['kickoff_at']) < aware(ref['captured_at']) == aware(result['known_at'])):
        raise ValueError('record identity, regulation basis or availability time differs')
    digest = ref['sha256']
    raw = gzip.decompress(git(repo, 'show', DATA + ':raw/' + digest + '.json.gz'))
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('raw response checksum mismatch')
    path = work / 'summary.json'
    path.write_bytes(raw)
    _finished_espn_label(result, {'url': result['source_url']}, path)
    return f"{result['home_goals']}-{result['away_goals']}", digest


def score(rows, repo, work):
    ids = [row['id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('one original cutoff per fixture required')
    markers = [f'"match_id": "{mid}"'.encode() for mid in ids]
    results = {}
    for line in git(repo, 'show', DATA + ':normalized-results.jsonl').splitlines():
        if not any(marker in line for marker in markers):
            continue
        item = json.loads(line)
        if item['match_id'] in results:
            raise ValueError('duplicate result identity')
        results[item['match_id']] = item
    for row in rows:
        item = results.get(row['id'])
        actual = evidence = None
        reason = 'NO_ARCHIVED_RESULT'
        if item:
            try:
                actual, evidence = verified_score(repo, row, item, work)
            except (KeyError, ValueError) as error:
                reason = str(error)
            else:
                reason = 'CERTIFIED'
        yield dict(**row, regulation_score=actual, result_sha256=evidence, result_status=reason)


def partition(rows, roles):
    result = Counter()
    patterns = Counter()
    for row in rows:
        if not all(row.get(role) for role in roles):
            continue
        result['available'] += 1
        if not row['regulation_score']:
            continue
        result['graded'] += 1
        winners = [role for role in roles if row[role] == row['regulation_score']]
        patterns['+'.join(winners) if winners else 'none'] += 1
    return dict(available=result['available'], graded=result['graded'], wins=dict(sorted(patterns.items())),
                retrospective_union=result['graded'] - patterns['none'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--lock-commit', required=True, help='published source revision that holds the pre-outcome choices')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    raw = git(here, 'show', args.lock_commit + ':' + STUDY_DIR + PANEL)
    if raw != (here / 'complement-evidence' / PANEL).read_bytes():
        raise ValueError('source choices differ from their named commit')
    with tempfile.TemporaryDirectory() as tmp:
        rows = list(score(json.loads(raw)['rows'], args.data, Path(tmp)))
    args.out.mkdir(parents=True, exist_ok=False)
    with (args.out / 'club-fixtures.csv').open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    pairs = [('frozen_comprehensive', 'market'), ('current', 'market'), ('current', 'test_model'),
             ('current', 'test_model', 'market'), ('current', 'test_model', 'market', 'frozen_comprehensive')]
    rates = {','.join(roles): partition(rows, roles) for roles in pairs}
    result = dict(registrations=len(rows), status=dict(Counter(r['result_status'] for r in rows)),
                  by_competition=dict(Counter(r['competition'] for r in rows)), fixed_pairs=rates,
                  authored_first_visible=dict(available=0, graded=0, rate=None))
    (args.out / 'club-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
