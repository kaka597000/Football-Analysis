"""Retired deploy-key installer; data repository owns unattended execution."""
import sys


def main():
    print('Retired: use the existing data-repository workflow; see docs/unattended-operation.md. '
          'Historical installer: docs/archive/setup_unattended.py.txt. No changes were made.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
