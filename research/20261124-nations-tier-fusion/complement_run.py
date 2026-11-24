"""Seal the original decision-clock panels before opening their later result archives."""
import argparse
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess

from archive_inputs import HERE
from complement import (FAMILIES, SENSITIVITY, WINDOW_START, clock_native, direction_metrics,
                        fit_selector, main_predictions, margin_bin, oracle, original_roster,
                        scored_rows, select_source)
from labels import certified
from research.analysis_freeze import digest
from study import selection

DIR = 'research/20261124-nations-tier-fusion/'
ID = 'ORIGINAL-FIRST-SCORE-COMPLEMENT-AUDIT-1'
PROTOCOL_COMMIT = 'b4f0523f3ead6971c4345e323f83755ca53ac48f'
PANELS_COMMIT = '3a2803d3136ae025136b2fe3ddd869e32fc9920b'


def original_selector_lock(repo, roster):
    early = {k:v for k,v in roster.items() if v['date'] < WINDOW_START}
    labels, issues = certified(repo,early,training=True)
    if issues:
        raise ValueError('earlier label evidence failed verification')
    fitted = fit_selector(early,labels)
    locked = []
    for mid,row in sorted(roster.items()):
        if row['date'] < WINDOW_START:
            continue
        source = select_source(row,fitted)
        m = row['market'] if source == 'market' else row['base']
        item = dict(id=mid,cutoff=row['cutoff'],original_pair=row['pair_sha'],original_issue=row['issue_sha'],
                    market_sha=row.get('raw_sha') if row['market'] is not None else None,
                    bin=margin_bin(row['base'],row['market']),source=source,choices=selection(m,row['line']),
                    matrix_sha=digest(m.tolist()))
        locked.append(item)
    return dict(study=ID,protocol_commit=PROTOCOL_COMMIT,protocol_sha=sha256((HERE/'complement-protocol.json').read_bytes()).hexdigest(),
                earlier_evidence_sha=digest(labels),trained=fitted,late=locked,late_sha=digest(locked))


def panels_at_origin(repo, roster):
    from native_complement import load_native_panels
    native = load_native_panels(repo,roster)
    main, fitted, earlier = main_predictions(repo,roster,native)
    panels = dict(main=main,**clock_native(native))
    selector_lock = json.loads((HERE/'complement-selector-lock.json').read_bytes())
    if selector_lock['trained'] != fitted or selector_lock['earlier_evidence_sha'] != digest(earlier):
        raise ValueError('early selection changed')
    for r in selector_lock['late']:
        saved = main[r['id']]
        if (r['source'] != saved['selector_source'] or r['choices'] != saved['choices']['selector']
                or saved['matrix_hashes'][r['source']] != r['matrix_sha'] or r['cutoff']!=saved['cutoff']):
            raise ValueError('late decision differs from locked selector')
    entries = {k:[dict(id=mid,sha=digest(row)) for mid,row in sorted(p.items())] for k,p in panels.items()}
    locked = dict(study=ID,selector_lock_sha=digest(selector_lock),rows=entries,fingerprint=digest(entries))
    return panels, locked, earlier


def describe(rows, kind):
    roles = ('team','market') if kind=='repair' else FAMILIES
    result = dict(registered=len(rows), certified=sum(s is not None for _,s in rows),
                  covered_signed_record=sum(bool(r['original_issue_sha']) for r,_ in rows),
                  visible_author_first=sum(r['authored_first'] is not None for r,_ in rows),
                  available_family_union=oracle(rows,roles), same_source_inclusive_union=oracle(rows,roles+SENSITIVITY))
    residual = dict(zero_to_two=0,three=0,four_or_more=0)
    for row,actual in rows:
        picks = [row['choices'][k] for k in roles if k in row['choices']]
        if actual and picks and all(p.get('top3') and actual not in p['top3'] and actual!=p['primary'] for p in picks):
            goals = sum(map(int,actual.split('-')))
            group = 'zero_to_two' if goals<=2 else 'three' if goals==3 else 'four_or_more'
            residual[group] += 1
    result['all_family_firsts_and_matrix_threes_missed_by_actual_total'] = residual
    if kind=='main':
        result['signed_author_primary_without_delivery'] = sum(r['signed_primary_present'] for r,_ in rows)
    tests = {'team_market':('team','market')}
    if kind in ('main','xg'):
        tests.update(team_xg=('team','xg'),market_xg=('market','xg'),three_families=FAMILIES)
    if kind=='main':
        tests.update(original_current_vs_team=('original_current','team'),
                     repair_vs_refreshed_goals=('refreshed_goals','repair'),xg_vs_its_goals=('xg_goals','xg'))
    if kind=='repair':
        tests['goals_vs_challenger'] = ('team','repair')
        tests['all_including_same_source'] = ('team','repair','market')
    result['fixed_common_panels'] = {name:oracle(rows,arms,complete=True) for name,arms in tests.items()}
    return result


