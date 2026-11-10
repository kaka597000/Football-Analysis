import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from research_program.nations_phase import classify, classify_source_phase, verify_period_scores
from research_program.phase_classification import classify as dispatch
from competition_scope import training_rows


def event(slug='league-a',note='UEFA Nations League, LEAGUE A - GROUP 1'):
    return {'id':'1','season':{'year':2026,'slug':slug},'competitions':[{
        'id':'1','date':'2026-10-01T18:45:00Z','altGameNote':note,'neutralSite':False,
        'status':{'period':2,'type':{'name':'STATUS_FULL_TIME','completed':True,'state':'post','detail':'FT'}},
        'competitors':[{'homeAway':side,'team':{'id':tid},'score':str(goal),
            'linescores':[{'period':1,'displayValue':str(goal)},{'period':2,'displayValue':'0'}]}
            for side,tid,goal in [('home','11',1),('away','22',0)]]}]}


class NationsPhaseTests(unittest.TestCase):
    def supplemental(self,root,raw=None):
        path=Path(root)/'scoreboard.json'
        path.write_text(json.dumps({'leagues':[{'slug':'uefa.nations'}],'events':[raw or event('group-stage','GROUP D1')]}))
        return {'source_path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/scoreboard?dates=2026',
            'query_time':'2026-10-02T00:00:00Z'}

    def retained(self,root,raw=None):
        payload={'header':{**(raw or event()),'league':{'slug':'uefa.nations'}}}
        path=Path(root)/'summary.json';path.write_text(json.dumps(payload))
        return {'competition':'uefa.nations','cohort':'uefa.nations','gender':'men','age':'senior',
            'stage':'league-phase','league_level':'A','score_basis':'regulation_90','source_event_id':'1',
            'neutral':False,'home_goals':1,'away_goals':0,
            'home':'espn:11','away':'espn:22','date':'2026-10-01','observed_at':'2026-10-02T00:00:00Z',
            'phase_evidence':{'source_path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/summary?event=1',
                'query_time':'2026-10-02T00:00:00Z'}}

    def test_grades_and_historical_provider_group_alias(self):
        for grade in 'ABCD':
            r=classify_source_phase(event('group-stage',f'UEFA Nations League, GROUP {grade}1'))
            self.assertTrue(r['complete']);self.assertEqual(r['stage'],'league-phase');self.assertEqual(r['league_level'],grade)
            r=classify_source_phase(event('league-'+grade.lower(),f'LEAGUE {grade} - GROUP 1'))
            self.assertTrue(r['complete']);self.assertEqual(r['league_level'],grade)

    def test_knockouts_and_playoffs_never_become_regular(self):
        for slug,stage in [('quarterfinals','quarterfinals'),('semifinals','semifinals'),('final','final'),('3rd-place-match','third-place')]:
            r=classify_source_phase(event(slug,'League A'));self.assertEqual(r['stage'],stage);self.assertTrue(r['complete'])
        r=classify_source_phase(event('relegation-playoffs','UEFA Nations League'))
        self.assertEqual(r['stage'],'promotion-relegation-playoffs');self.assertIsNone(r['league_level']);self.assertFalse(r['complete'])
        r=classify_source_phase(event('relegation-playoffs','League A/B Playoffs'))
        self.assertTrue(r['complete']);self.assertEqual(r['league_level'],'A-B')
        for slug in ('regular','friendly','qualification','unknown'):
            r=classify_source_phase(event(slug,'UEFA Nations League'));self.assertFalse(r['complete']);self.assertIsNone(r['stage'])

    def test_conflicting_source_phase_or_grade_refused(self):
        raw=event('group-stage','League A Quarterfinals')
        self.assertFalse(classify_source_phase(raw)['complete'])
        self.assertFalse(classify_source_phase(event('league-a','League B'))['complete'])
        self.assertFalse(classify_source_phase(event('group-stage','GROUP A'))['complete'])

    def test_retained_bytes_identity_and_stage_verified(self):
        with tempfile.TemporaryDirectory() as root:
            row=self.retained(root);self.assertTrue(dispatch(row)['complete'])
            for key,value in [('league_level','B'),('stage','final'),('gender','women'),('age','U21'),('competition','fifa.friendly'),('source_event_id','2'),('home','espn:22'),('date','2026-10-02')]:
                bad={**row,key:value}
                with self.subTest(key=key):self.assertFalse(classify(bad)['complete'])
            bad=copy.deepcopy(row);bad['phase_evidence']['source_sha256']='a'*64
            self.assertFalse(classify(bad)['complete'])
            bad=copy.deepcopy(row);bad['phase_evidence']['source_url']=bad['phase_evidence']['source_url'].replace('uefa.nations','uefa.euroq')
            self.assertFalse(classify(bad)['complete'])
            bad={**row,'cutoff_at':'2026-10-01T18:00:00Z'}
            self.assertFalse(classify(bad)['complete'])
            bad={**row,'phase_evidence':{'stage':'league-phase','league_level':'A'}}
            self.assertFalse(classify(bad)['complete'])

    def test_grade_and_stage_routes_cannot_pool(self):
        with tempfile.TemporaryDirectory() as root:
            first=Path(root)/'first';first.mkdir();second=Path(root)/'second';second.mkdir();third=Path(root)/'third';third.mkdir()
            a=self.retained(first);b=self.retained(second,event('league-b','League B'));b['league_level']='B'
            final=self.retained(third,event('final','League A'));final['stage']='final'
            for other in (b,final):
                self.assertTrue(classify(other)['complete'])
                self.assertNotEqual(classify(a)['route_key'],classify(other)['route_key'])
                with self.assertRaisesRegex(ValueError,'one competition'):training_rows([a,other])
            alias={**a,'stage':'group-stage'}
            self.assertEqual(classify(a)['route_key'],classify(alias)['route_key']);training_rows([a,alias])

    def test_scoreboard_is_inventory_only_not_training_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            row=self.retained(root);path=Path(row['phase_evidence']['source_path'])
            payload={'leagues':[{'slug':'uefa.nations'}],'events':[event()]}
            row['phase_evidence']['source_url']='https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/scoreboard?dates=2026'
            def write():
                path.write_text(json.dumps(payload));row['phase_evidence']['source_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            write();self.assertTrue(classify_source_phase(payload['events'][0])['complete'])
            self.assertFalse(classify(row)['complete'])
            with self.assertRaisesRegex(ValueError,'unverified competition phase'):training_rows([row])
            payload['events'].append(event());write();self.assertFalse(classify(row)['complete'])
            payload['events'].pop();payload['leagues'][0]['slug']='uefa.euroq';write();self.assertFalse(classify(row)['complete'])

    def test_training_rejects_pending_fabricated_goals_and_venue(self):
        with tempfile.TemporaryDirectory() as root:
            row=self.retained(root)
            for changes in ({'home_goals':0},{'away_goals':3},{'home_goals':True},{'neutral':True},{'neutral':None}):
                with self.subTest(changes=changes):self.assertFalse(classify({**row,**changes})['complete'])
            raw=event();raw['competitions'][0]['status']['type'].update(name='STATUS_SCHEDULED',completed=False,state='pre')
            row=self.retained(root,raw);row.update(home_goals=0,away_goals=0)
            self.assertFalse(classify(row)['complete'])
            with self.assertRaises(ValueError):training_rows([row])

    def test_ft_without_period_ids_proves_only_combined_regulation_score(self):
        with tempfile.TemporaryDirectory() as root:
            raw=event()
            for side in raw['competitions'][0]['competitors']:
                for period in side['linescores']:del period['period']
            row=self.retained(root,raw);self.assertTrue(classify(row)['complete'])
            comp=raw['competitions'][0]
            proof=verify_period_scores(comp['competitors'][0],comp['status'])
            self.assertEqual(proof['score_basis_evidence'],'explicit_full_time_status_and_two_period_sum')
            comp['competitors'][0]['shootoutScore']='4';row=self.retained(root,raw)
            self.assertFalse(classify(row)['complete'])

    def test_et_requires_four_explicit_periods_and_separate_regulation(self):
        with tempfile.TemporaryDirectory() as root:
            raw=event('final','League A');comp=raw['competitions'][0]
            comp['status']={'period':4,'type':{'name':'STATUS_FINAL_AET','completed':True,'state':'post','detail':'AET'}}
            row=self.retained(root,raw);row['stage']='final'
            self.assertFalse(classify(row)['complete'])
            for side in comp['competitors']:
                side['linescores'] += [{'period':3,'displayValue':'1' if side['homeAway']=='home' else '0'}, {'period':4,'displayValue':'0'}]
                if side['homeAway']=='home':side['score']='2'
            row=self.retained(root,raw);row['stage']='final';self.assertTrue(classify(row)['complete'])
            row['home_goals']=2;self.assertFalse(classify(row)['complete'])
            for side in comp['competitors']:
                for p in side['linescores']:del p['period']
            row=self.retained(root,raw);row['stage']='final';self.assertFalse(classify(row)['complete'])

    def test_period_identity_and_values_cannot_contradict_source_total(self):
        with tempfile.TemporaryDirectory() as root:
            for mutate in ('duplicate','total','values','state'):
                raw=event();comp=raw['competitions'][0];side=comp['competitors'][0]
                if mutate=='duplicate':side['linescores'][1]['period']=1
                elif mutate=='total':side['score']='4'
                elif mutate=='values':side['linescores'][0]['value']=2
                else:comp['status']['type']['state']='in'
                with self.subTest(mutate=mutate):self.assertFalse(classify(self.retained(root,raw))['complete'])

    def test_penalty_period_separate_and_match_totals_must_be_level(self):
        with tempfile.TemporaryDirectory() as root:
            raw=event('final','League A');comp=raw['competitions'][0]
            comp['status']={'period':5,'type':{'name':'STATUS_FINAL_PEN','completed':True,'state':'post','detail':'Penalties'}}
            for side,shootout in zip(comp['competitors'],[5,4]):
                side['score']='1';side['shootoutScore']=str(shootout)
                side['linescores']=[{'period':i+1,'displayValue':str(v)} for i,v in enumerate([1,0,0,0,shootout])]
            row=self.retained(root,raw);row.update(stage='final',home_goals=1,away_goals=1)
            self.assertTrue(classify(row)['complete'])
            comp['competitors'][0]['linescores'][4]['displayValue']='4'
            changed=self.retained(root,raw);changed.update(stage='final',home_goals=1,away_goals=1)
            self.assertFalse(classify(changed)['complete'])
            comp['competitors'][0]['linescores'][4]['displayValue']='5'
            comp['competitors'][1]['score']='0';comp['competitors'][1]['linescores'][0]['displayValue']='0'
            changed=self.retained(root,raw);changed.update(stage='final',home_goals=1,away_goals=0)
            self.assertFalse(classify(changed)['complete'])

    def test_summary_scores_with_same_event_scoreboard_grade(self):
        with tempfile.TemporaryDirectory() as root:
            row=self.retained(root,event('group-stage','UEFA Nations League'));row['league_level']='D'
            self.assertFalse(classify(row)['complete'])
            row['supplemental_phase_evidence']=self.supplemental(root)
            result=classify(row);self.assertTrue(result['complete']);training_rows([row])
            self.assertEqual(result['classification']['league_level'],'D')
            self.assertEqual(result['provenance']['supplemental_phase_evidence'],row['supplemental_phase_evidence'])
            row['home_goals']=5;self.assertFalse(classify(row)['complete'])
            row['home_goals']=1;row['phase_evidence']=row['supplemental_phase_evidence']
            self.assertFalse(classify(row)['complete'])

    def test_supplemental_identity_time_hash_and_phase_conflicts_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            for mutation in ('event','team','date','hour','hash','stage','level','query','cutoff','duplicate','raw_conflict','league'):
                row=self.retained(root,event('group-stage','UEFA Nations League'));row['league_level']='D'
                raw=event('group-stage','GROUP D1')
                if mutation=='event':raw['id']='2';raw['competitions'][0]['id']='2'
                elif mutation=='team':raw['competitions'][0]['competitors'][0]['team']['id']='33'
                elif mutation=='date':raw['competitions'][0]['date']='2026-10-02T18:45:00Z'
                elif mutation=='hour':raw['competitions'][0]['date']='2026-10-01T19:45:00Z'
                elif mutation=='stage':raw['season']['slug']='final';raw['competitions'][0]['altGameNote']='League D'
                elif mutation=='level':
                    row=self.retained(root);raw=event('group-stage','GROUP D1')
                elif mutation=='raw_conflict':raw=event('league-a','GROUP D1')
                evidence=self.supplemental(root,raw)
                if mutation=='hash':evidence['source_sha256']='a'*64
                elif mutation=='query':evidence['query_time']='2026-10-03T00:00:00Z'
                elif mutation=='cutoff':row['cutoff_at']='2026-10-01T23:59:00Z'
                elif mutation in ('duplicate','league'):
                    path=Path(evidence['source_path']);payload=json.loads(path.read_text())
                    if mutation=='duplicate':payload['events'].append(raw)
                    else:payload['leagues'][0]['slug']='uefa.euroq'
                    path.write_text(json.dumps(payload));evidence['source_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                row['supplemental_phase_evidence']=evidence
                with self.subTest(mutation=mutation):self.assertFalse(classify(row)['complete'])


if __name__=='__main__':unittest.main()
