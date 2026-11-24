"""Compare first-score sources on one decision clock, and learn one hard choice on early disagreements."""
from collections import Counter
from math import log
import numpy as np

from archive_inputs import Bundle, frozen_nations_roster, merge_original_markets, same_fixture
from labels import ANCHOR, certified
from research.analysis_freeze import aware, digest
from research_program.three_arm_model import _matrix
from research_program.three_arm_review import settle_ou
from study import selection

BINS = ('market_margin_larger', 'other')
FAMILIES = ('team', 'market', 'xg')
SENSITIVITY = ('original_current', 'refreshed_goals', 'repair', 'xg_goals')
WINDOW_START = '2026-11-15'
STATES = ('FULL_WIN', 'HALF_WIN', 'PUSH', 'HALF_LOSS', 'FULL_LOSS')


def original_roster(repo):
    rows = merge_original_markets(repo, frozen_nations_roster(repo))
    if not all('2026-11-12' <= r['date'] <= '2026-11-17' for r in rows.values()):
        raise ValueError('fixture outside preregistered roster')
    return rows


def current_at_origin(repo, roster):
    """The original Current is a sensitivity check from the same goals family."""
    arc = Bundle(repo, 'pairs')
    values = {}
    for record in arc.freezes():
        if record['track'] != 'nations-paired-candidates':
            continue
        row = roster[record['match_id']]
        cur = record['payload']['current']
        if (record['record_sha256'] != row['pair_sha'] or cur['input'] != row['f']
                or digest(cur['matrix']) != cur['matrix_hash']
                or cur['artifact_sha256'] not in arc.raw):
            raise ValueError('original same-source reference identity differs')
        choices = selection(_matrix(cur['matrix']), row['line'])
        if choices['primary'] != cur['primary']:
            raise ValueError('original Current primary mismatch')
        values[record['match_id']] = choices
    if set(values) != set(roster):
        raise ValueError('missing original Current reference')
    return values


def margin_bin(team, market):
    if market is None:
        return None
    if team.shape != market.shape:
        raise ValueError('score support differs')
    team_top = np.unravel_index(team.argmax(), team.shape)
    market_top = np.unravel_index(market.argmax(), market.shape)
    if team_top == market_top:
        return None
    def log_mass(matrix, pos):
        return log(max(float(matrix[pos]), 1e-300))
    market_advantage = log_mass(market, market_top) - log_mass(market, team_top)
    team_advantage = log_mass(team, team_top) - log_mass(team, market_top)
    return BINS[0] if market_advantage > team_advantage else BINS[1]


def fit_selector(roster, earlier_results):
    if set(earlier_results) - set(roster) or any(r['date'] >= WINDOW_START for r in roster.values()):
        raise ValueError('held-out fixture supplied to hard selector')
    bins = {k:dict(disagreements=0, market_only=0, team_only=0, neither=0, days=set()) for k in BINS}
    paired, any_market_rescue = 0, False
    for mid,row in roster.items():
        label = earlier_results.get(mid)
        if label is None:
            continue
        if max(aware(label['observed_at']), aware(label['committed_at'])) > ANCHOR:
            raise ValueError('early label unavailable at fit origin')
        base, market = row['base'], row['market']
        if market is None:
            continue
        paired += 1
        group = margin_bin(base, market)
        if group is None:
            continue
        score = '-'.join(map(str,label['score']))
        cell = bins[group]
        team_hit = selection(base, None)['primary'] == score
        market_hit = selection(market, None)['primary'] == score
        cell['market_only'] += int(market_hit)
        cell['team_only'] += int(team_hit)
        cell['neither'] += int(not team_hit and not market_hit)
        cell['disagreements'] += 1
        cell['days'].add(row['date'])
        any_market_rescue |= market_hit
    route = {}
    for group,b in bins.items():
        b['days'] = sorted(b['days'])
        route[group] = ('market' if b['disagreements'] >= 4 and len(b['days']) >= 2
                        and b['market_only'] >= 2 and b['market_only'] > b['team_only'] else 'team')
    return dict(earlier_registered=len(roster), earlier_labels=len(earlier_results), earlier_price_pairs=paired,
                earlier_exclusive_market_rescue=any_market_rescue, disagreement_bins=bins, route=route,
                disposition='FIXED_RULE' if any_market_rescue else 'STOP_NO_EARLY_RESCUE')


