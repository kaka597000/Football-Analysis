"""Authenticate retained regulation results for the sealed Nations League and Brazilian fixtures."""
import argparse
from collections import defaultdict
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import tarfile
from urllib.parse import parse_qs, urlsplit

from research.analysis_freeze import aware
from raw_summary_adapter import authenticate
from study import indexed, packed, save, sha, write

BUNDLES=[
 ('20261113-nations-review','nations-review-evidence.tar.gz','ea87500a7850cccd2b1029079f38ec3e1701d0cc','448edda016c523688b2f0558d6af396c787390f3bad578e8144a20a178af8f1e'),
 ('20261114-nations-review','nations-review-evidence.tar.gz','fc9d3e8d1ddaefdc6ecee0ed566437d64644b007','7da16bb5c7c089a236ad41d62d52a5d7f6f09949056d6cd7593445d4c2f160e1'),
 ('20261113-nations-reanalysis','nations-reanalysis-evidence.tar.gz','28f30ca3b7df0cb0827900937bae339e3dc1ab61','4e3bdfc7b9d194ffb26ea1c73d3db21a904f067a1bba63f9486cee03f3c92b0c'),
 ('20261115-remaining-nations-brazil','remaining-evidence.tar.gz','e932ab13ec6938d5ffd1c5fc95678798da3466a1','3f842a58a73eb453d354e8a770c934378785714189c2e73bf190f77596534e49'),
 ('20261115-evening-scope','evening-scope-evidence.tar.gz','a4cbc462df2ace01739269a66262321ac6e69e66','8d9fd69c81955c12b7a65de9be099032bd947b7baae57fa8b1746d416db84809'),
 ('20261115-review-kashiwa','review-kashiwa-evidence.tar.gz','b5651c1ef2b50ddde8650ca2c30cb7b413d60ad6','d332e31f47de60fc92531447633e01632c60bb8c484bb73d4e249504b3e95586'),
 ('20261116-nations-review','nations-review-evidence.tar.gz','85df31d491bc1faed79de02a97325e3321c18ca7','3b58dc3cf731eaf7d4762f70ad534d888a3966b7b40ad00b636a46f2f50b5bc8'),
 ('20261117-nations-review-0833','nations-review-evidence.tar.gz','0e06c14e8338c7bca9b9f07ee68345dfe6930e41','b00b5eebefdbd251d9e243d21c96057d1b507c8e8158b4c38d81cd4acb8e9071'),
 ('20261117-tonight-analysis','tonight-evidence.tar.gz','0b366adac09a186847214a639bef4f559de2b79f','ce754819274a83450ad8e0459a12f6331eb03c472b61e4e027476acf6468e2c0'),
 ('20261118-all-analysis-review','review-evidence.tar.gz','3a40c16d1873fe803a6b79b23fe19e633d140868','7913ed0d798119f537f03889af46fccd6eec317f837e857a0881651abd2d1e25'),
]

BATCHES = {'20261112-nations-prematch','20261113-nations-evening','20261114-nations-prematch',
           '20261115-remaining-nations-brazil','20261115-evening-scope','20261116-nations-prematch-2044',
           '20261117-tonight-analysis'}
TIME_FIELDS = ('response_read_complete','read_complete_at','completed_utc','response_received_at',
               'observed_at','captured_at','captured_at_utc','query_time','captured')


def receipts(node):
    if isinstance(node, list):
        for child in node:
            yield from receipts(child)
    elif isinstance(node, dict):
        url = next((node[k] for k in ('url','source_url','request_url') if node.get(k)), None)
        digest = next((node[k] for k in ('sha256','source_sha256','response_sha256','evidence_sha256')
                       if isinstance(node.get(k),str) and len(node[k]) == 64), None)
        field = next((k for k in TIME_FIELDS if isinstance(node.get(k),str) and 'T' in node[k]), None)
        if isinstance(url,str) and digest and field and urlsplit(url).hostname == 'site.api.espn.com':
            yield (url,digest.lower(),field,node[field])
        for child in node.values():
            if isinstance(child,(list,dict)):
                yield from receipts(child)


