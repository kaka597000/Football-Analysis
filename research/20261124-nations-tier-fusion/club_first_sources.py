"""Verify the earlier club inventory without opening results or later forecast revisions."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import shutil
import tarfile
import tempfile
from contextlib import contextmanager

import numpy as np

from research.analysis_freeze import aware, digest, verify
from research_program.market_baseline import quote_only_matrices
from research_program.three_arm_model import score_picks, top_three_scores

HERE = Path(__file__).resolve().parent
PACKS = {'nov07-55': '20261107-night19-55matches', 'nov08-46': '20261108-night19-46matches'}
STAGE_ADAPTER_SHA = '39a5d06e68699c8463c2481c69c568db15939bf031ab844e25ae23f2e9163588'


@contextmanager
def indexed_archive(stage, repo, slug):
    # Repeated random access through gzip would decompress the same archive for every fixture.
    _, packed = stage.archive(repo, slug)
    with packed, tempfile.TemporaryFile() as raw:
        packed.fileobj.seek(0)
        shutil.copyfileobj(packed.fileobj, raw)
        raw.seek(0)
        with tarfile.open(fileobj=raw, mode='r:') as archive:
            yield archive


def members_first(archive, mid, track):
    identity = dict(match_id=mid, stage='prematch', track=track)
    path = 'WCPP/forecasts/issued-analyses/' + digest(identity) + '/000001.json'
    if path not in archive.getnames():
        return None
    record = verify(json.load(archive.extractfile(path)))
    if (any(record[k] != v for k, v in identity.items()) or record['revision'] != 1
            or record['evaluation_slot'] != 'FIRST_PREMATCH' or record['previous_sha256'] is not None):
        raise ValueError('archived first issue identity mismatch')
    return record


def pair_choices(record, fixture):
    blank = dict(current=None, test_model=None, current_market_adjustment=None, current_matrix_sha=None,
                 test_matrix_sha=None, pair_cutoff=None, pair_issued=None, pair_sha=None)
    if record is None:
        return dict(**blank, pair_status='FIRST_PAIR_ABSENT')
    source = record['fixture']
    identity_keys = ('competition', 'stage', 'match_id', 'home', 'away', 'neutral')
    if (any(source.get(k) != fixture.get(k) for k in identity_keys)
            or aware(source['kickoff_at']) != aware(fixture['kickoff_at']) or source['identity_verified'] is not True):
        return dict(**blank, pair_status='FIRST_PAIR_IDENTITY_UNCONFIRMED')
    signed = aware(record['recorded_at'])
    if not aware(source['cutoff_at']) <= signed <= aware(fixture['cutoff_at']):
        return dict(**blank, pair_status='FIRST_PAIR_UNAVAILABLE_AT_DECISION')
    payload = record['payload']
    picks = {}
    refs = payload.get('current_test_disagreement') or {}
    for model, col in [('current_model','current'), ('test_model','test_model')]:
        arm = payload.get(model) or {}
        g = arm.get('matrix_hash')
        if (not g or not arm.get('code_hash') or not arm.get('primary') or digest(arm.get('matrix')) != g
                or (refs.get('matrix_sha256') or {}).get(model) != g
                or (refs.get('primary_scores') or {}).get(model) != arm['primary']):
            return dict(**blank, pair_status='FIRST_PAIR_MATRIX_OR_PRIMARY_UNCONFIRMED')
        picks[col] = arm['primary']
        picks[('current' if model=='current_model' else 'test') + '_matrix_sha'] = g
    return dict(**picks, current_market_adjustment=payload['current_model'].get('market_numeric_adjustment_applied'),
                pair_cutoff=source['cutoff_at'], pair_issued=record['recorded_at'], pair_sha=record['record_sha256'],
                pair_status='VERIFIED_EARLIER_FIRST')


def verify_capture(archive, member, quote):
    sidecar = member + '.meta.json'
    if sidecar in archive.getnames():
        meta = json.load(archive.extractfile(sidecar))
        captured = meta.get('query_time_utc') or meta.get('query_time') or meta.get('queried_utc')
        if (meta.get('http_status') != 200 or (meta.get('response_sha256') or meta.get('sha256')) != quote['raw_sha256']
                or not captured or aware(captured) != aware(quote['query_utc'])):
            raise ValueError('quote receipt hash, HTTP status or capture time differs')
        return
    index, = [n for n in archive.getnames() if n.endswith('/markets/root-verified-quotes.json')]
    groups = json.load(archive.extractfile(index))
    matches = [snap for group in groups.values() for snap in group.get('snapshots', [])
               if snap.get('event_id') == quote['event_id'] and snap.get('raw_sha256') == quote['raw_sha256']
               and aware(snap['query_utc']) == aware(quote['query_utc'])]
    if len(matches) != 1 or any(matches[0].get(k) != quote.get(k) for k in
                                ('h2h', 'total', 'market_last_update_utc', 'home', 'away', 'utc')):
        raise ValueError('original quote acquisition index does not bind the signed snapshot')


def verified_market_matrix(raw, fixture, quote):
    if hashlib.sha256(raw).hexdigest() != quote['raw_sha256']:
        raise ValueError('Pinnacle source digest mismatch')
    mid = fixture['source_ids']['the_odds']
    event, = [item for item in json.loads(raw) if item['id']==mid]
    if (mid != quote['event_id'] or event['home_team']!=quote['home'] or event['away_team']!=quote['away']
            or aware(event['commence_time'])!=aware(fixture['kickoff_at']) or quote['bookmaker'].lower()!='pinnacle'):
        raise ValueError('signed cross-provider event mismatch')
    book, = [book for book in event['bookmakers'] if book['key']=='pinnacle']
    captured = aware(quote['query_utc'])
    for name in ('h2h','totals'):
        market, = [m for m in book['markets'] if m['key']==name]
        expected = ({quote['home']:quote['h2h']['home'], 'Draw':quote['h2h']['draw'], quote['away']:quote['h2h']['away']}
                    if name=='h2h' else {'Over':quote['total']['over'], 'Under':quote['total']['under']})
        sides = [v for v in market['outcomes'] if name=='h2h' or v.get('point')==quote['total']['line']]
        ts = aware(market['last_update'])
        if (len(sides)!=len(expected) or {v['name']:v['price'] for v in sides}!=expected
                or ts!=aware(quote['market_last_update_utc'][name]) or not ts<=captured<=aware(fixture['cutoff_at'])):
            raise ValueError('signed prices, market side or clock mismatch')
    return quote_only_matrices(quote['h2h'],quote['total'])[1]


def market_score(raw, fixture, quote):
    return score_picks(verified_market_matrix(raw, fixture, quote), line=quote['total']['line'])['primary']


def build(repo, stage, *, include_ranks=False):
    inventory = json.loads((HERE/'complement-evidence/club-inventory-inputs.json').read_bytes())
    rows = []
    for panel in inventory['panels']:
        if panel['name'] not in PACKS:
            continue  # Later Big Five package contains only already-registered events.
        slug, commit = PACKS[panel['name']], panel['original_data_commit']
        manifest = json.loads(subprocess.check_output(['git','-C',str(repo),'show',commit+':workspace-updates/'+slug+'/manifest.json']))
        committed = aware(subprocess.check_output(['git','-C',str(repo),'show','-s','--format=%cI',commit],text=True).strip())
        if manifest['archive_sha256'] != panel['archive_sha256']:
            raise ValueError('original pre-kickoff archive differs')
        with indexed_archive(stage,repo,slug) as archive:
            capture_meta = stage.summary_metadata(archive)
            for saved in panel['entries']:
                mid = saved['id']
                record = members_first(archive,mid,'comprehensive')
                f, p = record['fixture'], record['payload']
                if (record['record_sha256']!=saved['freeze'] or f['cutoff_at']!=saved['cutoff']
                        or not aware(f['cutoff_at'])<=aware(record['recorded_at'])<=committed<aware(f['kickoff_at'])
                        or not p['primary'] or p['primary']!=saved['original_first_comprehensive']):
                    raise ValueError('first comprehensive choice or archive time mismatch')
                # The prior adapter binds ESPN identity, prematch status, stage, both teams and acquisition receipt.
                # Its DraftKings quote fields are outside this Pinnacle comparison.
                proof, reason = stage.bind_summary(archive,f,record,capture_meta,lambda unused: dict(latest_h2h=None,current_total=None))
                if (not proof or proof['home_id']!=f['home'] or proof['away_id']!=f['away']
                        or proof['source_ids']['espn_event_id']!=mid):
                    raise ValueError('native fixture proof unavailable: '+str(reason))
                quote = p['quote']
                name = stage.member(archive,quote.get('raw_path') or quote['source_file'])
                verify_capture(archive,name,quote)
                matrix = verified_market_matrix(archive.extractfile(name).read(),f,quote)
                market = score_picks(matrix,line=quote['total']['line'])['primary']
                if market != saved['market'] or quote['raw_sha256'] != saved['quote_sha256']:
                    raise ValueError('dual-market reply differs from original inventory lock')
                pair_record = members_first(archive,mid,'current-test-pair')
                pair = pair_choices(pair_record,f)
                if include_ranks:
                    pair['market_ranks'] = top_three_scores(matrix)
                    for model_key, role in [('current_model','current'), ('test_model','test_model')]:
                        ranks = None
                        if pair['pair_status'] == 'VERIFIED_EARLIER_FIRST':
                            model = pair_record['payload'][model_key]
                            ranks = top_three_scores(np.asarray(model['matrix'], dtype=float))
                            if ranks != [item['score'] for item in model['top3']] or ranks[0] != pair[role]:
                                raise ValueError('ranked matrix differs from the signed first three')
                        pair[role + '_ranks'] = ranks
                rows.append(dict(id=mid,batch=panel['name'],competition=f['competition'],stage=f['stage'],
                                 home=f['home_name'],away=f['away_name'],home_id=f['home'],away_id=f['away'],
                                 kickoff=f['kickoff_at'],cutoff=f['cutoff_at'],frozen_comprehensive=p['primary'],
                                 market=market,**pair,comprehensive_sha=record['record_sha256'],
                                 quote_sha=quote['raw_sha256'],prematch_identity_sha=proof['raw_sha256']))
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('duplicate club event')
    return dict(purpose='Earlier club-source inventory. Frozen comprehensive is a mixed-source choice with no delivery proof. Current and Test share team inputs. No source selector is trained or tested on this population.',rows=rows)


def load_stage(path):
    if hashlib.sha256(path.read_bytes()).hexdigest()!=STAGE_ADAPTER_SHA:
        raise ValueError('unexpected archived prematch stage adapter')
    spec = importlib.util.spec_from_file_location('club_native_stage',path)
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    return stage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-adapter',type=Path,required=True)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    panel = build(args.data,load_stage(args.stage_adapter))
    with args.out.open('x',encoding='utf8') as stream:
        stream.write(json.dumps(panel,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