def assess(repo, roster, panels, earlier):
    later = {k:v for k,v in roster.items() if v['date'] >= WINDOW_START}
    late_labels, issues = certified(repo,later,training=False)
    labels = {**earlier,**late_labels}
    if set(earlier) & set(late_labels):
        raise ValueError('training and later fixtures overlap')
    report = dict(study=ID, grade_evidence_digest=digest(labels), late_label_issues=issues, panels={})
    cases = []
    for p,items in panels.items():
        rows = list(scored_rows(items,labels))
        section = describe(rows,p)
        if p=='main':
            section['by_window'] = {}
            for key,group in [('earlier',[(r,s) for r,s in rows if r['date']<WINDOW_START]),
                              ('later',[(r,s) for r,s in rows if r['date']>=WINDOW_START])]:
                section['by_window'][key] = describe(group,p)
            section['selector_later'] = direction_metrics([(r,s) for r,s in rows if r['date']>=WINDOW_START])
        section['by_tier'] = {k:oracle([(r,s) for r,s in rows if r['tier']==k],FAMILIES)
                              for k in sorted({r['tier'] for r,s in rows})}
        report['panels'][p] = section
        for row,actual in rows:
            cases.append(ledger_row(p,row,actual,labels.get(row['id'])))
    report['unique_fixtures_in_nations_panels'] = len(set.union(*(set(v) for v in panels.values())))
    report['real_displayed_metrics'] = dict(first=dict(n=0,hits=None),three=dict(n=0,hits=None),
                                            selected_three_batches=dict(n=0,hits=None))
    return report,cases


def ledger_row(panel,row,actual,label):
    choices = row['choices']
    sources = {k:v['primary'] for k,v in choices.items()}
    comparable = [k for k in FAMILIES if k in sources]
    winners = [k for k in comparable if actual == sources[k]] if actual is not None else []
    out = dict(panel=panel,id=row['id'],tier=row['tier'],kickoff=row['kickoff'],cutoff=row['cutoff'],
               registered_source_record=row['original_issue_sha'],author_visible_first=row['authored_first'],
               signed_author_primary_present=row['signed_primary_present'],
               result_certified=label is not None,result_source_sha=label['raw_sha'] if label else None)
    for role in FAMILIES+SENSITIVITY+('selector',):
        out[role+'_first'] = sources.get(role)
        out[role+'_hit'] = (int(sources[role]==actual) if actual is not None and role in sources else None)
    out.update(family_count=len(comparable),distinct_family_firsts=len({sources[k] for k in comparable}),
               family_winners='|'.join(winners) if actual is not None else None,
               family_oracle_hit=int(bool(winners)) if actual is not None and comparable else None,
               actual_inside_any_family_matrix_three=(int(any(actual in choices[k].get('top3',[]) for k in comparable))
                                                       if actual is not None and comparable else None),
               selector_source=row.get('selector_source'), market_gap=row.get('gap'),
               native_price_source_sha=row['hash_by_source'].get('market'),
               xg_source_record=row['hash_by_source'].get('xg'), repair_source_record=row['hash_by_source'].get('repair'))
    return out


def write_json(path, doc):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(doc,stream,indent=2,ensure_ascii=False,allow_nan=False)
        stream.write('\n')


def locked_object(commit, filename):
    return json.loads(subprocess.check_output(['git','-C',str(HERE),'show',commit+':'+DIR+filename]))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=('selector','seal','grade'))
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--seal-commit')
    args=p.parse_args()
    if args.operation=='grade' and args.seal_commit != PANELS_COMMIT:
        p.error('grade requires the published pre-grading seal ' + PANELS_COMMIT)
    roster=original_roster(args.data)
    if args.operation=='selector':
        write_json(args.out,original_selector_lock(args.data,roster))
        return
    panels,lock,earlier=panels_at_origin(args.data,roster)
    if args.operation=='seal':
        write_json(args.out,lock)
        return
    if lock != locked_object(args.seal_commit,'complement-panels-lock.json'):
        raise ValueError('forecast selection or source evidence differs from the pre-grading seal')
    report,ledger=assess(args.data,roster,panels,earlier)
    report['panels_lock_commit']=args.seal_commit
    buf=io.StringIO()
    w=csv.DictWriter(buf,fieldnames=ledger[0],lineterminator='\n')
    w.writeheader()
    w.writerows(ledger)
    args.out.mkdir(exist_ok=False)
    write_json(args.out/'results.json',report)
    (args.out/'fixtures.csv').write_bytes(buf.getvalue().encode('utf-8'))


if __name__=='__main__':
    main()