def result_stage(raw, original):
    header = json.loads(raw)['header']
    season_name = header['season']['name'].strip().casefold()
    if header['league']['slug'] != original['competition']:
        return False
    if original['competition'] == 'uefa.nations':
        return original['stage'] == 'league-phase' and season_name.endswith('uefa nations league, league phase')
    return original['competition'] == 'bra.1' and original['stage'] == 'regular' and season_name.endswith('brasileiro serie a')


def reconstruct(data, inputs, lockpath, out, report):
    lock = json.loads(lockpath.read_bytes())
    forecasts_path = lockpath.parent/'forecasts.jsonl'
    if lock['panel'] != 'native' or lock['input_sha256'] != sha(inputs) or lock['forecasts_sha256'] != sha(forecasts_path):
        raise ValueError('roster or forecast digest changed')
    forecasts = indexed(forecasts_path)
    targets = {k:r for k,r in indexed(inputs).items() if r['batch'] in BATCHES and k in forecasts
               and forecasts[k].get('forecasts') and r['competition'] in ('uefa.nations','bra.1')}
    cutoff = lock['result_cutoff_at']
    verified = defaultdict(list)
    for folder,filename,commit,expected_hash in BUNDLES:
        path = f'workspace-updates/{folder}/{filename}'
        blob = subprocess.check_output(['git','-C',str(data),'show',commit+':'+path])
        if sha256(blob).hexdigest() != expected_hash:
            raise ValueError('archive digest differs: '+folder)
        bodies, captured = {}, set()
        with tarfile.open(fileobj=io.BytesIO(blob),mode='r|gz') as stream:
            for member in stream:
                if not member.isfile() or not member.name.lower().endswith('.json'):
                    continue
                raw = stream.extractfile(member).read()
                bodies[sha256(raw).hexdigest()] = raw
                if any(s in member.name.lower() for s in ('meta','capture','ledger')):
                    captured.update(receipts(json.loads(raw)))
        for url,digest,field,clock in sorted(captured):
            query = parse_qs(urlsplit(url).query)
            event_ids = query.get('event', [])
            if len(event_ids) != 1:
                continue
            mid = 'espn:'+event_ids[0]
            if mid not in targets or digest not in bodies:
                continue
            original = targets[mid]
            if (urlsplit(url).path != '/apis/site/v2/sports/soccer/'+original['competition']+'/summary'
                    or not aware(original['kickoff_at']) < aware(clock) < aware(cutoff)):
                continue
            raw = bodies[digest]
            if not result_stage(raw, original):
                raise ValueError('source phase disagrees with original for '+mid)
            row = authenticate(raw,expected=original,source_url=url,captured_at=clock,cutoff_at=cutoff,evidence_sha256=digest)
            row['provenance'] = dict(data_commit=commit,data_archive=path,archive_sha256=expected_hash,
                                     capture_clock_field=field,source_url=url)
            verified[mid].append(row)
    selected = []
    for mid, rows in sorted(verified.items()):
        if len({tuple(r['score']) for r in rows}) != 1:
            raise ValueError('conflicting authenticated results for '+mid)
        selected.append(max(rows,key=lambda r:(aware(r['observed_at']),r['evidence_sha256'])))
    write(out,b''.join(packed(r) for r in selected))
    save(report,dict(lock_sha256=sha(lockpath),targets=len(targets),accepted=len(selected),
                     missing_ids=sorted(set(targets)-set(verified)),output_sha256=sha(out)))


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('data','inputs','lock','out','report'):
        cli.add_argument('--'+name,type=Path,required=True)
    a=cli.parse_args()
    reconstruct(a.data,a.inputs,a.lock,a.out,a.report)
