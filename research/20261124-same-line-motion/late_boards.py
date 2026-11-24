"""Recover the original Nov 22/23 sportsbook responses without consulting match outcomes."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from urllib.parse import urlsplit

from research.analysis_freeze import aware, verify
from early_boards import project_quote
from study import packed, write


PACKS = {
    '20261122-covered-slate': ('87e3ad5ba551714dbf840bae84335d3bf619a50a',
                               'df67e4209ff042fba2473b770084e84d1401e085f25c8d629616aeaa5b3c72c2'),
    '20261123-evening-slate': ('e958e8e09e062d147e98714a466d69ff6d35ca56',
                               '446f929fc17484de087f389f4b1617d1792e26d8af4574fcc12ac9bec27f942a')}
REGULAR = {'bra.1': '2026-brasileiro-serie-a', 'swe.1': '2026-allsvenskan', 'nor.1': '2026-eliteserien',
           'ita.1': '2026-27-italian-serie-a', 'eng.1': '2026-27-english-premier-league',
           'esp.1': '2026-27-laliga', 'ger.1': '2026-27-german-bundesliga', 'fra.1': '2026-27-ligue-1',
           'ned.1': 'regular-season', 'usa.1': 'regular-season'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_object(raw, expected):
    if digest(raw) != expected:
        raise ValueError('original response or manifest object changed')
    return json.loads(raw)


def project(record, batch, originals):
    verify(record)
    f = record['fixture']
    row = dict(id=f['match_id'], batch=batch, provider='DraftKings', date=f['kickoff_at'][:10],
               home_id=f['home'], away_id=f['away'], competition=f['competition'], stage=f['stage'],
               source_ids=f.get('source_ids'), cutoff_at=f['cutoff_at'], kickoff_at=f['kickoff_at'],
               recorded_at=record['recorded_at'], freeze_sha256=record['record_sha256'])
    league = f['competition']
    if (record['track'] != 'comprehensive' or record['stage'] != 'prematch' or record['revision'] != 1
            or record['evaluation_slot'] != 'FIRST_PREMATCH' or record['match_id'] != f['match_id']
            or not aware(f['cutoff_at']) <= aware(record['recorded_at']) < aware(f['kickoff_at'])):
        raise ValueError('record is not a prematch original')
    refs = [s for s in f.get('sources', []) if urlsplit(s.get('source_url','')).hostname == 'site.api.espn.com'
            and urlsplit(s['source_url']).path.endswith('/soccer/'+league+'/scoreboard')]
    if len(refs) != 1:
        return dict(row, excluded='missing_unique_original_scoreboard')
    ref, = refs
    sha = ref['response_sha256']
    clock = ref.get('query_time')
    if not clock or aware(clock) >= aware(f['cutoff_at']) or sha not in originals:
        return dict(row, excluded='missing_original_bytes_or_predecision_clock')
    raw, receipt = originals[sha]
    if receipt is not None and (receipt['url'] != ref['source_url'] or receipt['http_status'] != 200 or
                                aware(receipt['fetched_at_utc']) != aware(clock)):
        return dict(row, excluded='original_receipt_mismatch')
    body = checked_object(raw, sha)
    events = [e for e in body['events'] if 'espn:'+str(e['id']) == f['match_id']]
    if len(events) != 1 or league not in {x['slug'] for x in body['leagues']}:
        return dict(row, excluded='unverified_original_event')
    e, = events
    comp, = e['competitions']
    sides = {t['homeAway']:'espn:'+str(t['team']['id']) for t in comp['competitors']}
    if (str(comp['id']) != str(e['id']) or not f.get('identity_verified') or f['age'] != 'senior'
            or f['gender'] not in ('men','male') or f.get('score_basis') != 'regulation_90'
            or aware(comp['date']) != aware(f['kickoff_at']) or comp['status']['type']['state'] != 'pre'
            or sides != {k:f[k] for k in ('home','away')}):
        return dict(row, excluded='unverified_original_event')
    stage = e['season'].get('slug')
    regular = (league in REGULAR and f['cohort'] == league and f['stage'] == 'regular' and
               stage == REGULAR[league])
    elite = (f['cohort'] == 'afc.champions:ELITE:league_phase' and f['stage'] == 'league_phase'
             and league == 'afc.champions' and e['season'].get('year') in (2025, 2026) and stage == 'league-stage')
    if not (regular or elite):
        return dict(row, excluded='outside_verified_regular_or_elite_phase')
    return dict(row, **project_quote(comp.get('odds')), capture_at=clock, raw_sha256=sha)


def batch_inputs(repo, batch, saved_boards):
    pin, expected = PACKS[batch]
    relative = f'workspace-updates/{batch}/evidence.tar.gz'
    blob = subprocess.check_output(['git','-C',str(repo),'show',pin+':'+relative])
    if digest(blob) != expected:
        raise ValueError('unexpected revision of original delivery archive')
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r|gz') as t:
        members = {m.name:t.extractfile(m).read() for m in t if m.isfile()}
    manifest = json.loads(members['manifest.json'])
    if 'freeze_records' in manifest:
        # The issuer's original first binds the retrieval clock to the same content-addressed response.
        originals = {sha:(members['objects/'+sha], None) for path, sha in manifest['files'].items()
                     if path.startswith('schedule/raw/espn-')}
        records = [checked_object(members['objects/'+r['file_sha256']], r['file_sha256'])
                   for r in manifest['freeze_records'].values()]
    else:
        records = [checked_object(members[p], sha) for p,sha in manifest.items()
                   if p.startswith('freezes/') and p.endswith('-comprehensive.json')]
        path = 'schedule/snapshot-metadata.json'
        meta = checked_object(members[path], manifest[path])
        receipts = {s['sha256']:s for s in meta['responses'] if s.get('http_status') == 200 and s.get('sha256')}
        originals = {}
        for snapshot in json.loads(saved_boards.read_bytes()) if saved_boards else []:
            sha = snapshot.get('sha256')
            if sha in receipts:
                originals[sha] = (snapshot['raw'].encode('utf-8'), receipts[sha])
    selected = [r for r in records if r.get('evaluation_slot') == 'FIRST_PREMATCH' and
                r.get('track') == 'comprehensive' and r.get('stage') == 'prematch']
    if len({r['match_id'] for r in selected}) != (33 if 'freeze_records' in manifest else 15):
        raise ValueError('original roster size or identities changed')
    return [project(r, batch, originals) for r in selected]


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data', required=True, type=Path)
    cli.add_argument('--nov23-raw', type=Path, help='retained original scoreboard bundle; absent bytes are recorded')
    cli.add_argument('--out', required=True, type=Path)
    args = cli.parse_args()
    rows = [r for batch in PACKS for r in batch_inputs(args.data, batch, args.nov23_raw)]
    write(args.out, b''.join(packed(r) for r in rows))