def select_source(row, fitted):
    if fitted['disposition'] != 'FIXED_RULE' or aware(row['cutoff']) < ANCHOR:
        return 'team'
    return fitted['route'].get(margin_bin(row['base'],row['market']), 'team')


def main_predictions(repo, roster, native):
    earlier = {k:v for k,v in roster.items() if v['date'] < WINDOW_START}
    known, issues = certified(repo, earlier, training=True)
    if issues:
        raise ValueError('training evidence requires review')
    fit = fit_selector(earlier,known)
    earlier_current = current_at_origin(repo,roster)
    panels = {}
    for mid,row in sorted(roster.items()):
        fc = {'team':selection(row['base'],row['line']), 'original_current':earlier_current[mid]}
        hashes = {'team':row['pair_sha'], 'original_current':row['pair_sha']}
        if row['market'] is not None:
            fc['market'] = selection(row['market'], row['line'])
            hashes['market'] = row['raw_sha']
        carried = {}
        unavailable = {}
        for panel, mapping in [('repair', {'team':'refreshed_goals', 'repair':'repair'}),
                                ('xg', {'team':'xg_goals', 'xg':'xg'})]:
            extra = native[panel].get(mid)
            if extra is None:
                continue
            if not same_fixture(row['f'],extra['f']) or row['f']['neutral'] != extra['f']['neutral']:
                raise ValueError('cross-batch stage, fixture or venue differs')
            if not (aware(extra['cutoff']) <= aware(extra['recorded_at']) <= aware(extra['committed_at'])
                    <= aware(row['cutoff'])):
                unavailable[panel] = 'EARLIER_ISSUE_NOT_AVAILABLE_AT_THIS_CUTOFF'
                continue
            carried[panel] = dict(source_cutoff=extra['cutoff'], recorded_at=extra['recorded_at'],
                                  committed_at=extra['committed_at'], record_sha=extra['record_sha'])
            for src, dest in mapping.items():
                if src in extra['choices']:
                    # A different batch's total direction belongs to its own quote/line.
                    fc[dest] = dict(extra['choices'][src], total=None)
                    hashes[dest] = extra['record_sha']
        after = row['date'] >= WINDOW_START
        selected = select_source(row,fit) if after else None
        if selected:
            fc['selector'] = fc[selected]
        panels[mid] = dict(id=mid,tier=row['tier'],date=row['date'],cutoff=row['cutoff'],
                           kickoff=row['f']['kickoff_at'], original_issue_sha=row['issue_sha'],
                           signed_primary_present=bool(row.get('signed_primary_present')), authored_first=None,
                           line=row['line'], gap=row['gap'], book=row['book'], choices=fc,
                           hash_by_source=hashes, earlier_issues=carried, excluded_later_issues=unavailable,
                           selector_source=selected, disagreement_bin=margin_bin(row['base'],row['market']),
                           matrix_hashes={k:digest(m.tolist()) for k,m in
                                          [('team',row['base']),('market',row['market'])] if m is not None})
    return panels, fit, known


def clock_native(native):
    panels = {}
    for panel,rows in native.items():
        converted = {}
        for mid,row in sorted(rows.items()):
            f = row['f']
            converted[mid] = dict(id=mid, tier=f['league_level'], date=f['date'], cutoff=row['cutoff'],
                                  kickoff=f['kickoff_at'], original_issue_sha=row['record_sha'],
                                  signed_primary_present=None, authored_first=None, line=row['line'],
                                  book='pinnacle' if row['line'] is not None else None, gap=row['market_gap'],
                                  choices=row['choices'], hash_by_source={
                                      k:row['market_sha'] if k=='market' else row['record_sha'] for k in row['choices']},
                                  recorded_at=row['recorded_at'], committed_at=row['committed_at'],
                                  xg_used_rows=row['xg_used_rows'])
        panels[panel] = converted
    return panels


def scored_rows(roster, labels):
    for mid,row in roster.items():
        if mid not in labels:
            yield row, None
        else:
            yield row, '-'.join(map(str,labels[mid]['score']))


