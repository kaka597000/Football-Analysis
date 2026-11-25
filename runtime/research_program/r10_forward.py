"""Publish the independent R10 score and direction heads from frozen upcoming inputs."""

import argparse
from datetime import date, timezone
from pathlib import Path
import tempfile

from research.analysis_freeze import DEFAULT_ROOT, aware, verify
from . import all_scope_model as base
from . import continuation_forward as r7
from . import goal_state_forward as ledger
from . import r10_shot_model as model
from .all_scope_study import half_life
from .goal_state_study import sha, write_new
from .kpi_validation import fixture_identity
from .r8_score_study import read
from .r9_score_study import verified_archive

TRACK = 'research-r10-shot-volume-20261125'


def run(r5, r7_dir, r9_dir, main, elite, protocol, output, *, freeze_root=DEFAULT_ROOT):
    if read(protocol)['version'] != model.VERSION:
        raise ValueError('protocol version mismatch')
    done = output / 'receipts.json'
    if done.is_file():
        pending_report = ledger.review(output, [], output, by_competition=True)
        idx = read(done)
        old = ledger._read(output, idx['registration'])
        if old['payload']['track'] != TRACK or old['payload']['identity']['protocol_sha256'] != sha(protocol):
            raise ValueError('different research batch in output directory')
        if not (output/'pending.json').exists():
            write_new(output/'pending.json', pending_report)
        return idx['registration'], len(idx['matches'])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        verified_archive(r5, read(r5/'manifest.json'), 'forward-first-issues.tar.gz', root/'r5')
        verified_archive(r9_dir, read(r9_dir/'manifest.json'), 'forward/first-issues.tar.gz', root/'r9')
        source = root/'r5/issued'
        previous = root/'r9'
        raw_idx = read(source/'receipts.json')
        idx = read(previous/'receipts.json')
        reg = ledger._read(previous, idx['registration'])
        population = reg['payload']['fixtures']
        if set(idx['matches']) != set(population):
            raise ValueError('incomplete R9 first issues')
        codes = reg['payload']['identity']['competitions']
        hashes, recipes, baselines = {}, {}, {}
        for code in codes:
            if code == 'afc.champions':
                folder = elite/'elite'/code
                baseline = folder/'r7-forward.json'
            else:
                folder = main/'main'/code
                verified_archive(r7_dir, read(r7_dir/'manifest.json'), 'models/'+code+'.tar.gz', root/code)
                baseline = root/code/'main/forward.json'
            path = folder/'forward.json'
            a, b = read(path), read(baseline)
            if (a['competition'], a['version'], a['r7_forward_sha256'], a['fit_sha256'], a['through']) != (
                    code, model.VERSION, sha(baseline), sha(folder/'fit.json'), b['through']):
                raise ValueError('mismatched forward weights: '+code)
            hashes[code], recipes[code], baselines[code] = sha(path), a, b
        registrations = (read(path) for path in output.glob('*-v000001.json'))
        registered = [verify(record) for record in registrations if record.get('track') == TRACK+'-registration']
        if len(registered) > 1:
            raise ValueError('multiple plans in a single batch directory')
        as_of = aware(registered[0]['recorded_at'] if registered else ledger._now()).astimezone(timezone.utc)
        pending, expired = [], []
        for mid, ref in idx['matches'].items():
            old = ledger._read(previous, ref)
            f, p = old['fixture'], old['payload']
            if aware(f['kickoff_at']) <= as_of:
                expired.append(mid)
                continue
            original = ledger._read(source, raw_idx['matches'][mid])
            if (fixture_identity(f) != fixture_identity(original['fixture']) or fixture_identity(f) != fixture_identity(population[mid])
                    or p['registration_sha256'] != reg['record_sha256'] or p['odds_source_first_issue'] != original['record_sha256']
                    or not aware(original['recorded_at']) <= aware(old['recorded_at']) < aware(f['kickoff_at'])):
                raise ValueError('first-issue identity or time mismatch: '+mid)
            game = r7.frozen_game(f, original['payload'])
            code = game['competition']
            a = recipes[code]
            if date.fromisoformat(a['through']) >= min(aware(f['kickoff_at']), as_of).date():
                raise ValueError('training reaches the prediction date: '+mid)
            prior = r7.matrix(game, baselines[code])
            line = base.actual_line(game)
            if line != p['line']:
                raise ValueError('source line changed: '+mid)
            comparison = base.pick(prior, line)
            baseline = comparison if code == 'afc.champions' else p['forecasts']['r7']
            ctx = model.context(a['state'], game, date.fromisoformat(game['date']).toordinal(), half_life(code))
            _, full = model.alternatives(prior, ctx, a['fitted'])
            chosen, heads = model.decisions(prior, full, a['doses'], line, baseline)
            score = heads['score']
            tips = dict(candidate=chosen, r9=p['forecasts']['candidate'], r7=p['forecasts']['r7'],
                        local_r7=comparison, market=p['forecasts']['market'])
            control_name = 'refreshed_Elite_R7' if code == 'afc.champions' else 'issued_R7'
            selected_cells = [tuple(map(int, x.split('-'))) for x in chosen['top3']]
            payload = dict(version=model.VERSION, forecasts=tips, line=line, home_name=p['home_name'], away_name=p['away_name'],
                           shot_ready=ctx['ready'], prior_shot_games=ctx['exposure'], doses=a['doses'],
                           head_sources={k: control_name if m is prior else model.VERSION for k,m in heads.items()},
                           training_through=a['through'], r9_first_issue=old['record_sha256'],
                           odds_source_first_issue=original['record_sha256'], archived_cutoff_at=original['fixture']['cutoff_at'],
                           recipe_sha256=hashes[code], r7_forward_sha256=a['r7_forward_sha256'],
                           quality=dict(p_first=float(score[selected_cells[0]]),
                                        p_three=float(sum(score[cell] for cell in selected_cells))))
            pending.append((f, payload))
        if not pending:
            raise ValueError('no unstarted fixtures remain')
        identity = dict(version=model.VERSION, competitions=codes, protocol_sha256=sha(protocol), recipe_sha256=hashes,
                        r9_registration=reg['record_sha256'], already_started=sorted(expired),
                        pairs={f'vs_{k}': [k, 'candidate'] for k in ('r7','r9','local_r7','market')})
        if registered and registered[0]['payload']['identity'] != identity:
            raise ValueError('inputs or model version changed after registration')
        plan = ledger.register([f for f,_ in pending], identity, TRACK, output, freeze_root=freeze_root)
        saved = {}
        for f, payload in pending:
            # The complete recipe and inputs were available by registration. A retry
            # before kickoff can therefore reuse the identical cutoff and issue key.
            fixture = dict(f, cutoff_at=plan['recorded_at'])
            saved[f['match_id']] = ledger.issue(plan, fixture, payload, TRACK, output, freeze_root=freeze_root)
        write_new(done, dict(registration=plan, matches=saved))
        write_new(output/'pending.json', ledger.review(output, [], output, by_competition=True))
        return plan, len(saved)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for k in ('r5','r7','r9','main','elite','protocol','out'):
        parser.add_argument('--'+k, type=Path, required=True)
    a = parser.parse_args()
    print(run(a.r5, a.r7, a.r9, a.main, a.elite, a.protocol, a.out))
