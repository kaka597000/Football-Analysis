"""Explicit runtime-root and registry resolution; never search alternative assets."""
import os
from pathlib import Path

def runtime_root():
    return Path(os.environ.get('WCPP_RUNTIME_ROOT', '/workspace/WCPP')).expanduser().absolute()

def registry_path(explicit=None):
    return Path(explicit or os.environ.get('WCPP_ACTIVE_MODEL') or runtime_root() / 'runtime/active-model.json').expanduser().absolute()
