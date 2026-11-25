"""Replay J1 lineups with explicit conventional roles from the retained ESPN names and abbreviations."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from . import lineup_cutoff as cutoff
from . import lineup_study as study
from .goal_state_study import read_lines, sha, write_new
from .paired_kpi_inference import compare
from .r9_score_study import verified_archive

PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261125-r19-j1-positions/protocol.json'
VERSION = 'R19-J1-provider-position-roles-1'
PROTOCOL_SHA = '79c475cc0311ddc605071bd579103c91c6e41e9fc89f773e7f30a6248adfe3ef'


def taxonomy():
    spec = study.control.read(PROTOCOL)
    if sha(PROTOCOL) != PROTOCOL_SHA or spec['version'] != VERSION:
        raise ValueError('J1 position taxonomy differs from registration')
    return {(code, name): role for role, pairs in spec['taxonomy'].items() for code, name in pairs.items()}


def normalize(rows):
    roles = taxonomy()
    corrected = []
    for row in rows:
        source = dict(row)
        for side in study.SIDES:
            key = side + ('_roster' if side + '_roster' in row else '_starters')
            players = row.get(key)
            if not isinstance(players, list):
                continue
            source[key] = []
            for player in players:
                if not isinstance(player, dict):
                    source[key].append(player)
                    continue
                code, name = player.get('position_abbreviation'), player.get('position_group')
                role = roles.get((code, name))
                if role and player.get('position', role) in (None, role, name):
                    source[key].append(dict(player, position=role, position_group=role, position_abbreviation=role))
                elif code and name and (code not in ('G', 'D', 'M', 'F') or (code, name) not in roles):
                    source[key].append(dict(player, position=None, position_abbreviation=None, position_group=None))
                else:
                    source[key].append(player)
        corrected.append(source)
    return corrected


def compare_original(current, parent):
    before = study.archive.read_gzip(parent / 'forecasts.jsonl.gz')
    after = study.archive.read_gzip(current / 'forecasts.jsonl.gz')
    control_arms = ('r5', 'r7', 'r10', 'market', 'form_only', 'fixed_form')
    results = {'evaluation': [], 'recent': []}
    for old, new in zip(before, after, strict=True):
        if (any(old[k] != new[k] for k in ('id', 'cohort', 'window', 'date', 'home_id', 'away_id', 'actual', 'line', 'source_sha256'))
                or any(old['forecast'][k] != new['forecast'][k] for k in control_arms)):
            raise ValueError('unaltered fixture, form-only or market reference differs: ' + old['id'])
        tips = {k: new['forecast'][k] for k in ('candidate', 'fixed_full', 'fixed_form')}
        tips.update(r15=old['forecast']['candidate'], old_full=old['forecast']['fixed_full'])
        results[new['window']].append(dict(id=new['id'], date=new['date'], cohort=study.CODE,
                                           line=new['line'], actual=new['actual'], forecast=tips))
    comparisons = {}
    for window, rows in results.items():
        comparisons[window] = {
            tag: compare(rows, baseline=baseline, candidate=candidate)
            for tag, baseline, candidate in [('issued', 'r15', 'candidate'), ('fixed_vs_original', 'old_full', 'fixed_full'),
                                               ('fixed_personnel', 'fixed_form', 'fixed_full')]}
    return comparisons


def run(r5, r7, r10, parent, roster_file, out):
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as tmp:
        root = Path(tmp)
        games, _, _, _, old_fit, settings, metadata = cutoff.load(r5, r7, parent, roster_file, root)
        rows, fingerprint = study.read_rosters(roster_file)
        if (len(games) != 2822 or len(rows) != 2822 or fingerprint != metadata['rosters_sha256']
                or metadata['roster_rows'] != len(rows)):
            raise ValueError('required full J1 schedule or original R15 snapshot differs')
        shot_archive = 'replay/main.tar.gz'
        shot_manifest = study.control.read(r10 / 'manifest.json')
        if metadata['archive_sha256']['r10'] != shot_manifest['files'][shot_archive]:
            raise ValueError('R10 reference archive differs')
        verified_archive(r10, shot_manifest, shot_archive, root / 'r10')
        fifth = [dict(row, window=window) for window in ('evaluation', 'recent')
                 for row in read_lines(root / 'r5/study' / (window + '.jsonl'))]
        references = (fifth, study.archive.read_gzip(root / 'r7/main/forecasts.jsonl.gz'),
                      study.archive.read_gzip(root / 'r10/main/jpn.1/forecasts.jsonl.gz'))
        inputs = dict(metadata, experiment=VERSION, protocol_sha256=PROTOCOL_SHA,
                      r15_protocol_sha256=metadata['protocol_sha256'], r15_inputs_sha256=sha(parent / 'inputs.json'),
                      r15_original_fit_sha256=sha(parent / 'fit.json'), r15_original_forecasts_sha256=sha(parent / 'forecasts.jsonl.gz'))
        package = root / 'result'
        package.mkdir()
        study.replay(games, normalize(rows), references, settings, inputs, package)
        new_fit = study.control.read(package / 'fit.json')
        for key in ('fitted', 'doses', 'trials'):
            if new_fit[key]['form'] != old_fit[key]['form']:
                raise ValueError('pure team form changed when only player positions were remapped')
        write_new(package / 'r15-comparison.json', compare_original(package, parent))
        package.rename(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('r5', 'r7', 'r10', 'parent', 'rosters', 'out'):
        parser.add_argument('--' + key, type=Path, required=True)
    opts = parser.parse_args()
    run(opts.r5, opts.r7, opts.r10, opts.parent, opts.rosters, opts.out)
