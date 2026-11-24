"""Select first/backup, WDL and totals routing on the registered selection season."""

import argparse
import json
from pathlib import Path

from train_prior_team_strength import signature
from research_program.future_validation import read, write_new
from research_program.paired_kpi_inference import compare
from research_program.prior_team_strength import allocate_picks


def apply(ledger, name, policy, strength):
    for row in ledger:
        alternatives = row['forecast']
        alternatives[name] = allocate_picks(alternatives.get('six_match_xg'), alternatives[strength], policy)


def select(rows, strength):
    candidates = ('six_match_xg', 'strength', 'incumbent_first_strength_backups')
    scores = {}
    for name in candidates:
        # The pure six-match option also falls back to strength on unseen teams.
        rule = {'scores':name, 'wdl':'six_match_xg', 'total':'six_match_xg'}
        trial = 'select_'+name
        apply(rows, trial, rule, strength)
        counts = compare(rows, baseline=strength, candidate=trial, draws=1)['tracks'][trial]['metrics']
        scores[name] = tuple(counts[m]['hits'] for m in ('score_top1','score_top3'))
    chosen = max(candidates, key=scores.get)
    metrics = compare(rows, baseline='select_six_match_xg', candidate=strength, draws=1)['tracks']
    heads = {'scores':chosen}
    for key, metric in (('wdl','wdl'), ('total','actual_ou')):
        incumbent, team = (metrics[name]['metrics'][metric]['hits'] for name in ('select_six_match_xg',strength))
        heads[key] = 'strength' if team > incumbent else 'six_match_xg'
    return heads, scores


def main(args):
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    strength = read(args.study/'selection.json')['selected_strength']
    chosen = None
    for period in ('select','known_holdout','final'):
        with (args.study/(period+'-paired.jsonl')).open() as handle:
            rows = [json.loads(line) for line in handle]
        if chosen is None:
            chosen, scores = select(rows, strength)
            write_new(out/'selection.json', {'heads':chosen, 'score_policy_hits':scores, 'strength':strength,
                      'protocol_sha256':signature(args.protocol)})
        apply(rows, 'independent_heads', chosen, strength)
        comparisons = {k:compare(rows, baseline=k, candidate='independent_heads')
                       for k in ('six_match_xg',strength,'market')}
        write_new(out/(period+'-intervals.json'), comparisons)
        with (out/(period+'-picks.jsonl')).open('x') as handle:
            for row in rows:
                handle.write(json.dumps({k:v for k,v in row.items() if k != 'forecast'} |
                             {'forecast': {k:v for k,v in row['forecast'].items() if not k.startswith('select_')}},
                             separators=(',',':'))+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('study','protocol','out'):
        parser.add_argument('--'+name, type=Path, required=True)
    main(parser.parse_args())
