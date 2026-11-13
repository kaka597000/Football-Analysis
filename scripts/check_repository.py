"""Check versioned/pending source without printing credential values or lines."""
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from data_sources.clients import PROVIDERS, QUARANTINED_CREDENTIAL_NAMES

# Reuse the actual client registry so new aliases cannot silently escape the
# exact-value scan. Standalone collectors and notification bindings live here.
BINDINGS = tuple(sorted(set(QUARANTINED_CREDENTIAL_NAMES) | {
    name for provider in PROVIDERS.values()
    for name in (provider.get('env'), *provider.get('aliases', [])) if name
} | {'WCPP_ODDS_API_KEY', 'WCPP_INFERSPORTS_KEY', 'infer_yapan_zhishu',
     'yapan_infersports', 'WCPP_SMTP_PASSWORD'}))
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
