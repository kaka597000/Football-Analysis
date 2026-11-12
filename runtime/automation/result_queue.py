"""Fair bounded polling: retry pending results without starving unqueried fixtures.

Caller holds the cycle lock. Attempts are persisted before transport, so a crash
or a non-final/invalid response cannot reset priority on the next execution.
"""
import json
from pathlib import Path
from ..publication import atomic_json
from ..research.analysis_freeze import aware


class ResultQueue:
    def __init__(self, state):
        self.path = Path(state) / 'result-attempts.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'sequence': 0, 'attempts': {}}
        self.attempted = []

    def order(self, identities):
        return sorted(identities, key=lambda r: (self.state['attempts'].get(r['match_id'], 0),
                                                 aware(r['kickoff_at']), r['match_id']))

    def mark(self, match_id):
        self.state['sequence'] += 1
        self.state['attempts'][match_id] = self.state['sequence']
        atomic_json(self.path, self.state)
        self.attempted.append(match_id)