def oracle(rows, roles, *, complete=False):
    """Exact union of first picks only. Never classify a one-source hit as an exclusive rescue."""
    by_role = {k:dict(available=0, scored=0, hits=0, scored_against_other=0,
                      exclusive_among_available=0, shared_hits=0, unopposed_hits=0) for k in roles}
    union = all_missed = scored = supported = equal = different = shared = unopposed = 0
    inside_three = outside_three = incomplete_threes = 0
    support_counts = Counter()
    for row,score in rows:
        fc = {k:row['choices'][k]['primary'] for k in roles if k in row['choices']
              and row['choices'][k].get('primary') is not None}
        if complete and len(fc) != len(roles):
            continue
        support_counts['+'.join(fc) or 'none'] += 1
        for k in fc:
            by_role[k]['available'] += 1
        if score is None or not fc:
            continue
        scored += 1
        if len(fc) > 1:
            supported += 1
            if len(set(fc.values())) == 1:
                equal += 1
            else:
                different += 1
        winners = [k for k,v in fc.items() if v == score]
        all_missed += int(not winners)
        if not winners:
            threes = [row['choices'][k].get('top3') for k in fc]
            if any(p and score in p for p in threes):
                inside_three += 1
            elif all(p is not None and len(p)==3 for p in threes):
                outside_three += 1
            else:
                incomplete_threes += 1
        union += int(bool(winners))
        shared += int(len(winners)>1)
        unopposed += int(len(fc)==1 and bool(winners))
        for k in fc:
            entry = by_role[k]
            entry['scored'] += 1
            entry['scored_against_other'] += int(len(fc)>1)
            entry['hits'] += int(k in winners)
            if k in winners:
                if len(fc)==1:
                    entry['unopposed_hits'] += 1
                elif len(winners)==1:
                    entry['exclusive_among_available'] += 1
                else:
                    entry['shared_hits'] += 1
    result = dict(registered=sum(support_counts.values()), scored=scored, two_or_more=supported,
                  all_available_primaries_equal=equal, disagreement=different, covered_by_any_first=union,
                  all_available_missed=all_missed, shared_winning_matches=shared,
                  all_missed_inside_one_matrix_three=inside_three,
                  all_missed_outside_every_matrix_three=outside_three,
                  all_missed_incomplete_matrix_threes=incomplete_threes,
                  hits_with_only_one_available=unopposed, coverage_patterns=dict(sorted(support_counts.items())),
                  by_role=by_role, oracle_rate=union/scored if scored else None)
    # A best fixed source contrast needs identical coverage.
    if complete and scored:
        result['additional_vs_best_single'] = union - max(x['hits'] for x in by_role.values())
    return result


def direction_metrics(rows):
    """A switch inherits the selected matrix's three picks, W/D/L and actual line direction."""
    tracked = ('top1','matrix_top3','wdl','actual_line_full_win')
    arms = {a:{k:[0,0] for k in tracked} for a in ('team','selector')}
    flips = {k:dict(n=0,changed=0,gained=0,lost=0) for k in tracked}
    states = {a:Counter() for a in arms}
    switches = registered = labelled = 0
    for row, actual in rows:
        registered += 1
        switches += int(row['selector_source']=='market')
        if actual is None:
            continue
        labelled += 1
        h,a = map(int,actual.split('-'))
        correct_wdl = 'home' if h>a else 'away' if a>h else 'draw'
        values, choices = {}, {}
        for arm in arms:
            p = row['choices'][arm]
            outcome = settle_ou(h+a,row['line'],p['total']) if p['total'] is not None else None
            if outcome is not None:
                states[arm][outcome] += 1
            values[arm] = dict(top1=(p['primary']==actual), matrix_top3=(actual in p['top3']),
                               wdl=(p['wdl']==correct_wdl),
                               actual_line_full_win=None if outcome in (None,'PUSH') else outcome=='FULL_WIN')
            choices[arm] = dict(zip(tracked,(p['primary'],p['top3'],p['wdl'],p['total'])))
            for metric,value in values[arm].items():
                if value is not None:
                    arms[arm][metric][0] += int(value)
                    arms[arm][metric][1] += 1
        for metric in tracked:
            orig, chosen = values['team'][metric], values['selector'][metric]
            if orig is None or chosen is None:
                continue
            f = flips[metric]
            f['n'] += 1
            f['changed'] += int(choices['team'][metric]!=choices['selector'][metric])
            f['gained'] += int(chosen and not orig)
            f['lost'] += int(orig and not chosen)
    return dict(registered=registered,labelled=labelled,market_switches=switches,
                arms={a:{k:dict(hits=h if n else None,n=n) for k,(h,n) in m.items()} for a,m in arms.items()},
                flips=flips,asian_five_states={a:{k:c[k] for k in STATES} for a,c in states.items()})
