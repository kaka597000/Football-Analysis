"""Read-only provider audit/fetch, emitting sanitized evidence only."""
import argparse
import json
from pathlib import Path
from clients import audit, fetch, match_context, PROVIDERS
from api_first import run


def main():
    p = argparse.ArgumentParser()
    group = p.add_mutually_exclusive_group()
    group.add_argument('--provider', choices=tuple(PROVIDERS))
    group.add_argument('--plan', help='default batch workflow: API-first plan JSON')
    p.add_argument('--cache-dir')
    p.add_argument('--path'); p.add_argument('--params', default='{}')
    p.add_argument('--match-id', type=int); p.add_argument('--out', required=True)
    a = p.parse_args()
    if Path(a.out).exists():
        raise FileExistsError(a.out)
    if a.plan and not a.cache_dir:
        p.error('--plan requires --cache-dir')
    if a.match_id and a.provider not in ('football-data', 'api-football'):
        p.error('--match-id is only supported by football-data and api-football')
    result = (run(json.loads(Path(a.plan).read_text()), a.cache_dir) if a.plan else
              audit() if not a.provider else match_context(a.provider, a.match_id)
              if a.match_id else fetch(a.provider, a.path, json.loads(a.params)))
    with Path(a.out).open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(a.out)


if __name__ == '__main__': main()
