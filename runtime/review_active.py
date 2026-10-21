"""Review both named models without selecting the better outcome after the fact."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'v5'))
from paired_review import review


def main():
    p = argparse.ArgumentParser()
    p.add_argument('pairs'); p.add_argument('results'); p.add_argument('--out', required=True)
    a = p.parse_args()
    pairs = json.loads(Path(a.pairs).read_text())
    if isinstance(pairs, dict): pairs = [pairs]
    output = review(pairs, json.loads(Path(a.results).read_text()))
    with Path(a.out).open('x') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(a.out)


if __name__ == '__main__': main()
