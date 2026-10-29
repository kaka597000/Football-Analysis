"""Check versioned/pending source without printing credential values or lines."""
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BINDINGS = ('WCPP_5DOLLAR_API_KEY', 'WCPP_FOOTBALL_DATA_API_KEY',
            'football_data', 'Football_data_api', 'WCPP_API_FOOTBALL_KEY',
            '_apifootball', 'dashboard_api_football', 'Apifootballcom_APIKEY',
            'apifootball', 'apifootball2', 'WCPP_APIFOOTBALLCOM_API_KEY',
            'manyzhishu_theodds_sports', 'WCPP_THE_ODDS_API_KEY', 'yapan_infersports')
PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'openai_token': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    'github_token': re.compile(r'\b(?:ghp_|github_pat_)[A-Za-z0-9_]{24,}'),
    'literal_credential': re.compile(r'''(?i)(?:api_key|apikey|password|access_token)\s*[=:]\s*["']([a-zA-Z0-9._-]{20,})["']'''),
}


def main():
    names = subprocess.check_output(['git', 'ls-files', '-c', '-o', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    problems = []
    files = sorted(set(n for n in names if n and (ROOT/n).is_file()))
    for name in files:
        path = ROOT/name
        if ((path.name.startswith('.env') and path.name != '.env.example')
                or path.suffix.lower() in ('.sqlite', '.db', '.zip', '.pkl', '.parquet', '.jsonl')
                or path.stat().st_size > 10_000_000):
            problems.append((name, 0, 'runtime_asset_or_secret_file'))
        try:
            lines = path.read_text().splitlines()
        except UnicodeError:
            problems.append((name, 0, 'unexpected_binary'))
            continue
        for number, line in enumerate(lines, 1):
            for binding in BINDINGS:
                value = os.environ.get(binding)
                if value and len(value) >= 8 and value in line:
                    problems.append((name, number, 'configured_credential_value'))
            for kind, pattern in PATTERNS.items():
                for match in pattern.finditer(line):
                    if kind == 'literal_credential' and match.group(1).upper().startswith(('TEST_', 'SYNTHETIC_', 'EXAMPLE_')):
                        continue
                    problems.append((name, number, kind))
    for name, number, kind in sorted(set(problems)):
        print(f'{name}:{number}: {kind}')
    print(f'Checked {len(files)} source files; findings: {len(set(problems))}. Credential values are never displayed.')
    return bool(problems)


if __name__ == '__main__':
    sys.exit(main())
