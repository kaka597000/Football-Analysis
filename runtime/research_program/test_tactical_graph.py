"""Prevent recipient/endpoint leakage and ambiguous roster membership in tactical replay."""
import copy
import unittest

import numpy as np

from research_program.tactical_graph import samples


def event(index, kind, **data):
    return {'index': index, 'period': 1, 'team': {'id': 111}, 'type': {'name': kind}, **data}


def starting_xi():
    return event(1, 'Starting XI', tactics={'lineup': [
        {'player': {'id': n}, 'position': {'id': n}} for n in range(1, 12)]})


def completed(index, player, recipient, end):
    return event(index, 'Pass', player={'id': player}, location=[60, 40],
                 play_pattern={'name': 'Regular Play'},
                 **{'pass': {'recipient': {'id': recipient}, 'end_location': end}})


class TacticalGraphTests(unittest.TestCase):
    def test_current_receiver_and_endpoint_enter_only_after_the_pass(self):
        events = [starting_xi(), completed(2, 1, 2, [60, 30]), completed(3, 1, 2, [60, 35])]
        changed = copy.deepcopy(events)
        changed[1]['pass'] = {'recipient': {'id': 3}, 'end_location': [70, 50]}
        x, y, _, _ = samples(events)
        other, alternative, _, _ = samples(changed)
        np.testing.assert_array_equal(x[0], other[0])
        self.assertNotEqual(y[0], alternative[0])
        self.assertFalse(np.array_equal(x[1], other[1]))

    def test_team_personnel_change_removes_later_examples_from_coverage(self):
        events = [starting_xi(), completed(2, 2, 1, [60, 30]), event(3, 'Substitution'),
                  completed(4, 2, 1, [60, 35])]
        _, labels, counts, _ = samples(events)
        self.assertEqual(len(labels), 1)
        self.assertEqual(counts['completed_regular_first_half'], 2)
        self.assertEqual(counts['roster_or_recipient_unavailable'], 1)


if __name__ == '__main__':
    unittest.main()
