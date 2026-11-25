"""Issue the registered R9 candidate on unstarted R8 fixtures with their frozen prices."""

import argparse
from datetime import date
from pathlib import Path
import tempfile

import numpy as np

from research.analysis_freeze import DEFAULT_ROOT, aware
from . import all_scope_model as base
from . import continuation_forward as r7
from . import goal_state_forward as ledger
from . import r9_team_shape as model
from .all_scope_study import half_life
from .goal_state_study import sha, write_new
from .kpi_validation import fixture_identity
from .r8_score_study import extract, read
from .r9_score_study import verified_archive

TRACK = 'research-r9-team-goal-buckets-20261125'


def run(r5, r7_evidence, r8_evidence, fit_root, protocol, output, *, freeze_root=DEFAULT_ROOT):
    r7_manifest = read(r7_evidence / 'manifest.json')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        verified_archive(r8_evidence, read(r8_evidence / 'manifest.json'),
                         'score/first-issues.tar.gz', root / 'r8')
        extract(r5 / 'forward-first-issues.tar.gz', root / 'r5')
        archived = root / 'r5/issued'
        old_idx = read(root / 'r8/receipts.json')
        source_idx = read(archived / 'receipts.json')
        reg = ledger._read(root / 'r8', old_idx['registration'])
        identity = reg['payload']['identity']
        if set(old_idx['matches']) != set(reg['payload']['fixtures']):
            raise ValueError('incomplete R8 registered slate')
        recipes, artifacts, hashes = {}, {}, {}
        for code in identity['competitions']:
            verified_archive(r7_evidence, r7_manifest, 'models/' + code + '.tar.gz', root / code)
            r7_path = root / code / 'main/forward.json'
            path = fit_root / 'main' / code / 'forward.json'
            recipes[code], artifacts[code] = read(r7_path), read(path)
            a = artifacts[code]
            if (a['competition'] != code or a['version'] != model.VERSION or a['r7_forward_sha256'] != sha(r7_path)
                    or a['fit_sha256'] != sha(path.with_name('fit.json')) or a['through'] != recipes[code]['through']):
                raise ValueError('mismatched R9 forward recipe: ' + code)
            hashes[code] = sha(path)
        pending, expired = [], []
        for mid, ref in old_idx['matches'].items():
            old = ledger._read(root / 'r8', ref)
            fixture, p = old['fixture'], old['payload']
            if aware(fixture['kickoff_at']) <= aware(ledger._now()):
                expired.append(mid)
                continue
            original = ledger._read(archived, source_idx['matches'][mid])
            registered = reg['payload']['fixtures'][mid]
            if (fixture_identity(fixture) != fixture_identity(registered)
                    or fixture_identity(original['fixture']) != fixture_identity(fixture)
                    or original['record_sha256'] != p['odds_source_first_issue']
                    or p['registration_sha256'] != reg['record_sha256']
                    or not aware(original['recorded_at']) <= aware(old['recorded_at']) < aware(fixture['kickoff_at'])):
                raise ValueError('first-issue chain mismatch: ' + mid)
            game = r7.frozen_game(fixture, original['payload'])
            code = game['competition']
            a = artifacts[code]
            if date.fromisoformat(a['through']) >= aware(ledger._now()).date():
                raise ValueError('training is not earlier than issue date')
            prior = r7.matrix(game, recipes[code])
            line = base.actual_line(game)
            if line != p['line']:
                raise ValueError('source line changed: ' + mid)
            local_r7 = base.pick(prior, line)
            x, exposure = model.features(a['state'], game, date.fromisoformat(game['date']).toordinal(), half_life(code))
            matrix = model.adjust(prior, x, a['beta'], a['dose'])
            picks = dict(candidate=p['forecasts']['r7'] if matrix is prior else base.pick(matrix, line), r7=p['forecasts']['r7'],
                         r8=p['forecasts']['candidate'], market=p['forecasts']['market'])
            payload = dict(forecasts=picks, line=line, home_name=p['home_name'], away_name=p['away_name'],
                           training_through=a['through'], beta=a['beta'], dose=a['dose'], prior_team_games=exposure,
                           quality=dict(p_first=float(matrix.max()), p_three=float(np.partition(matrix.ravel(), -3)[-3:].sum())),
                           recipe_sha256=hashes[code], r8_first_issue=old['record_sha256'],
                           r7_first_issue=p['r7_first_issue'], odds_source_first_issue=original['record_sha256'],
                           archived_cutoff_at=original['fixture']['cutoff_at'], version=model.VERSION,
                           different_rebuilt_r7=local_r7 if local_r7 != picks['r7'] else None)
            pending.append((fixture, payload))
        plan_id = dict(version=model.VERSION, competitions=identity['competitions'], protocol_sha256=sha(protocol),
                       recipe_sha256=hashes, r8_registration=reg['record_sha256'], already_started=sorted(expired),
                       pairs={f'vs_{k}': [k, 'candidate'] for k in ('r7', 'r8', 'market')})
        if not pending:
            raise ValueError('no unstarted R8 fixtures remain')
        plan = ledger.register([f for f, _ in pending], plan_id, TRACK, output, freeze_root=freeze_root)
        receipts = {}
        for fixture, payload in pending:
            f = dict(fixture, cutoff_at=ledger._now())
            receipts[f['match_id']] = ledger.issue(plan, f, payload, TRACK, output, freeze_root=freeze_root)
        write_new(output / 'receipts.json', dict(registration=plan, matches=receipts))
        write_new(output / 'pending.json', ledger.review(output, [], output, by_competition=True))
        return plan, len(receipts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('r5', 'r7-evidence', 'r8-evidence', 'fit-root', 'protocol', 'out'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    print(run(args.r5, args.r7_evidence, args.r8_evidence, args.fit_root, args.protocol, args.out))
