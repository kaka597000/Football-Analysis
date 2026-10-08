"""Commands for source-backed context candidates; no implicit training or freezing."""
import argparse
import json
from pathlib import Path
from pipeline import predict, freeze, evaluate, train_context
from context import assess

def read(path):return json.loads(Path(path).read_text())

def main():
    p=argparse.ArgumentParser();cmd=p.add_subparsers(dest='command',required=True)
    a=cmd.add_parser('audit');a.add_argument('fixture');a.add_argument('--out',required=True)
    a=cmd.add_parser('predict');a.add_argument('artifact');a.add_argument('fixture');a.add_argument('--out',required=True)
    a=cmd.add_parser('fit-context');a.add_argument('samples');a.add_argument('--out',required=True)
    a=cmd.add_parser('validate-context');a.add_argument('samples');a.add_argument('--out',required=True)
    a=cmd.add_parser('freeze');a.add_argument('artifact');a.add_argument('draft');a.add_argument('--directory',required=True)
    a=cmd.add_parser('evaluate');a.add_argument('freezes',nargs='+');a.add_argument('--results',required=True);a.add_argument('--eligible-count',type=int);a.add_argument('--out',required=True)
    args=p.parse_args()
    if args.command=='audit':result=assess(read(args.fixture))
    elif args.command=='predict':result=predict(read(args.artifact),read(args.fixture))
    elif args.command in ('fit-context','validate-context'):result=train_context(read(args.samples),args.command=='validate-context')
    elif args.command=='freeze':print(freeze(read(args.draft),read(args.artifact),args.directory));return
    else:result=evaluate([read(f) for f in args.freezes],read(args.results),args.eligible_count)
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True)
    # Predictions and audit snapshots are immutable; never clobber an older cutoff.
    with path.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(str(path))

if __name__=='__main__':main()
