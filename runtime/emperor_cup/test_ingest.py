import json
import sqlite3
import unittest
from pathlib import Path
from ingest import parse,ingest,SCHEMA

RAW=Path(__file__).resolve().parents[2] / 'tests' / 'fixtures' / 'emperor_cup'
ARCHIVE=RAW

def historical(name,year,number):
    raw=(RAW/name).read_bytes()
    return raw,parse(raw,year,number,f'https://www.jfa.jp/match/emperorscup_{year}/match_page/m{number}.html','2026-10-07T10:30:00+00:00')

class Tests(unittest.TestCase):
    def test_penalties_and_extra_not_regulation(self):
        _,p=historical('2025-m1.html',2025,1)
        self.assertEqual(p['regulation'],[1,1]);self.assertEqual(p['extra'],[1,1]);self.assertEqual(p['penalty'],[4,5]);self.assertEqual(p['display_final'],[2,2])

    def test_historical_normal_score_and_timezone(self):
        _,p=historical('2024-m87.html',2024,87)
        self.assertEqual(p['regulation'],[0,1]);self.assertEqual(p['kickoff_utc8'],'2024-11-23T13:00:00+08:00')
        self.assertIsNone(p['first_known_at']);self.assertFalse(p['physical_home_verified'])

    def test_registered_squad_not_starting_lineup(self):
        raw=(ARCHIVE/'m62.html').read_bytes();p=parse(raw,2026,62,'https://www.jfa.jp/match/emperorscup_2026/match_page/m62.html','2026-10-07T08:40:00+00:00')
        self.assertIsNone(p['regulation']);self.assertEqual(p['status'],'SCHEDULED')
        self.assertTrue(p['roster']);self.assertEqual({r['role'] for r in p['roster']},{'registered'})

    def test_official_starting_eleven_and_bench(self):
        raw=(ARCHIVE/'m67.html').read_bytes();p=parse(raw,2026,67,'https://www.jfa.jp/match/emperorscup_2026/match_page/m67.html','2026-10-07T08:40:00+00:00')
        self.assertEqual(sum(r['role']=='starter' and r['side']=='first' for r in p['roster']),11)
        self.assertTrue(any(r['role']=='bench' for r in p['roster']))

    def test_identity_and_contradictory_score_rejected(self):
        raw,p=historical('2025-m1.html',2025,1)
        with self.assertRaises(ValueError):parse(raw,2025,2,p['source_url'],p['observed_at'])
        bad=raw.replace(b'<div class="total-score">2</div>',b'<div class="total-score">9</div>',1)
        with self.assertRaises(ValueError):parse(bad,2025,1,p['source_url'],p['observed_at'])

    def test_database_idempotency_and_foreign_keys(self):
        raw,p=historical('2025-m1.html',2025,1);c=sqlite3.connect(':memory:');c.executescript(SCHEMA)
        ingest(c,p,RAW/'2025-m1.html',raw);ingest(c,p,RAW/'2025-m1.html',raw)
        self.assertEqual(c.execute('SELECT count(*) FROM matches').fetchone()[0],1)
        self.assertEqual(c.execute('SELECT count(*) FROM result_observations').fetchone()[0],1)
        self.assertEqual(c.execute('SELECT regulation_first,regulation_second FROM training_90').fetchone(),(1,1))
        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        c.close()

    def test_live_partial_period_not_final(self):
        path=next(RAW.glob('2026-m61-*.html'));raw=path.read_bytes()
        p=parse(raw,2026,61,'https://www.jfa.jp/match/emperorscup_2026/match_page/m61.html','2026-10-07T10:34:00+00:00')
        self.assertIsNone(p['regulation']);self.assertEqual(p['status'],'LIVE_OR_AWAITING_RESULT')
        self.assertIsNone(p['periods']['後半'])

    def test_future_unannounced_slot_not_real_fixture(self):
        path=next(RAW.glob('2026-m73-*.html'));raw=path.read_bytes()
        p=parse(raw,2026,73,'https://www.jfa.jp/match/emperorscup_2026/match_page/m73.html','2026-10-07T10:34:00+00:00')
        self.assertTrue(p['slot_only']);self.assertNotIn('regulation',p)
        c=sqlite3.connect(':memory:');c.executescript(SCHEMA);ingest(c,p,path,raw)
        self.assertEqual(c.execute('SELECT count(*) FROM matches').fetchone()[0],0)
        self.assertEqual(c.execute('SELECT count(*) FROM scheduled_slots').fetchone()[0],1);c.close()

if __name__=='__main__':unittest.main()
