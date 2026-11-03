"""Train and execute the independent weighted research model."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from competition_scope import require, training_rows
from weighted_model import train, predict


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    t = sub.add_parser('train'); t.add_argument('history'); t.add_argument('--directory', required=True)
    t = sub.add_parser('predict'); t.add_argument('artifact'); t.add_argument('fixture'); t.add_argument('--out', required=True)
    a = p.parse_args()
    if a.command == 'train':
        artifact, report = train(training_rows(json.loads(Path(a.history).read_text(encoding="utf-8"))))
        directory = Path(a.directory); directory.mkdir(parents=True, exist_ok=True)
        for name, obj in [('artifact', artifact), ('report', report)]:
            with (directory/(name+'.json')).open('x') as f: json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({'cohort': artifact['cohort'], 'weights': report['weights'],
                          'acceptance': report['acceptance']}, ensure_ascii=False))
    else:
        obj = predict(json.loads(Path(a.artifact).read_text()), require(json.loads(Path(a.fixture).read_text(encoding="utf-8"))))
        with Path(a.out).open('x') as f: json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        print(a.out)


if __name__ == '__main__': main()
