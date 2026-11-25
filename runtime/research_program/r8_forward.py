"""Freeze a new R8 research track beside the immutable R7 first issues."""

import argparse
import json
from pathlib import Path
import tempfile

import numpy as np

from research.analysis_freeze import DEFAULT_ROOT, aware
from . import all_scope_model as base
from . import continuation_forward as r7
from . import goal_state_forward as ledger
from . import r8_score_calibration as model
from .goal_state_study import sha, write_new
from .kpi_validation import fixture_identity
from .r8_score_study import extract, read

TRACK = 'research-r8-conditional-score-20261125'


def run(r7_evidence, r5_evidence, fit_root, protocol, output, *, freeze_root=DEFAULT_ROOT):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        extract(r7_evidence/'first-issues.tar.gz', root/'r7')
        extract(r5_evidence/'forward-first-issues.tar.gz', root/'r5')
        return issue(r7_evidence, root/'r7', root/'r5/issued', fit_root,
                     protocol, output, freeze_root=freeze_root)


def issue(evidence, previous, original, fit_root, protocol, output, *, freeze_root):
    prev_idx = read(previous/'receipts.json')
    orig_idx = read(original/'receipts.json')
    reg = ledger._read(previous, prev_idx['registration'])
    codes = reg['payload']['identity']['competitions']
    recipes, offsets, artifact_hashes = {}, {}, {}
    for code in codes:
        fit_path = fit_root/'main'/code/'fit.json'
        fit = read(fit_path)
        if (fit['competition'], fit['version']) != (code, model.VERSION):
            raise ValueError('wrong R8 calibration artifact')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extract(evidence/'models'/(code+'.tar.gz'), root)
            path = root/'main/forward.json'
            recipes[code] = read(path)
            if (sha(path) != reg['payload']['identity']['recipe_sha256'][code]
                    or recipes[code]['fit_sha256'] != fit['r7_fit_sha256']):
                raise ValueError('R8 offsets are not bound to this R7 recipe')
        offsets[code] = fit['offsets']
        artifact_hashes[code] = sha(fit_path)

    pending = []
    expired = []
    clock = aware(ledger._now())
    for mid, ref in prev_idx['matches'].items():
        old = ledger._read(previous, ref)
        f, p = old['fixture'], old['payload']
        if aware(f['kickoff_at']) <= clock:
            expired.append(mid)
            continue
        source = ledger._read(original, orig_idx['matches'][mid])
        if (fixture_identity(source['fixture']) != fixture_identity(f)
                or source['record_sha256'] != p['input_sha256']
                or aware(old['recorded_at']) >= aware(f['kickoff_at'])):
            raise ValueError('R7 source identity or timing mismatch')
        original_payload = source['payload']
        code = f['cohort']
        game = r7.frozen_game(f, original_payload)
        prior = r7.matrix(game, recipes[code])
        line = base.actual_line(game)
        r7_selection = base.pick(prior, line)
        if r7_selection != p['forecasts']['candidate'] or line != p['line']:
            raise ValueError('original R7 selection could not be reproduced: ' + mid)
        answer = model.apply(prior, offsets[code], line)
        indices = model.partitions(line)
        if not np.allclose(np.bincount(indices, weights=prior.ravel()),
                           np.bincount(indices, weights=answer.ravel()), rtol=0, atol=2e-14):
            raise ValueError('protected future score mass changed')
        new = base.pick(answer, line)
        payload = dict(forecasts=dict(candidate=new, r7=r7_selection,
            market=p['forecasts']['market']), line=line, home_name=p['home_name'],
            away_name=p['away_name'], current_test_promotion=False,
            quality=dict(p_first=float(answer.max()), p_three=float(np.partition(answer.ravel(), -3)[-3:].sum())),
            version=model.VERSION, calibration_sha256=artifact_hashes[code],
            r7_first_issue=old['record_sha256'], odds_source_first_issue=source['record_sha256'],
            archived_cutoff_at=source['fixture']['cutoff_at'])
        pending.append((f, payload))
    pending = [(f,p) for f,p in pending if aware(f['kickoff_at']) > aware(ledger._now())]
    if not pending:
        raise ValueError('no verified unstarted fixtures remain')
    identity = dict(version=model.VERSION, competitions=codes, protocol_sha256=sha(protocol),
                    pairs=dict(vs_r7=['r7','candidate'], vs_market=['market','candidate']),
                    r7_registration=prev_idx['registration']['sha256'],
                    calibration_sha256=artifact_hashes, already_started_at_registration=sorted(expired))
    plan = ledger.register([f for f,_ in pending], identity, TRACK, output, freeze_root=freeze_root)
    receipts = {}
    for f, payload in pending:
        new_fixture = dict(f, cutoff_at=ledger._now())
        receipts[f['match_id']] = ledger.issue(plan, new_fixture, payload, TRACK, output, freeze_root=freeze_root)
    write_new(output/'receipts.json', dict(registration=plan, matches=receipts))
    reviewed = ledger.review(output, [], output, by_competition=True)
    write_new(output/'pending.json', reviewed)
    return len(receipts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('r7-evidence','r5-evidence','fit-root','protocol','out'):
        parser.add_argument('--'+key, type=Path, required=True)
    args = parser.parse_args()
    print('First issues:', run(args.r7_evidence, args.r5_evidence, args.fit_root, args.protocol, args.out))
