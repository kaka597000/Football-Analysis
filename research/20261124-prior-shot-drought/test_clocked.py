"""Exercise the shared history, previously frozen identity, and separated label route."""

from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import clocked


def fixture():
    return dict(match_id='espn:synthetic', cohort='eng.1', stage='regular', score_basis='regulation_90',
                home='espn:1', away='espn:2', kickoff_at='2026-11-21T16:00:00Z',
                cutoff_at='2026-11-20T10:00:00Z', freeze_reference={'sha256':'3'*64,'freeze_id':'f-v000001'})


def season():
    teams = [f'C{i}' for i in range(12)]
    rows = []
    for round_no in range(11):
        day = date(2026, 9, 28) + timedelta(days=round_no*4)
        for i in range(6):
            rows.append(dict(day=day, home=teams[i], away=teams[-1-i],
                             raw=dict(FTHG='1',FTAG='1',FTR='D',HST='3',AST='2')))
        teams[1:] = [teams[-1], *teams[1:-1]]
    return rows


class TimedInputTest(unittest.TestCase):
    def inputs(self, row, games, when):
        archives = SimpleNamespace(r7_ids=set(), r7_keys=set(),
                                   original=lambda r: dict(r, home_name='C0', away_name='C1'),
                                   quote_line=lambda f: None, season=lambda league, year: (games, when))
        audit = SimpleNamespace(identity=lambda r:(r['home'],r['away']), CODES={'eng.1':'E0'},
                                resolve_club=lambda names,clubs:next(iter(names & clubs),None))
        return clocked.clocked_inputs(archives,[row],defaultdict(set),audit)[0]

    def test_market_absence_keeps_shared_strength_and_ordered_shot_histories(self):
        r = self.inputs(fixture(),season(),clocked.aware('2026-11-18T20:30:35Z'))
        self.assertEqual(r['status'],'eligible')
        self.assertIsNone(r['line'])
        self.assertEqual(len(r['rates']),2)
        self.assertTrue(all(len(s)==6 for side in r['for_against'] for s in side))
        # Team C0 was always at home. Its own shot count and concessions occupy opposite sides.
        self.assertEqual(r['for_against'][0][0],[3]*6)
        self.assertEqual(r['for_against'][1][1],[2]*6)

    def test_missing_latest_sot_falls_back_without_replacing_the_game(self):
        games = season()
        next(r for r in reversed(games) if r['home']=='C0')['raw']['HST']=''
        r = self.inputs(fixture(),games,clocked.aware('2026-11-18T20:30:35Z'))
        self.assertEqual(r['status'],'anchor_only')
        self.assertIsNotNone(r['rates'])
        self.assertIsNone(r['for_against'])

    def test_outside_phase_and_equal_capture_stay_in_roster(self):
        f = fixture()
        result = self.inputs(f,[], clocked.aware(f['cutoff_at']))
        self.assertEqual(result['status'],'no_history_available_at_cutoff')
        self.assertIsNone(result['rates'])
        r = self.inputs(dict(f,stage='final-a'),[],None)
        self.assertEqual(r['status'],'outside_approved_stage')
        self.assertEqual(r['freeze_id'], f['freeze_reference']['freeze_id'])

    def test_recent_or_target_dates_are_skipped_before_reading_any_values(self):
        cut=date(2026,11,20)
        prior={'day':cut-timedelta(days=2),'home':'A','away':'B',
               'raw':dict(FTHG='0',FTAG='0',FTR='D',HST='0',AST='')}
        rows = [prior,{'day':cut-timedelta(days=1)}, {'day':cut}, {'day':cut+timedelta(days=5)}]
        out=clocked.previous_games(rows,cut)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['sot'],(0,None))


class LabelGateTest(unittest.TestCase):
    def files(self, folder, mismatch=False):
        r = fixture()
        mid = r['match_id']
        inputs = [dict(id=mid, league=r['cohort'], home=r['home'], away=r['away'],
                       kickoff_at=r['kickoff_at'], cutoff_at=r['cutoff_at'],
                       freeze_id=r['freeze_reference']['freeze_id'],
                       freeze_sha='0'*64 if mismatch else r['freeze_reference']['sha256'])]
        ip, fp, lock = [folder/s for s in ('inputs','forecasts','lock')]
        ip.write_bytes(clocked.enc(inputs[0])+b'\n')
        fp.write_bytes(clocked.enc({'id':mid})+b'\n')
        lock.write_text(json.dumps({'panel':'clocked','n':1,'fit_sha256':'d'*64,
                        'protocol_sha256':clocked.digest(clocked.PROTOCOL.read_bytes()),
                        'inputs_sha256':clocked.digest(ip.read_bytes()),'forecast_sha256':clocked.digest(fp.read_bytes()),
                        'roster_sha256':clocked.digest(clocked.enc([mid]))}))
        return (lock,ip,fp,[r])

    def test_altered_forecast_fails_before_label_lookup(self):
        with TemporaryDirectory() as folder:
            args=self.files(Path(folder))
            clocked.verify_prediction_lock(*args)
            args[2].write_bytes(args[2].read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError,'lock'):
                clocked.verify_prediction_lock(*args)

    def test_resigned_wrong_original_freeze_refused(self):
        with TemporaryDirectory() as folder:
            args=self.files(Path(folder),mismatch=True)
            with self.assertRaisesRegex(ValueError,'first issue'):
                clocked.verify_prediction_lock(*args)


if __name__ == '__main__':
    unittest.main()
