from copy import deepcopy
import unittest

from research_program import lineup_position_study as roles
from research_program import lineup_study as study


def side():
    positions = [('G', 'Goalkeeper'), ('LB', 'Left Back'), ('CD-L', 'Center Left Defender'),
                 ('CD-R', 'Center Right Defender'), ('RB', 'Right Back'), ('DM', 'Defensive Midfielder'),
                 ('CM', 'Center Midfielder'), ('LM', 'Left Midfielder'), ('AM-R', 'Attacking Midfielder Right'),
                 ('CF-L', 'Center Left Forward'), ('RCF', 'Rear Center Forward')]
    return [dict(player_id=str(i), starter=True, position_group=name, position_abbreviation=code)
            for i, (code, name) in enumerate(positions)]


class ExplicitPositionTests(unittest.TestCase):
    def test_original_named_roles_are_translated_and_raw_rows_are_unchanged(self):
        record = dict(source_sha256='c'*64, home_roster=side(), away_starters=None)
        before = deepcopy(record)
        self.assertEqual(study._starters(record, 'home')[1], 'unknown_or_ambiguous_position')
        mapped, = roles.normalize([record])
        players, status, n = study._starters(mapped, 'home')
        self.assertEqual((status, n), ('complete', 11))
        self.assertEqual({p['id']: p['position'] for p in players}, dict(zip(map(str, range(11)), 'GDDDDMMMMFF')))
        self.assertEqual(study._starters(mapped, 'away')[1], 'missing_side')
        self.assertEqual(record, before)
        self.assertEqual(mapped['source_sha256'], record['source_sha256'])

    def test_conflicting_name_or_unregistered_code_preserves_missing_side(self):
        for updates in (dict(position_group='Right Forward'), dict(position_abbreviation='NEW'),
                        dict(position='F'), dict(player_id='0'), dict(starter=False)):
            players = side()
            players[1].update(updates)
            converted, = roles.normalize([dict(home_roster=players)])
            with self.subTest(updates=updates):
                self.assertIsNone(study._starters(converted, 'home')[0])


if __name__ == '__main__':
    unittest.main()
