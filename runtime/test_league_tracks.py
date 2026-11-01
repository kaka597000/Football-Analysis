"""League-map guards. Synthetic codes only; no fit and no promotion."""
import unittest

from league_tracks import (
    EUROPE_IN_DOMAIN, OOD_ASIA, OOD_MLS, OOD_RESEARCH_TRACKS,
    reject_ood_champion_promotion, reject_ood_in_europe_training,
)


class LeagueTrackTests(unittest.TestCase):
    def test_big_five_and_four_ood_tracks_stay_disjoint(self):
        self.assertEqual(EUROPE_IN_DOMAIN, ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1'))
        self.assertEqual(OOD_ASIA, ('aus.1', 'jpn.1', 'kor.1'))
        self.assertEqual(OOD_MLS, ('usa.1',))
        self.assertEqual(set(OOD_RESEARCH_TRACKS), {'aus.1', 'jpn.1', 'kor.1', 'usa.1'})
        self.assertTrue(set(EUROPE_IN_DOMAIN).isdisjoint(OOD_RESEARCH_TRACKS))

    def test_ood_codes_cannot_enter_europe_training(self):
        reject_ood_in_europe_training(EUROPE_IN_DOMAIN)
        for code in OOD_RESEARCH_TRACKS:
            with self.assertRaises(RuntimeError):
                reject_ood_in_europe_training(['eng.1', code])
            with self.assertRaises(RuntimeError):
                reject_ood_in_europe_training([code + ':regular'])

    def test_ood_codes_cannot_be_promoted(self):
        reject_ood_champion_promotion('eng.1', False)
        for code in OOD_RESEARCH_TRACKS:
            reject_ood_champion_promotion(code, False)
            with self.assertRaises(RuntimeError):
                reject_ood_champion_promotion(code, True)
            with self.assertRaises(RuntimeError):
                reject_ood_champion_promotion(code + ':regular', None)
