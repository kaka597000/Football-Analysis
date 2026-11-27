"""Issue the registered corrected-position comparison beside the signed R15 Japanese firsts."""
import argparse
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from research.analysis_freeze import aware, DEFAULT_ROOT, digest
from storage_lock import exclusive_lock
from . import lineup_forward as original
from . import lineup_position_study as positions
from . import lineup_study as study
from .goal_state_study import sha

TRACK = 'research-r19-j1-position-roles-20261125'


def prepare(folders, parent, corrected, roster_file, old_firsts, retry, root):
    manifests = {k: study.control.read(p / 'manifest.json') for k, p in folders.items()}
    sources = original._sources(root, folders, manifests)
    _, baseline, _, prior_identity = original._model_inputs(root, folders, manifests, parent, roster_file, study.PROTOCOL)
    inputs, input_hash = original._document(corrected / 'inputs.json')
    fit, fit_hash = original._document(corrected / 'fit.json')
    recipe, recipe_hash = original._document(corrected / 'forward.json')
    if (inputs['experiment'] != positions.VERSION or inputs['protocol_sha256'] != positions.PROTOCOL_SHA
            or inputs['r15_inputs_sha256'] != prior_identity['study_inputs_sha256']
            or inputs['r15_original_fit_sha256'] != prior_identity['fit_sha256']
            or inputs['r15_original_forecasts_sha256'] != sha(parent / 'forecasts.jsonl.gz')
            or recipe['study_inputs_sha256'] != input_hash or recipe['fit_sha256'] != fit_hash
            or recipe['doses'] != fit['doses'] or any(recipe[k] != v for k, v in inputs.items())
            or any(inputs[k] != prior_identity[k] for k in ('panel_sha256', 'rosters_sha256', 'roster_manifest_sha256'))
            or sha(positions.PROTOCOL) != positions.PROTOCOL_SHA):
        raise ValueError('corrected-position study differs from registered protocol, snapshot or R15 firsts')
    panel = root / 'r5/panel.jsonl'
    games = study.check_panel(panel, study.CODE, last=recipe['through'])
    if len(games) != recipe['games'] or games[-1]['date'] != recipe['through']:
        raise ValueError('corrected-position recipe training population differs')
    study._schedule(games, folders['r5'], manifests['r5'])
    rows, roster_sha = study.read_rosters(roster_file)
    if roster_sha != inputs['rosters_sha256']:
        raise ValueError('J1 roster source differs from fitted model')
    rosters, _, _ = study.roster_inputs(games, positions.normalize(rows))
    history = study.LineupHistory(rosters)
    history.walk(games, study.r7.inputs(games, study.CODE)[0])
    parent_issues = original.FirstIssues.load(old_firsts)
    reg = parent_issues.registration
    fixtures = reg['payload']['fixtures']
    if (reg['payload']['track'] != original.TRACK or len(fixtures) != 10
            or reg['payload']['identity']['recipe_sha256'] != prior_identity['recipe_sha256']):
        raise ValueError('expected the original ten signed R15 weekend fixtures')
    at = aware(retry['payload']['identity']['inputs_cutoff_at'] if retry else original.ledger._now()).astimezone(timezone.utc)
    if aware(inputs['roster_snapshot_at']) > at or aware(reg['recorded_at']) > at or recipe['through'] >= at.date().isoformat():
        raise ValueError('position, recipe or registration information postdates cutoff')
    existing = {mid: parent_issues.match(f, at) for mid, f in fixtures.items()}
    pairs = dict(original.PAIRS, vs_r15=['r15', 'candidate'])
    identity = dict(prior_identity, version=positions.VERSION, pairs=pairs, roster_coverage=inputs['roster_coverage'],
                    protocol_sha256=positions.PROTOCOL_SHA, study_inputs_sha256=input_hash, fit_sha256=fit_hash,
                    recipe_sha256={study.CODE: recipe_hash}, r15_registration_sha256=reg['record_sha256'],
                    r15_original_selected=reg['payload']['identity']['selected'], inputs_cutoff_at=at.isoformat(),
                    history_cutoff=at.date().isoformat(), slots_per_competition=3,
                    already_started=reg['payload']['identity']['already_started'])
    payloads = {}
    for mid, f in fixtures.items():
        p = original._forecast(f, sources, recipe, baseline, history, identity, at, identity['history_cutoff'])
        old = existing[mid]
        unchanged = ('r5', 'r7', 'r10', 'market', 'form_only', 'fixed_form')
        if (any(p['forecasts'][arm] != old['payload']['forecasts'][arm] for arm in unchanged)
                or any(p[k] != old['payload'][k] for k in ('line', 'prices', 'r7_first_issue', 'odds_source_first_issue', 'r10_first_issue'))):
            raise ValueError('a fixed original comparator or original line changed: ' + mid)
        p['forecasts']['r15'] = old['payload']['forecasts']['candidate']
        p.update(version=positions.VERSION, r15_first_sha256=old['record_sha256'],
                 prior_display=old['payload'].get('prior_display'),
                 head_sources={k: 'R19' if v == 'R15' else v for k, v in p['head_sources'].items()})
        payloads[mid] = p
    identity.update(selected={study.CODE: original._seats(payloads)}, payload_sha256={k: digest(p) for k, p in payloads.items()})
    return fixtures, payloads, identity


def run(folders, parent, corrected, rosters, prior_issues, out, *, freeze_root=DEFAULT_ROOT, issue=False):
    store = Path(freeze_root)
    if not issue:
        with TemporaryDirectory() as tmp:
            _, _, identity = prepare(folders, parent, corrected, rosters, prior_issues, None, Path(tmp))
            return identity
    for folder in (out, store):
        folder.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(out / '.batch.lock'), exclusive_lock(store / ('.' + TRACK + '.lock')), TemporaryDirectory() as tmp:
        local = original._existing(out, track=TRACK)
        canonical = original._canonical_plan(store, track=TRACK)
        if local and canonical and local != canonical:
            raise ValueError('different original corrected-position registrations')
        old = local or canonical
        fixtures, payloads, identity = prepare(folders, parent, corrected, rosters, prior_issues, old, Path(tmp))
        return original._save(fixtures, payloads, identity, out, store, old, track=TRACK)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('r5', 'r7', 'r10', 'parent', 'study', 'rosters', 'prior-issues', 'out'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--issue', action='store_true')
    args = parser.parse_args()
    print(run(dict(r5=args.r5, r7=args.r7, r10=args.r10), args.parent, args.study, args.rosters,
              args.prior_issues, args.out, issue=args.issue))
