"""Read-only provider audit/fetch, emitting sanitized evidence only."""
import argparse
import json
from pathlib import Path
from clients import audit, fetch, match_context


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--provider', choices=('football-data', 'api-football'))
    p.add_argument('--path'); p.add_argument('--params', default='{}')
    p.add_argument('--match-id', type=int); p.add_argument('--out', required=True)
    a = p.parse_args()
    result = (audit() if not a.provider else match_context(a.provider, a.match_id)
              if a.match_id else fetch(a.provider, a.path, json.loads(a.params)))
    with Path(a.out).open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(a.out)


if __name__ == '__main__': main()
