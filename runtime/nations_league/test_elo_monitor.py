from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nations_league import elo_monitor as monitor


class EloMonitoringTests(unittest.TestCase):
    def test_window_covers_next_calendar_year(self):
        self.assertEqual(list(monitor.months(date(2027,12,31),date(2028,2,29))), ['202712','202801','202802'])

    @staticmethod
    def event(mid, time, names, *, done):
        return {'id':mid, 'date':time,'season':{'year':2026},'competitions':[{
            'id':mid,'date':time, 'groups':{'name':'League A - Group 1'}, 'neutralSite':False,
            'status':{'type':{'state':'post' if done else 'pre','completed':done}},'competitors':[
                {'team':{'id':tid,'displayName':name},'homeAway':orientation,'score':{'value':1-position}}
                for position,(orientation,tid,name) in enumerate(zip(('home','away'),('1','2'),names))
            ]}]}

    def test_missing_recent_result_defers_then_matching_result_issues_only_once(self):
        start = datetime.now(timezone.utc)
        game = self.event('901',(start+timedelta(hours=20)).isoformat(),['Alpha','Beta'],done=False)
        last = self.event('801',(start-timedelta(days=8)).isoformat(),['Alpha','Beta'],done=True)
        scoreboard = {'leagues':[{'slug':'uefa.nations'}],'events':[game]}
        head = dict(game,league={'slug':'uefa.nations'})
        def captured(url, value):
            raw = json.dumps(value).encode()
            return raw, {'url':url, 'sha256':hashlib.sha256(raw).hexdigest(),
                         'captured_at':start.isoformat()}
        def fake_source(url):
            if 'summary?' in url:
                return captured(url,{'header':head})
            tid = '1' if '/teams/1/' in url else '2'
            return captured(url,{'team':{'id':tid},'events':[last]})
        world = {'home':'Alpha','away':'Beta','score':[1,0],'neutral':False,'date':last['date'][:10]}
        with TemporaryDirectory() as tmp, patch.object(monitor,'fetch',side_effect=fake_source):
            study=Path(tmp)
            (study/'report.json').write_text(json.dumps({'campaigns':{'2026':{'fits':{'A':{'elo':{
                'coefficients':[.2,.1,.4],'include_elo':True}}}}}}))
            store=study/'forward'
            raw,meta=captured(monitor.ESPN+'uefa.nations/scoreboard?dates=202611',scoreboard)
            missing={'recent_nations':[],'world':([],{})}
            first=monitor.candidate(game,raw,meta,study,store,48,missing)
            self.assertEqual(first['status'],'DEFERRED_INCOMPLETE_NATIONAL_RESULTS')
            self.assertEqual(len(first['coverage']['home']['unmatched']),1)
            self.assertIsNone(monitor.existing(store,'espn:901','nations-elo-independent'))
            present={'recent_nations':[],'world':([world],{})}
            issued=monitor.candidate(game,raw,meta,study,store,48,present)
            again=monitor.candidate(game,raw,meta,study,store,48,present)
            self.assertEqual(issued['status'],'ISSUED')
            self.assertEqual(again['status'],'ALREADY_ISSUED')
            self.assertEqual(issued['freeze_sha256'],again['freeze_sha256'])
            rec=monitor.existing(store,'espn:901','nations-elo-independent')
            self.assertEqual(rec['evaluation_slot'],'FIRST_PREMATCH')
            self.assertEqual(rec['payload']['picks']['total']['line'],2.5)
            self.assertEqual(rec['payload']['fixture_registration'],first['registration_sha256'])


if __name__=='__main__':
    unittest.main()
