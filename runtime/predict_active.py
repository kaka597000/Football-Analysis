"""Backward-compatible active prediction and publication CLI."""
import argparse
import json
from pathlib import Path
from prediction_engine import ROOT, predict, legacy, file_sha256, object_sha256, source_identity
from prediction_service import run


def main():
    p = argparse.ArgumentParser()
    p.add_argument('fixture'); p.add_argument('--out', required=True)
    p.add_argument('--registry')
    p.add_argument('--report', help='New Markdown path for the eight-module evidence report')
    p.add_argument('--freeze-root', default='/workspace/WCPP/forecasts/issued-analyses')
    p.add_argument('--stage', choices=['prematch', 'live'], default='prematch')
    a = p.parse_args()
    fixture = json.loads(Path(a.fixture).read_text())
    # Reject an existing output before producing any new freeze revision.
    if a.report and (Path(a.report).exists() or Path(a.report).resolve() == Path(a.out).resolve()):
        raise FileExistsError(a.report)
    if Path(a.out).exists():
        raise FileExistsError(a.out)
    output = run(fixture, a.registry)
    from research.team_goal_analysis import attach_pair
    if any('team_goal_analysis' not in output[k] for k in ('current_model','test_model')):
        attach_pair(output)
    from research.score_distribution_audit import attach_pair as attach_audit
    if any('score_distribution_audit' not in output[k] for k in ('current_model','test_model')):
        attach_audit(output)
    from research.analysis_freeze import save
    # One atomic paired snapshot preserves both independent model outputs.
    output['freeze_receipt'] = save(output, fixture, stage=a.stage,
                                   track='current-test-pair', root=a.freeze_root)
    with Path(a.out).open('x') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    if a.report:
        from research.match_analysis import render
        with Path(a.report).open('x') as f:
            f.write(render(output['match_analysis']))
    print(a.out)


if __name__ == '__main__': main()
