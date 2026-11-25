"""Issue the registered domestic-form model on the four continental R10 cohorts."""

import argparse
from datetime import timezone
from pathlib import Path
import tempfile

from research.analysis_freeze import DEFAULT_ROOT, aware, verify
from storage_lock import exclusive_lock
from . import all_scope_model as base
from . import continuation_forward as r7
from . import domestic_form as model
from . import goal_state_forward as ledger
from . import r10_shot_model as decision
from .goal_state_study import read_lines, sha, write_new
from .kpi_validation import fixture_identity
from .r8_score_study import read
from .r9_score_study import verified_archive

TRACK = 'research-r13-domestic-form-20261125'


def issue(r5, r7_dir, r10, study, domestic, protocol, out, freeze_root):
    source_hash = sha(domestic)
    if read(protocol)['version'] != model.VERSION or read(study/'inputs.json')['domestic_sha256'] != source_hash:
        raise ValueError('the research protocol or domestic source differs from the fitted study')
    manifests = {p: read(p/'manifest.json') for p in (r5, r7_dir, r10)}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        verified_archive(r5, manifests[r5], 'forward-first-issues.tar.gz', root/'prices')
        verified_archive(r10, manifests[r10], 'forward/first-issues.tar.gz', root/'source')
        verified_archive(r10, manifests[r10], 'replay/elite.tar.gz', root/'baseline')
        prices, source = root/'prices/issued', root/'source'
        raw = read(prices/'receipts.json')
        previous = read(source/'receipts.json')
        registration = ledger._read(source, previous['registration'])
        population = registration['payload']['fixtures']
        if set(population) != set(previous['matches']):
            raise ValueError('R10 registered population incomplete')
        weights, baselines, hashes = {}, {}, {}
        for code in model.CODES:
            if code == 'afc.champions':
                baseline = root/'baseline/elite'/code/'r7-forward.json'
            else:
                verified_archive(r7_dir, manifests[r7_dir], 'models/'+code+'.tar.gz', root/'baseline'/code)
                baseline = root/'baseline'/code/'main/forward.json'
            folder = study/code
            recipe = read(folder/'forward.json')
            if (recipe['version'], recipe['competition'], recipe['domestic_through'], recipe['fit_sha256'],
                    recipe['baseline_sha256']) != (model.VERSION, code, read(study/'inputs.json')['domestic_through'],
                                                  sha(folder/'fit.json'), sha(baseline)):
                raise ValueError('prospective model or source identity mismatch: ' + code)
            hashes[code] = sha(folder/'forward.json')
            weights[code], baselines[code] = recipe, read(baseline)
        identity = dict(version=model.VERSION, competitions=list(model.CODES), protocol_sha256=sha(protocol),
                        domestic_sha256=source_hash, recipe_sha256=hashes, r10_registration=registration['record_sha256'],
                        pairs={f'vs_{k}':[k,'candidate'] for k in ('r7','r10','local_r7','market')})
        plans = [v for p in out.glob('*-v000001.json') if (v := read(p)).get('track') == TRACK+'-registration']
        if len(plans) > 1:
            raise ValueError('multiple registrations in output')
        old_plan = verify(plans[0]) if plans else None
        cutoff = aware(old_plan['recorded_at'] if old_plan else ledger._now()).astimezone(timezone.utc)
        records, expired = [], []
        history = model.DomesticHistory(read_lines(domestic))
        for mid, f in population.items():
            code = f['cohort']
            if code not in weights:
                continue
            if aware(f['kickoff_at']) <= cutoff:
                expired.append(mid)
                continue
            old = ledger._read(source, previous['matches'][mid])
            original = ledger._read(prices, raw['matches'][mid])
            p = old['payload']
            if (fixture_identity(f) != fixture_identity(original['fixture']) or fixture_identity(f) != fixture_identity(old['fixture'])
                    or p['registration_sha256'] != registration['record_sha256']
                    or p['odds_source_first_issue'] != original['record_sha256']
                    or not aware(original['recorded_at']) <= aware(old['recorded_at']) <= cutoff):
                raise ValueError('earlier source identity or chronology differs: ' + mid)
            if not all(f[k].startswith('espn:') for k in ('home','away')):
                raise ValueError('unsupported continental provider identity: ' + mid)
            teams = [model.team_id(f[k].split(':')[1]) for k in ('home','away')]
            game = r7.frozen_game(f, original['payload'])
            day = aware(f['kickoff_at']).astimezone(timezone.utc).date().isoformat()
            weights_day = weights[code]['through']
            if (max(weights_day, baselines[code]['through'], history.through) >= cutoff.date().isoformat()
                    or day < cutoff.date().isoformat() or base.actual_line(game) != p['line']):
                raise ValueError('input reaches issue date or frozen price differs: ' + mid)
            x, n = history.context(teams, day, cutoff=cutoff.date().isoformat())
            prior = r7.matrix(game, baselines[code])
            local = base.pick(prior, p['line'])
            control = local if code == 'afc.champions' else p['forecasts']['r7']
            full = model.adjust(prior, x, weights[code]['fitted'])
            chosen, heads = decision.decisions(prior, full, weights[code]['doses'], p['line'], control)
            cells = [tuple(map(int, score.split('-'))) for score in chosen['top3']]
            score = heads['score']
            payload = dict(version=model.VERSION, home_name=p['home_name'], away_name=p['away_name'],
                           forecasts=dict(candidate=chosen, local_r7=local, r7=p['forecasts']['r7'],
                                          r10=p['forecasts']['candidate'], market=p['forecasts']['market']),
                           quality=dict(p_first=float(score[cells[0]]), p_three=float(sum(score[cell] for cell in cells))),
                           line=p['line'], domestic_games=n, features=x.tolist(), doses=weights[code]['doses'],
                           training_through=weights_day, domestic_through=history.through, domestic_sha256=source_hash,
                           recipe_sha256=hashes[code], r10_first_issue=old['record_sha256'],
                           odds_source_first_issue=original['record_sha256'], odds_cutoff_at=original['fixture']['cutoff_at'])
            records.append((f, payload))
        identity['already_started'] = sorted(expired)
        if old_plan and old_plan['payload']['identity'] != identity:
            raise ValueError('already registered with different inputs')
        plan = ledger.register([f for f,_ in records], identity, TRACK, out, freeze_root=freeze_root)
        results = {}
        for fixture, payload in records:
            first = dict(fixture, cutoff_at=plan['recorded_at'])
            results[first['match_id']] = ledger.issue(plan, first, payload, TRACK, out, freeze_root=freeze_root)
        write_new(out/'receipts.json', dict(registration=plan, matches=results))
        return plan


def run(r5, r7_dir, r10, study, domestic, protocol, out, *, freeze_root=DEFAULT_ROOT):
    out.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(out/'.batch.lock'):
        index = out/'receipts.json'
        if index.exists():
            plan = read(index)['registration']
            previous = ledger._read(out, plan)
            ident = previous['payload']['identity']
            if (previous['payload']['track'] != TRACK or ident['protocol_sha256'] != sha(protocol)
                    or ident['domestic_sha256'] != sha(domestic) or any(
                        ident['recipe_sha256'][c] != sha(study/c/'forward.json') for c in model.CODES)):
                raise ValueError('stored first issue differs from requested experiment')
        else:
            plan = issue(r5, r7_dir, r10, study, domestic, protocol, out, freeze_root)
        pending = ledger.review(out, [], out, by_competition=True)
        if not (out/'pending.json').exists():
            write_new(out/'pending.json', pending)
        return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('r5','r7','r10','study','domestic','protocol','out'):
        parser.add_argument('--'+key, type=Path, required=True)
    a = parser.parse_args()
    print(run(a.r5, a.r7, a.r10, a.study, a.domestic, a.protocol, a.out))
