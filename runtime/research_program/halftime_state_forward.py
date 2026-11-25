"""Preview or issue the R20 research firsts from the ten signed R18 J1 fixtures.

run(study, r18, out=None, *, issue=False, freeze_root=DEFAULT_ROOT) reads only
the historical fit/forward/input identities and original prematch receipts.
Preview returns fixtures, payloads, ranking and registration identity in memory.
Issuance commits one whole batch at freeze_root/TRACK and exports its firsts
to out. A retry can read back an already committed batch after kickoff.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import numpy as np

from publication import atomic_json, deliver, sync_directory
from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from storage_lock import exclusive_lock
from . import all_scope_model as base
from . import goal_state_forward as ledger
from . import halftime_state as model
from . import halftime_state_study as history
from . import r10_shot_model as decision
from .future_identity import fixture_identity
from .goal_state_study import sha
from .kpi_validation import _first
from .paired_kpi_inference import _decisions, _line
from .r8_score_study import read
from .territory_forward import _document

CODE = 'jpn.1'
TRACK = 'research-r20-j1-latent-halftime-20261125'
PARENT_TRACK = 'research-r18-j1-prior-fixture-information-20261125'
PARENT_VERSION = 'R18-J1-prior-fixture-information-1'
# The original registration binds fixture identities and payloads. The second
# digest binds each actual first record (including its issue clock) by match ID.
PARENT_REGISTRATION = '9ba2b89ecb7e504ce9eef40701cbf002082c7548df3f06bce458b286852249be'
PARENT_FIRSTS = '1f531e2fea760d22b4f9912aa5caf8b4fe8f19e8810aa8a24057c11e0644cef9'
HALVES_SHA256 = '923dfda86ab85343a59a3b97f9cdeb816633fd47c29d450a3934b57ec7565839'
# Exact historical export designated for this prospective window, including
# the full-panel refit, which differs from fit.json's pre-2024 calibration.
STUDY_SHA256 = {
    'inputs.json': 'ac33a3f8e5807c65865845e848f4f12bf9d55aab883e52daa6e80737600d2ad4',
    'fit.json': '10abdbd335fe056225186af608ceed189c036b6a52411f0d1b3fdd25324d24d7',
    'forward.json': 'acf590a134e26d189a1890b58207423a5386b434c5db6ae6a3960e7a329a3465',
}
THROUGH = '2026-11-21'
HEADS = ('score', 'wdl', 'total')
PAIRS = {f'vs_{arm}': [arm, 'candidate'] for arm in ('r7', 'fixed_full', 'r10', 'market', 'r18')}


def _now():
    return datetime.now(timezone.utc)


def _first_ref(folder, ref, track, *, scopes=None):
    if (not re.fullmatch(r'[0-9a-f]{16}-v000001', ref['id'])
            or ref['file'] != ref['id'] + '.json'):
        raise ValueError('invalid first-receipt filename')
    record = _first(ledger._read(folder, ref), track)
    scope = {k: record[k] for k in ('match_id', 'stage', 'track')}
    if (ref['id'] != digest(scope)[:16] + '-v000001'
            or record['match_id'] != record['fixture']['match_id']
            or ref['recorded_at'] != record['recorded_at']
            or ref['recorded_at_utc'] != aware(record['recorded_at']).astimezone(timezone.utc).isoformat()):
        raise ValueError('first receipt scope or issue clock differs')
    if scopes is not None and read(scopes / digest(scope) / '000001.json') != record:
        raise ValueError('canonical ledger differs from archived first')
    return record


def _parent(folder, at):
    index, index_sha = _document(folder / 'receipts.json')
    reg = _first_ref(folder, index['registration'], PARENT_TRACK + '-registration')
    body = reg['payload']
    fixtures, spec = body['fixtures'], body['identity']
    earliest = min(aware(f['kickoff_at']) for f in fixtures.values())
    firsts = {mid: ref['sha256'] for mid, ref in index['matches'].items()}
    if (reg['record_sha256'] != PARENT_REGISTRATION or digest(firsts) != PARENT_FIRSTS
            or body['track'] != PARENT_TRACK or reg['idempotency_key'] != digest(body)
            or reg['match_id'] != PARENT_TRACK + '-plan:' + digest(body)
            or aware(reg['fixture']['kickoff_at']) != earliest
            or not aware(spec['inputs_cutoff_at']) <= aware(reg['recorded_at']) <= at < earliest
            or spec['version'] != PARENT_VERSION or spec['competitions'] != [CODE]
            or spec['window'] != 'nov28_29' or spec['training_through'] != THROUGH
            or len(fixtures) != 10 or fixtures.keys() != firsts.keys() or fixtures.keys() != spec['payload_sha256'].keys()):
        raise ValueError('expected the complete original R18 firsts before earliest kickoff')
    records = {}
    for mid, f in fixtures.items():
        r = _first_ref(folder, index['matches'][mid], PARENT_TRACK)
        p = r['payload']
        if (r['match_id'] != mid or f['match_id'] != mid or f['cohort'] != CODE
                or fixture_identity(r['fixture']) != fixture_identity(f)
                or not aware(reg['recorded_at']) <= aware(r['fixture']['cutoff_at']) <= aware(r['recorded_at']) <= at
                or r['idempotency_key'] != reg['record_sha256'] or p['registration_sha256'] != reg['record_sha256']
                or digest({k: v for k, v in p.items() if k != 'registration_sha256'}) != spec['payload_sha256'][mid]
                or p['version'] != PARENT_VERSION or p['training_through'] != THROUGH
                or aware(p['inputs_cutoff_at']) != aware(spec['inputs_cutoff_at'])
                or any('espn:' + str(p[s + '_id']) != f[s] for s in ('home', 'away'))
                or any(p[k] != spec['r15_inputs'][k] for k in ('panel_sha256', 'r7_forward_sha256', 'r10_recipe_sha256'))
                or digest(p['r7_grid']) != p['r7_grid_sha256']):
            raise ValueError('R18 identity, source grid, weights or first clock differs: ' + mid)
        records[mid] = r
    return reg, records, index_sha


def _study(folder, parent):
    fit, fit_sha = _document(folder / 'fit.json')
    recipe, recipe_sha = _document(folder / 'forward.json')
    inputs, inputs_sha = _document(folder / 'inputs.json')
    spec = parent['payload']['identity']
    common = dict(version=model.VERSION, competition=CODE, protocol_sha256=history.PROTOCOL_SHA256,
                  panel_sha256=spec['r15_inputs']['panel_sha256'], halves_sha256=HALVES_SHA256)
    if ({'fit.json': fit_sha, 'forward.json': recipe_sha, 'inputs.json': inputs_sha} != STUDY_SHA256
            or sha(history.PROTOCOL) != common['protocol_sha256']
            or any(doc.get(k) != v for doc in (inputs, fit, recipe) for k, v in common.items())
            or recipe['fit_sha256'] != fit_sha or recipe['training_through'] != THROUGH
            or type(recipe['training_games']) is not int or recipe['training_games'] != spec['r15_history_coverage']['scheduled']
            or list(fit['bounds']) != list(history.study.BOUNDS) or recipe['doses'] != fit['doses']
            or set(recipe['doses']) != set(HEADS)
            or any(type(v) not in (int, float) or v not in decision.DOSES for v in recipe['doses'].values())):
        raise ValueError('historical panel, halves, protocol, validation fit, training end or doses differ')
    coverage = fit['coverage']
    folds = ('training', 'validation', 'evaluation', 'recent')
    if (set(coverage) != set(folds) or any(type(s[k]) is not int for s in coverage.values()
                                           for k in ('fixtures', 'verified_halves'))
            or any(not 0 <= s['verified_halves'] <= s['fixtures'] for s in coverage.values())
            or sum(s['fixtures'] for s in coverage.values()) != recipe['training_games']
            or fit['calibration']['training_observed'] != coverage['training']['verified_halves']
            or recipe['calibration']['training_observed'] != sum(s['verified_halves'] for s in coverage.values())):
        raise ValueError('historical and full-refit populations differ')
    locks = {folder / 'fit.json': fit_sha, folder / 'forward.json': recipe_sha, folder / 'inputs.json': inputs_sha,
             history.PROTOCOL: common['protocol_sha256']}
    return recipe, dict(common, fit_sha256=fit_sha, recipe_sha256={CODE: recipe_sha}, study_inputs_sha256=inputs_sha,
                        training_through=THROUGH, training_games=recipe['training_games'],
                        calibration_sha256=digest(recipe['calibration']), doses=recipe['doses']), locks


def _forecast(record, recipe):
    source = record['payload']
    line = _line(source)
    prices = source['prices']
    if (prices['total'] or {}).get('line') != line:
        raise ValueError('signed total differs from the original market observation')
    old = source['forecasts']
    prior = np.asarray(source['r7_grid'], dtype=float)
    original = old['r7']
    _decisions(original, None, line)
    if (not original.get('primary') or not original.get('top3') or original.get('wdl') not in base.SIDES
            or original.get('total_line') != line or (original.get('total_side') is None) != (line is None)):
        raise ValueError('incomplete signed R7 score, WDL or total')
    full = model.adjusted(prior, recipe['calibration'])
    if np.array_equal(full, prior):
        full = prior
    candidate, heads = decision.decisions(prior, full, recipe['doses'], line, original)
    fixed = original if full is prior else base.pick(full, line)
    forecasts = dict(r7=original, fixed_full=fixed, candidate=candidate, r10=old['r10'], market=old['market'],
                     r5=old['r5'], r15=old['r15'], r18=old['candidate'])
    for pick in forecasts.values():
        _decisions(pick, None, line)
    cells = [tuple(map(int, s.split('-'))) for s in candidate['top3']]
    score = heads['score']
    quality = dict(p_first=float(score[cells[0]]), p_three=float(sum(score[cell] for cell in cells)),
                   wdl=np.bincount(base.RESULT.ravel(), weights=heads['wdl'].ravel(), minlength=3).tolist())
    fields = ('home_name', 'away_name', 'home_id', 'away_id', 'line', 'prices', 'market_evidence', 'market_mode',
              'source_market_status', 'fixture_sources', 'r7_grid', 'r7_grid_sha256', 'r7_first_issue',
              'odds_source_first_issue', 'r10_first_issue', 'r7_forward_sha256', 'r10_recipe_sha256', 'source_slots',
              'r15_registration_sha256', 'r15_first_sha256', 'r15_rank', 'r15_seat', 'r15_prior_display')
    return dict({k: source[k] for k in fields}, forecasts=forecasts, quality=quality,
                r18_first_sha256=record['record_sha256'], r18_recorded_at=record['recorded_at'],
                r18_rank=source['rank'], r18_seat=source['seat'],
                head_sources={k: 'signed_R7' if m is prior else 'R20' for k, m in heads.items()},
                head_matrix_sha256={k: digest(m.tolist()) for k, m in heads.items()})


def _prepare(study, r18, at):
    parent, records, index_sha = _parent(r18, at)
    fixtures = parent['payload']['fixtures']
    recipe, binding, locks = _study(study, parent)
    if recipe['training_through'] >= at.astimezone(timezone.utc).date().isoformat():
        raise ValueError('historical training reaches the input cutoff')
    identity = dict(binding, competitions=[CODE], pairs=PAIRS, formal_kpi_eligible=False,
                    calibration=recipe['calibration'],
                    r18_registration_sha256=parent['record_sha256'], r18_receipts_sha256=index_sha,
                    r18_first_sha256={m: r['record_sha256'] for m, r in records.items()},
                    inputs_cutoff_at=at.isoformat(), slots_per_competition=3)
    payloads = {}
    for mid, record in records.items():
        p = _forecast(record, recipe)
        p.update(binding, recipe_sha256=binding['recipe_sha256'][CODE], formal_kpi_eligible=False,
                 r18_registration_sha256=parent['record_sha256'], inputs_cutoff_at=at.isoformat())
        payloads[mid] = p
    ranking = sorted(fixtures, key=lambda m: (-payloads[m]['quality']['p_first'], aware(fixtures[m]['kickoff_at']), m))
    selected = []
    for rank, mid in enumerate(ranking, 1):
        p = payloads[mid]
        p.update(rank=rank, seat=rank if rank <= 3 else None)
        if p['seat']:
            selected.append(dict(match_id=mid, seat=rank, p_first=p['quality']['p_first'], **p['forecasts']['candidate']))
    identity.update(ranking=ranking, selected={CODE: selected}, scheduled=len(fixtures), filled=len(selected),
                    without_total_line=sorted(m for m, p in payloads.items() if p['line'] is None),
                    payload_sha256={m: digest(p) for m, p in payloads.items()})
    locks[r18 / 'receipts.json'] = index_sha
    # ledger.save writes the full first (including record_sha256) as canonical JSON bytes.
    for record in (parent, *records.values()):
        locks[r18 / (record['freeze_id'] + '.json')] = digest(record)
    return fixtures, payloads, identity, locks


def _readback(batch, fixtures, payloads, identity):
    folder, scopes = batch / 'issued', batch / 'scopes'
    index = read(folder / 'receipts.json')
    reg = _first_ref(folder, index['registration'], TRACK + '-registration', scopes=scopes)
    body = dict(track=TRACK, fixtures=fixtures, identity=identity)
    earliest = min(aware(f['kickoff_at']) for f in fixtures.values())
    observed = _now()
    if (reg['payload'] != body or reg['idempotency_key'] != digest(body)
            or reg['match_id'] != TRACK + '-plan:' + digest(body) or aware(reg['fixture']['kickoff_at']) != earliest
            or not aware(identity['inputs_cutoff_at']) <= aware(reg['recorded_at']) <= observed
            or index['matches'].keys() != fixtures.keys()):
        raise ValueError('canonical registration differs from R20 inputs, population or seats')
    for mid, ref in index['matches'].items():
        r = _first_ref(folder, ref, TRACK, scopes=scopes)
        if (r['idempotency_key'] != reg['record_sha256'] or r['fixture'] != dict(fixtures[mid], cutoff_at=reg['recorded_at'])
                or r['payload'] != dict(payloads[mid], registration_sha256=reg['record_sha256'])
                or aware(r['recorded_at']) >= earliest or aware(r['recorded_at']) > observed):
            raise ValueError('canonical R20 first differs from its registration: ' + mid)
    return index


def _publish(canonical, fixtures, payloads, identity, locks):
    earliest = min(aware(f['kickoff_at']) for f in fixtures.values())
    if _now() >= earliest:
        raise ValueError('too late to open R20 before earliest kickoff')
    # Neither a partial batch nor a changed slate can claim a permanent first.
    with TemporaryDirectory(dir=canonical.parent, prefix='.r20-pending-') as temporary:
        batch = Path(temporary) / 'batch'
        folder, scopes = batch / 'issued', batch / 'scopes'
        plan = ledger.register(list(fixtures.values()), identity, TRACK, folder, freeze_root=scopes)
        matches = {m: ledger.issue(plan, dict(f, cutoff_at=plan['recorded_at']), payloads[m], TRACK, folder,
                                   freeze_root=scopes) for m, f in fixtures.items()}
        for ref in (plan, *matches.values()):
            ref['recorded_at_utc'] = aware(ref['recorded_at']).astimezone(timezone.utc).isoformat()
            with (folder / ref['file']).open('rb') as handle:
                os.fsync(handle.fileno())
        atomic_json(folder / 'receipts.json', dict(registration=plan, matches=matches))
        index = _readback(batch, fixtures, payloads, identity)
        if any(sha(path) != h for path, h in locks.items()):
            raise ValueError('a bound study or parent input changed during issue')
        sync_directory(scopes)
        sync_directory(batch)
        if _now() >= earliest:
            raise ValueError('R20 batch crossed earliest kickoff before publication')
        batch.rename(canonical)
        sync_directory(canonical.parent)
    return index


def _export(source, out):
    if out.exists() and any(out.iterdir()):
        if ({p.name for p in source.iterdir()} != {p.name for p in out.iterdir()}
                or any(not (out / p.name).is_file() or p.read_bytes() != (out / p.name).read_bytes() for p in source.iterdir())):
            raise ValueError('export differs from canonical R20 firsts')
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent, prefix='.r20-export-') as tmp:
        stage = Path(tmp) / 'issued'
        stage.mkdir()
        for file in source.iterdir():
            deliver(stage / file.name, file.read_bytes())
        stage.rename(out)
        sync_directory(out.parent)


def run(study, r18, out=None, *, issue=False, freeze_root=DEFAULT_ROOT):
    study, r18 = Path(study).resolve(), Path(r18).resolve()
    if not issue:
        fixtures, payloads, identity, _ = _prepare(study, r18, _now())
        if _now() >= min(aware(f['kickoff_at']) for f in fixtures.values()):
            raise ValueError('preview crossed earliest kickoff')
        return dict(mode='preview', track=TRACK, fixtures=fixtures, payloads=payloads, identity=identity)
    if out is None:
        raise ValueError('R20 issuance requires --out')
    out, store = Path(out).resolve(), Path(freeze_root).resolve()
    canonical = store / TRACK
    if (out.is_relative_to(canonical) or canonical.is_relative_to(out)
            or any(path.is_relative_to(src) for path in (out, store, canonical) for src in (study, r18))):
        raise ValueError('R20 storage must be separate from original inputs and its export')

    def cutoff():
        if canonical.exists():
            folder = canonical / 'issued'
            ref = read(folder / 'receipts.json')['registration']
            return aware(_first_ref(folder, ref, TRACK + '-registration')['payload']['identity']['inputs_cutoff_at'])
        return _now()

    at = cutoff()
    prepared = _prepare(study, r18, at)
    store.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(store / ('.' + TRACK + '.lock')):
        if out.exists() and (not out.is_dir() or any(out.iterdir()) and not canonical.exists()):
            raise ValueError('output already contains an unbound first or other files')
        if canonical.exists():
            original_at = cutoff()
            if original_at != at:
                prepared = _prepare(study, r18, original_at)
            index = _readback(canonical, *prepared[:3])
        else:
            index = _publish(canonical, *prepared)
        _export(canonical / 'issued', out)
    return dict(mode='issued', track=TRACK, **index, ranking=prepared[2]['ranking'], selected=prepared[2]['selected'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('study', 'r18'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--out', type=Path, help='directory for exported R20 firsts with --issue')
    parser.add_argument('--issue', action='store_true', help='commit the ten research firsts before earliest kickoff')
    parser.add_argument('--freeze-root', type=Path, default=Path(DEFAULT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run(args.study, args.r18, args.out, issue=args.issue, freeze_root=args.freeze_root),
                     ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
