"""Register complete J1 display windows and lock their authored-score selections."""

import argparse
import hashlib
import math
from pathlib import Path

from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from research.display_delivery import delivery_request
from . import goal_state_forward as ledger
from .goal_state_study import sha, write_new
from .r8_score_study import read

TRACK = 'j1-comprehensive-windows-20261125'


def register(protocol, scope, goals, board, registered, registry, crosswalk, out, *, freeze_root=DEFAULT_ROOT):
    spec = read(protocol)
    code = spec['competition']
    windows = spec['windows']
    if (code not in {v['code'] for v in read(scope)['competitions']} or len(windows) != 2
            or aware(windows[0]['until']) != aware(windows[1]['from'])):
        raise ValueError('invalid competition or nonconsecutive windows')
    raw = read(board)
    if [v['slug'] for v in raw['leagues']] != [code]:
        raise ValueError('wrong schedule source')
    weights = read(registry)['models'][code]
    for path, fingerprint in [('artifact_path','artifact_sha256'),('test_artifact_path','test_artifact_sha256')]:
        if sha(Path(weights[path])) != weights[fingerprint]:
            raise ValueError('active artifact hash mismatch')
    idx = read(registered/'receipts.json')
    parent = ledger._read(registered, idx['registration'])
    all_fixtures = []
    slates = {}
    for window in windows:
        start, end = (aware(window[key]) for key in ('from','until'))
        ids = []
        for event in raw['events']:
            if not start <= aware(event['date']) < end:
                continue
            mid = 'espn:' + str(event['id'])
            game, = event['competitions']
            if (event['season']['slug'] != 'regular-season' or event['status']['type']['state'] != 'pre'
                    or not game.get('timeValid') or aware(game['date']) != aware(event['date'])):
                raise ValueError('unresolved scheduled regular fixture '+mid)
            teams = {t['homeAway']:'espn:'+str(t['team']['id']) for t in game['competitors']}
            f = parent['payload']['fixtures'][mid]
            if (f['cohort'] != code or aware(f['kickoff_at']) != aware(event['date']) or
                    (f['home'], f['away']) != (teams['home'],teams['away'])):
                raise ValueError('schedule and first source differ: '+mid)
            ids.append(mid)
            all_fixtures.append(f)
        source_ids = {m for m,f in parent['payload']['fixtures'].items() if f['cohort']==code
                      and start<=aware(f['kickoff_at'])<end}
        if set(ids) != source_ids or len(ids) != len(set(ids)) or len(ids) != window['expected']:
            raise ValueError('incomplete or duplicate schedule: '+window['name'])
        slates[window['name']] = sorted(ids)
    identity = dict(version=spec['version'], competitions=[code], pairs={}, protocol=spec,
                    protocol_sha256=sha(protocol), calendar_sha256=sha(board),
                    original_registration=parent['record_sha256'], scope_sha256=sha(scope),
                    roadmap_sha256=sha(goals), stages=read(goals)['research_roadmap_20261124']['stages'],
                    current_sha256=weights['artifact_sha256'], test_sha256=weights['test_artifact_sha256'],
                    compatibility_sha256=sha(crosswalk), slates=slates)
    plan = ledger.register(all_fixtures, identity, TRACK, out, freeze_root=freeze_root)
    index = out/'plan.json'
    if index.exists():
        if read(index) != plan:
            raise ValueError('conflicting registered windows')
    else:
        write_new(index, plan)
    return plan


def ranked(eligible):
    return sorted(eligible, key=lambda r:(-r['ranking_probability'], aware(r['kickoff_at']), r['match_id']))[:3]


def lock_publication(registered, window, entries, message, out, *, freeze_root=DEFAULT_ROOT):
    plan = ledger._read(registered, read(registered/'plan.json'))
    info = plan['payload']['identity']
    universe = plan['payload']['fixtures']
    mids = info['slates'][window]
    if set(entries) != set(mids):
        raise ValueError('every registered slot must be represented')
    rows, eligible = {}, []
    for mid in sorted(mids):
        f = universe[mid]
        item = entries[mid]
        record = ledger._read(out, item['authored'])
        pair = ledger._read(out, item['pair'])
        scope = ('home','away','cohort','match_id')
        if (any(f[k]!=record['fixture'][k] or f[k]!=pair['fixture'][k] for k in scope)
                or not aware(f['kickoff_at'])==aware(record['fixture']['kickoff_at'])==aware(pair['fixture']['kickoff_at'])
                or aware(pair['recorded_at'])>aware(record['recorded_at']) or pair['track']!='current-test-pair'
                or record['payload']['current_test_reference']['sha256']!=pair['record_sha256']):
            raise ValueError('first current/test pair or authored row differs: '+mid)
        block = delivery_request(record)['text']
        if message.count(block) != 1:
            raise ValueError('actual prepared message must contain each original block once')
        p = record['payload']
        base = dict(match_id=mid, kickoff_at=f['kickoff_at'], authored=item['authored'], pair=item['pair'],
                    displayed_scores=p['displayed_scores'], wdl=p.get('wdl'), total_direction=p.get('total_direction'),
                    total_line=p.get('total_line'), ranking_probability=None)
        if p['authored_display']['status']=='ISSUED' and record['selection_audit'].get('top3_ready'):
            h,a = map(int,p['authored_display']['first'].split('-'))
            probs = []
            for role, key in [('current_model','current_sha256'),('test_model','test_sha256')]:
                native = pair['payload'][role]
                if native.get('source_artifact_sha256') != info[key] or digest(native['matrix']) != native['matrix_hash']:
                    raise ValueError('native ranking artifact or matrix differs: '+mid)
                prob = native['matrix'][h][a]
                if type(prob) not in (int,float) or not math.isfinite(prob) or not 0<=prob<=1:
                    raise ValueError('invalid authored-cell probability')
                probs.append(prob)
            base['ranking_probability'] = math.sqrt(probs[0]*probs[1])
            eligible.append(base)
        rows[mid] = base
    identity = dict(version=info['version'], parent=plan['record_sha256'], window=window, slots=3,
                    rows=rows, selected=[r['match_id'] for r in ranked(eligible)],
                    body_sha256=hashlib.sha256(message.encode('utf8')).hexdigest())
    ref = ledger.register([universe[mid] for mid in mids], identity, TRACK+'-'+window, out, freeze_root=freeze_root)
    if (out/'publication.json').exists():
        if read(out/'publication.json') != ref:
            raise ValueError('first placement changed')
    else:
        write_new(out/'publication.json',ref)
    return ref


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sp=parser.add_subparsers(dest='command',required=True)
    lock=sp.add_parser('register')
    for k in ('protocol','scope','goals','board','registered','registry','crosswalk','out'):
        lock.add_argument('--'+k,type=Path,required=True)
    publish=sp.add_parser('lock-batch')
    for k in ('registered','entries','body','out'):
        publish.add_argument('--'+k,type=Path,required=True)
    publish.add_argument('--window',required=True)
    args=parser.parse_args()
    if args.command=='register':
        print(register(args.protocol,args.scope,args.goals,args.board,args.registered,args.registry,args.crosswalk,args.out))
    else:
        print(lock_publication(args.registered,args.window,read(args.entries),args.body.read_text(encoding='utf8'),args.out))
