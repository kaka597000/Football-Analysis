import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

try:
    from runtime.nations_league import database as db
except ModuleNotFoundError:
    from nations_league import database as db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'nations.sqlite';db.build(self.path)
    def tearDown(self):self.tmp.cleanup()
    def capture(self,event='10',kind='summary',status='STATUS_FULL_TIME',phase='2024 UEFA Nations League, League A',goals=(2,1),periods=None,neutral=False,query='2026-01-02T00:00:00Z'):
        comp={'id':event,'date':'2025-01-01T12:00:00Z','neutralSite':neutral,'status':{'type':{'name':status,'completed':status in db.TERMINAL,'state':'post' if status in db.TERMINAL else 'pre','detail':'FT' if status=='STATUS_FULL_TIME' else 'AET'}},'competitors':[]}
        for i,side in enumerate(('home','away')):
            lines=(periods[i] if periods else [1,goals[i]-1])
            comp['competitors'].append({'homeAway':side,'team':{'id':str(i+1),'displayName':side.title()},'score':str(goals[i]),'linescores':[{'period':j,'displayValue':str(n)} for j,n in enumerate(lines,1)]})
        head={'id':event,'date':comp['date'],'league':{'slug':'uefa.nations'},'season':{'year':2024,'name':phase},'competitions':[comp]}
        raw={'header':head} if kind=='summary' else {'leagues':[{'slug':'uefa.nations'}],'events':[head]}
        path=self.root/(event+'-'+kind+'-'+hashlib.sha256(json.dumps(raw).encode()).hexdigest()[:8]+'.json');path.write_text(json.dumps(raw))
        row={'competition_id':'uefa.nations','event_id':event,'source_kind':kind,'raw_path':str(path),'raw_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'query_time':query,'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/'+kind+('?event='+event if kind=='summary' else '?dates=2025'),
             'kickoff_at':comp['date'],'home_id':'1','away_id':'2','training_eligible':True}
        return row,raw
    def insert(self,rows):return db.import_captures(self.path,rows,self.root)
    def event(self):
        c=db.connect(self.path,True)
        try:return dict(c.execute('SELECT * FROM events').fetchone())
        finally:c.close()
    def test_verified_summary_idempotent_and_read_only_stats(self):
        row,_=self.capture();first=self.insert([row]);before=self.path.read_bytes();second=self.insert([row]);self.assertTrue(second['idempotent']);self.assertEqual(first['counts']['new_events'],1)
        s=db.stats(self.path);self.assertEqual(s['eligible'],1);self.assertEqual(s['integrity_check'],'ok');self.assertEqual(before,self.path.read_bytes());self.assertEqual((self.event()['home_90'],self.event()['away_90']),(2,1))
    def test_scoreboard_inventory_then_summary_preserves_observations(self):
        board,_=self.capture(kind='scoreboard');summary,_=self.capture(query='2026-01-03T00:00:00Z')
        self.insert([board]);self.assertEqual(self.event()['training_eligible'],0)
        self.insert([summary]);self.assertEqual(self.event()['training_eligible'],1);self.assertEqual(db.stats(self.path)['counts']['observations'],2)
    def test_missing_raw_is_inventory_and_later_raw_can_be_imported(self):
        row,raw=self.capture();path=Path(row['raw_path']);body=path.read_bytes();path.unlink()
        self.insert([row]);self.assertEqual(self.event()['training_eligible'],0);self.assertIsNone(self.event()['home_90'])
        path.write_bytes(body);self.assertFalse(self.insert([row])['idempotent']);self.assertEqual(self.event()['training_eligible'],1)
    def test_raw_hash_mismatch_and_wrong_event_quarantined(self):
        row,_=self.capture();row['raw_sha256']='f'*64;self.insert([row]);self.assertEqual(db.stats(self.path)['counts']['quarantine'],1);self.assertEqual(db.stats(self.path)['counts']['events'],0)
        row,_=self.capture();row['event_id']='11';self.insert([row]);self.assertEqual(db.stats(self.path)['counts']['quarantine'],2)
    def test_halves_final_conflict_cannot_train(self):
        row,_=self.capture(periods=([0,0],[0,0]));self.insert([row]);self.assertEqual(db.stats(self.path)['counts']['quarantine'],1);self.assertEqual(db.stats(self.path)['eligible'],0)
    def test_finished_score_correction_quarantined_not_overwritten(self):
        row,_=self.capture();self.insert([row]);before=self.event()
        changed,_=self.capture(goals=(3,1),query='2026-01-03T00:00:00Z');self.insert([changed]);self.assertEqual(self.event(),before);self.assertEqual(db.stats(self.path)['counts']['quarantine'],1)
    def test_et_and_shootout_are_separate_from_90(self):
        row,raw=self.capture(status='STATUS_FINAL_PEN',phase='2024 UEFA Nations League, League A, Final',goals=(3,3),periods=([1,1,1,0],[1,1,1,0]))
        for c,score in zip(raw['header']['competitions'][0]['competitors'],(5,4)):c['shootoutScore']=str(score)
        p=Path(row['raw_path']);p.write_text(json.dumps(raw));row['raw_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.insert([row]);r=self.event()
        self.assertEqual([r[k] for k in ('home_total','home_90','home_et','home_penalties')],[3,2,1,5]);self.assertEqual(r['training_eligible'],1)
    def test_unknown_neutral_and_grade_remain_null_not_training(self):
        row,_=self.capture(neutral=None,phase='2024 UEFA Nations League, Quarterfinals');self.insert([row]);r=self.event()
        self.assertEqual(r['stage'],'quarterfinals');self.assertIsNone(r['league_level']);self.assertIsNone(r['neutral']);self.assertEqual(r['training_eligible'],0)
    def test_future_inventory_has_no_outcome_even_with_fake_eligible(self):
        row,raw=self.capture(status='STATUS_SCHEDULED');self.insert([row]);r=self.event();self.assertIsNone(r['home_90']);self.assertIsNone(r['home_total']);self.assertEqual(r['training_eligible'],0)
    def test_training_export_one_phase_provenance_compatible(self):
        row,_=self.capture();self.insert([row]);out=self.root/'export.json';db.export(self.path,out,'league-phase','A');x=json.loads(out.read_text());self.assertEqual(len(x['rows']),1)
        try:
            from runtime.research_program.nations_phase import classify
        except ModuleNotFoundError:
            from research_program.nations_phase import classify
        self.assertTrue(classify(x['rows'][0])['complete']);self.assertFalse(x['historical_asof_eligible'])
        with self.assertRaises(FileExistsError):db.export(self.path,out,'league-phase','A')
    def test_append_only_observations_and_sources(self):
        row,_=self.capture();self.insert([row]);c=db.connect(self.path)
        for sql in ('DELETE FROM observations','UPDATE observations SET projected=0','DELETE FROM source_records','UPDATE source_records SET kind=\'inventory\''):
            with self.assertRaises(sqlite3.IntegrityError):c.execute(sql)
        c.close()
    def test_new_capture_same_bytes_appends_and_old_capture_cannot_downgrade(self):
        row,_=self.capture();self.insert([row]);later=dict(row,query_time='2026-01-04T00:00:00Z');self.insert([later]);before=self.event();earlier=dict(row,query_time='2026-01-03T00:00:00Z');self.insert([earlier]);self.assertEqual(self.event(),before);self.assertEqual(db.stats(self.path)['counts']['observations'],3)
    def test_cross_competition_and_team_claim_rejected(self):
        row,_=self.capture();row['competition_id']='uefa.champions';self.insert([row]);row,_=self.capture();row['home_id']='44';self.insert([row]);self.assertEqual(db.stats(self.path)['counts']['events'],0)
    def test_build_never_overwrites_existing_or_canonical(self):
        with self.assertRaises(FileExistsError):db.build(self.path)
        with self.assertRaises(ValueError):db.build(db.CANONICAL)
    def test_phase_conflict_does_not_replace_original(self):
        row,_=self.capture();self.insert([row]);before=self.event();changed,_=self.capture(phase='2024 UEFA Nations League, League A, Semifinals',query='2026-01-03T00:00:00Z');self.insert([changed]);self.assertEqual(self.event(),before);self.assertEqual(db.stats(self.path)['counts']['quarantine'],1)

    def test_explicit_ft_two_period_sum_allows_unlabelled_periods(self):
        row,raw=self.capture()
        for c in raw['header']['competitions'][0]['competitors']:
            for period in c['linescores']:period.pop('period')
        p=Path(row['raw_path']);p.write_text(json.dumps(raw));row['raw_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.insert([row]);r=self.event()
        self.assertEqual(r['home_90'],2);self.assertEqual(r['summary_verified'],1);self.assertEqual(r['training_eligible'],1);self.assertIn('explicit_full_time_status_and_two_period_sum',r['score_basis_evidence'])
    def test_extra_time_without_four_explicit_periods_is_unresolved(self):
        row,_=self.capture(status='STATUS_FINAL_AET',phase='2024 UEFA Nations League, League A, Final');self.insert([row]);self.assertEqual(self.event()['summary_verified'],0);self.assertIsNone(self.event()['home_90'])
    def test_endpoint_port_and_mixed_league_board_rejected(self):
        row,_=self.capture();row['source_url']=row['source_url'].replace('site.api.espn.com/','site.api.espn.com:444/');self.insert([row])
        row,raw=self.capture(kind='scoreboard');raw['leagues'].append({'slug':'uefa.nations.women'});p=Path(row['raw_path']);p.write_text(json.dumps(raw));row['raw_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.insert([row])
        self.assertEqual(db.stats(self.path)['counts']['events'],0);self.assertEqual(db.stats(self.path)['counts']['quarantine'],2)
    def test_unexpected_failure_rolls_back_entire_batch(self):
        from unittest.mock import patch
        first,_=self.capture(event='10');second,_=self.capture(event='11');original=db.normalize_capture
        def fault(row,raw_root,raw_cache=None):
            if row['event_id']=='11':raise RuntimeError('synthetic interrupted transaction')
            return original(row,raw_root,raw_cache)
        with patch.object(db,'normalize_capture',side_effect=fault):
            with self.assertRaises(RuntimeError):self.insert([first,second])
        counts=db.stats(self.path)['counts'];self.assertEqual(counts['events'],0);self.assertEqual(counts['observations'],0);self.assertEqual(counts['import_runs'],0)

    def test_penalty_status_with_unequal_pre_shootout_totals_is_quarantined(self):
        row,_=self.capture(status='STATUS_FINAL_PEN',goals=(2,1),periods=([1,1,0,0],[1,0,0,0]));self.insert([row]);self.assertEqual(db.stats(self.path)['counts']['quarantine'],1);self.assertEqual(db.stats(self.path)['counts']['events'],0)

    def test_ft_with_status_period_four_is_quarantined(self):
        row,raw=self.capture();raw['header']['competitions'][0]['status']['period']=4
        p=Path(row['raw_path']);p.write_text(json.dumps(raw));row['raw_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.insert([row]);self.assertEqual(db.stats(self.path)['counts']['quarantine'],1);self.assertEqual(db.stats(self.path)['eligible'],0)
    def test_extra_time_in_league_phase_is_contradictory(self):
        row,_=self.capture(status='STATUS_FINAL_AET',goals=(3,1),periods=([1,1,1,0],[1,0,0,0]));self.insert([row]);self.assertEqual(db.stats(self.path)['counts']['quarantine'],1)

    def test_verified_scoreboard_supplements_missing_summary_grade(self):
        board,_=self.capture(kind='scoreboard',phase='2024 UEFA Nations League, League D',query='2026-01-04T00:00:00Z')
        summary,_=self.capture(phase='2024 UEFA Nations League, Group Stage',query='2026-01-03T00:00:00Z')
        self.insert([board]);self.insert([summary]);r=self.event()
        self.assertEqual(r['league_level'],'D');self.assertEqual(r['training_eligible'],1);self.assertIsNotNone(r['supplemental_phase_evidence_json']);self.assertEqual(db.aware(r['observed_at']),db.aware('2026-01-04T00:00:00Z'))
        out=self.root/'supplemented-export.json';db.export(self.path,out,'league-phase','D')
        try:from runtime.research_program.nations_phase import classify
        except ModuleNotFoundError:from research_program.nations_phase import classify
        self.assertTrue(classify(json.loads(out.read_text())['rows'][0])['complete'])
    def test_later_supplement_reimport_upgrades_and_tamper_cannot_hide_behind_idempotency(self):
        summary,_=self.capture(phase='2024 UEFA Nations League, Group Stage',query='2026-01-03T00:00:00Z')
        board,_=self.capture(kind='scoreboard',phase='2024 UEFA Nations League, League D',query='2026-01-04T00:00:00Z')
        self.insert([summary]);self.assertEqual(self.event()['training_eligible'],0)
        self.insert([board]);result=self.insert([summary]);self.assertFalse(result['idempotent']);self.assertEqual(self.event()['training_eligible'],1)
        self.assertTrue(self.insert([summary])['idempotent']);Path(board['raw_path']).write_text('{}')
        changed=self.insert([summary]);self.assertFalse(changed['idempotent']);self.assertEqual(changed['counts']['quarantined'],1)
        with self.assertRaises(ValueError):db.export(self.path,self.root/'bad-export.json','league-phase','D')
    def test_mixed_board_summary_batch_is_idempotent(self):
        board,_=self.capture(kind='scoreboard',phase='2024 UEFA Nations League, League D');summary,_=self.capture(phase='2024 UEFA Nations League, Group Stage')
        self.insert([board,summary]);self.assertEqual(self.event()['training_eligible'],1);self.assertTrue(self.insert([board,summary])['idempotent'])


if __name__=='__main__':unittest.main()
