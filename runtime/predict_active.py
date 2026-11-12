"""Backward-compatible active prediction and publication CLI."""
import argparse
import json
from pathlib import Path
from prediction_engine import ROOT, predict, legacy, file_sha256, object_sha256, source_identity
from prediction_service import run


def main():
    p = argparse.ArgumentParser()
    p.add_argument('fixture', nargs='?'); p.add_argument('--out', required=True)
    p.add_argument('--registry')
    p.add_argument('--retain-current-on-test-failure', action='store_true',
                   help='Freeze successful Current with explicit failed Test; never substitute predictions')
    p.add_argument('--report', help='New Markdown path for the eight-module evidence report')
    p.add_argument('--freeze-root', default='/workspace/WCPP/forecasts/issued-analyses')
    p.add_argument('--stage', choices=['prematch', 'live'], default='prematch')
    p.add_argument('--resume-publication', action='store_true', help='Resume OUT.publication.json without inference or another freeze')
    a = p.parse_args()
    from publication import publish
    if a.resume_publication:
        publish(None, None, out=a.out, freeze_root=a.freeze_root, resume=True)
        print(a.out)
        return
    if a.stage != 'prematch':
        p.error('live numerical inference is not supported; use a separately validated live engine')
    if not a.fixture:
        p.error('fixture is required unless resuming publication')
    fixture = json.loads(Path(a.fixture).read_text())
    # Reject an existing output before producing any new freeze revision.
    if a.report and (Path(a.report).exists() or Path(a.report).resolve() == Path(a.out).resolve()):
        raise FileExistsError(a.report)
    if Path(a.out).exists():
        raise FileExistsError(a.out)
    output = (run(fixture, a.registry, retain_current_on_test_failure=True)
              if a.retain_current_on_test_failure else run(fixture, a.registry))
    from research.team_goal_analysis import attach_pair
    if any('team_goal_analysis' not in output[k] for k in ('current_model','test_model')):
        attach_pair(output)
    from research.score_distribution_audit import attach_pair as attach_audit
    if any('score_distribution_audit' not in output[k] for k in ('current_model','test_model')):
        attach_audit(output)
    publish(output, fixture, out=a.out, freeze_root=a.freeze_root,
            stage=a.stage, report=a.report)
    print(a.out)


if __name__ == '__main__': main()
