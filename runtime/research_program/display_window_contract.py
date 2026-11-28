"""Shared identifiers and date boundaries for two-window J1 reviews."""

import re

from research.analysis_freeze import aware


def series_for_version(version):
    match = re.fullmatch(r'J1-first-display-windows-([0-9]{8})-1', version) if isinstance(version, str) else None
    if not match:
        raise ValueError('a dated J1 first-display protocol is required')
    return 'j1-comprehensive-windows-' + match.group(1)


def consecutive_windows(windows):
    if not isinstance(windows, list) or len(windows) != 2:
        raise ValueError('both registered windows are required')
    result = {}
    previous_end = None
    for window in windows:
        name = window.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_]{0,31}', name) or name in result:
            raise ValueError('window names must be distinct ledger identifiers')
        start, end = aware(window['from']), aware(window['until'])
        if start >= end or (previous_end is not None and start != previous_end):
            raise ValueError('registered windows must be consecutive and nonempty')
        result[name] = dict(window)
        previous_end = end
    return result
