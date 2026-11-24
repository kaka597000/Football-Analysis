"""Locate missed domestic first scores within the two original pre-match distributions."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from club_first_sources import build, load_stage
from club_score_review import CHOICE_COMMIT, PANEL, STUDY_DIR, git, score


FAMILIES = ('current', 'market')
RANK_FIELDS = {role + '_ranks' for role in (*FAMILIES, 'test_model')}


def recall(rows):
    counts = Counter()
    origins = Counter()
    missed_totals = Counter()
    same_family = Counter()
    for row in rows:
        counts['registered'] += 1
        ranks = [row[role + '_ranks'] for role in FAMILIES]
        if not all(ranks):
            continue
        counts['paired_matrices'] += 1
        actual = row['regulation_score']
        if actual is None:
            continue
        counts['paired_graded'] += 1
        positions = [items.index(actual) + 1 if actual in items else None for items in ranks]
        hits = []
        for role, position in zip(FAMILIES, positions):
            if position is not None:
                hits.append(role)
                counts[role + '_matrix_three'] += 1
                if position == 1:
                    counts[role + '_first'] += 1
        origins['+'.join(hits) or 'neither'] += 1
        best = min((position for position in positions if position is not None), default=None)
        counts['best_rank_' + (str(best) if best else 'outside_three')] += 1
        if best is None:
            total = sum(map(int, actual.split('-')))
            missed_totals['0-2' if total <= 2 else '3' if total == 3 else '4+'] += 1
        test_ranks = row.get('test_model_ranks')
        if test_ranks:
            same_family['available'] += 1
            if actual in test_ranks:
                same_family['matrix_three'] += 1
                if best is None:
                    same_family['additional_outside_both_families'] += 1
    metrics = ('registered', 'paired_matrices', 'paired_graded', 'current_first', 'market_first',
               'current_matrix_three', 'market_matrix_three',
               'best_rank_1', 'best_rank_2', 'best_rank_3', 'best_rank_outside_three')
    return dict(counts={k: counts[k] for k in metrics},
                matrix_three_overlap={k: origins[k] for k in ('current', 'market', 'current+market', 'neither')},
                missed_by_regulation_total={k: missed_totals[k] for k in ('0-2', '3', '4+')},
                same_team_input_test={k: same_family[k] for k in
                                      ('available', 'matrix_three', 'additional_outside_both_families')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--stage-adapter', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='new directory')
    args = parser.parse_args()
    panel = build(args.data, load_stage(args.stage_adapter), include_ranks=True)
    rows = panel['rows']
    prior = json.loads(git(Path(__file__).parent, 'show', CHOICE_COMMIT + ':' + STUDY_DIR + PANEL))
    without_ranks = [{k: v for k, v in row.items() if k not in RANK_FIELDS} for row in rows]
    if prior != dict(panel, rows=without_ranks):
        raise ValueError('the original pre-score club panel has changed')
    args.out.mkdir(parents=True, exist_ok=False)
    # Finish the outcome-free projection before opening the results used in the earlier descriptive study.
    (args.out / 'original-ranks.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    with TemporaryDirectory() as temp:
        graded = list(score(rows, args.data, Path(temp)))
    identifiers = ('id', 'batch', 'competition', 'cutoff', 'kickoff', 'regulation_score', 'result_sha256')
    rank_columns = [role + '_ranks' for role in (*FAMILIES, 'test_model')]
    with (args.out / 'club-rank-fixtures.csv').open('x', encoding='utf8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=[*identifiers, *rank_columns], lineterminator='\n')
        writer.writeheader()
        for row in graded:
            line = {k: row[k] for k in identifiers}
            line.update({k: '|'.join(row[k] or []) for k in rank_columns})
            writer.writerow(line)
    result = dict(original_choice_commit=CHOICE_COMMIT, overall=recall(graded),
                  by_original_batch={name: recall([v for v in graded if v['batch'] == name])
                                     for name in sorted({v['batch'] for v in graded})})
    (args.out / 'club-rank-recall.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
