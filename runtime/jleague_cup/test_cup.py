import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from datetime import date,timedelta
from jleague_cup import evidence as e,database as db,model
from competition_scope import training_rows


class CupTests(unittest.TestCase):
    def capture(self,root,score='1:0',heading='Group A - 1. Round',year=2023):
        url=f'https://www.betexplorer.com/football/japan/ybc-levain-cup-{year}/'
        event=url+'home-away/ABC123/'
        body='<h1>YBC Levain Cup</h1><table><tr><th>'+heading+'</th></tr><tr><td><a class="in-match" href="'+event+'">Home - Away</a></td><td>'+score+'</td><td>01.03.'+str(year)+'</td></tr></table>'
        path=Path(root)/'raw.html';path.write_text(body)
        source={'raw_path':str(path),'raw_sha256':e.sha(path),'source_url':url+'results/','query_time':'2026-10-10T00:00:00Z'}
        row={**e.parse_list(body,source['source_url'])[0],'competition':'jpn.league_cup','gender':'men','age':'senior','score_basis':'regulation_90','evidence':source,'neutral':None}
        return source,row

    def test_actual_phase_and_legs_are_separate(self):
        for label,stage,leg,form in [('Group B - 2. Round','group-stage',None,'group-home-away'),('Quarter-finals - First leg','quarterfinals',1,'two-leg'),('Quarter-finals - Second leg','quarterfinals',2,'two-leg'),('1/64-finals','round-of-128',None,'single'),('Final','final',None,'single')]:
            x=e.phase(label+'1X2');self.assertEqual((x['stage'],x['leg'],x['tie_format']),(stage,leg,form))
        self.assertIsNone(e.phase('cup')['stage'])

    def test_senior_cup_source_is_reverified_and_editions_cannot_mix(self):
        with tempfile.TemporaryDirectory() as d:
            _,r=self.capture(d);r.update(match_id='x',cohort='jpn.league_cup')
            training_rows([r]);bad=copy.deepcopy(r);bad['home_name']='Other'
            self.assertFalse(e.classify(bad)['complete'])
            for change in ({'competition':'jpn.1'},{'gender':'women'},{'stage':'final'},{'leg':2},{'home_goals':4}):
                self.assertFalse(e.classify({**r,**change})['complete'])
            Path(r['evidence']['raw_path']).write_text('changed')
            self.assertFalse(e.classify(r)['complete'])

    def test_wrong_cup_and_numeric_extra_time_are_not_accepted_as_90(self):
        with tempfile.TemporaryDirectory() as d:
            _,r=self.capture(d,score='2:1 AfE')
            with self.assertRaises(ValueError):e.verify_row(r)
            bad=copy.deepcopy(r);bad['evidence']['source_url']=bad['evidence']['source_url'].replace('ybc-levain-cup','emperor-cup')
            with self.assertRaises(ValueError):e.verify_row(bad)

    def test_database_is_new_only_unknown_venue_preserved_and_sources_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            source,row=self.capture(d);path=Path(d)/'cup.sqlite'
            stat=db.build({'sources':[source]},path);self.assertEqual(stat['counts']['matches'],1)
            rows=db.rows(path);self.assertIsNone(rows[0]['neutral']);self.assertEqual(rows[0]['home_goals'],1)
            with self.assertRaises(FileExistsError):db.build({'sources':[source]},path)
            c=sqlite3.connect(path)
            with self.assertRaises(sqlite3.IntegrityError):c.execute('UPDATE sources SET query_time=NULL')
            c.close()

    def test_extra_time_periods_recover_90_not_final_total(self):
        url='https://www.betexplorer.com/football/japan/ybc-levain-cup-2024/a-b/id/'
        s='<link rel="canonical" href="'+url+'"><input id="isFinished" value="1"><span id="js-eventstage">After Extra Time</span><span id="js-partial">(1:0, 0:1, 1:0)</span><span id="js-score">2:1</span>'
        r=e.periods(s,url);self.assertEqual((r['home_90'],r['away_90'],r['home_et']),(1,1,1))
        with self.assertRaises(ValueError):e.periods(s.replace('2:1</span>','3:1</span>'),url)

    def test_unknown_venue_has_own_indicator_and_real_models_fit(self):
        rows=[]
        for i in range(30):
            rows.append({'match_id':str(i),'date':(date(2024,1,1)+timedelta(days=i)).isoformat(),
                         'home':'a','away':'b','home_goals':i%3,'away_goals':(i+1)%2,'neutral':None})
        self.assertEqual(model.design([rows[0]],[]).tolist(),[[1.,0.,1.],[1.,0.,1.]])
        original=copy.deepcopy(rows)
        for role in ('current','test'):
            fitted=model.fit(rows,role);m,_=model.distribution(fitted,rows[-1]);self.assertAlmostEqual(float(m.sum()),1.)
        self.assertEqual(rows,original)

    def test_research_loader_rejects_bad_date_numeric_venue_and_wrong_edition(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);dep=root/'dependency.json';dep.write_text('{}')
            scope={'cohort':'jpn.league_cup:edition-2025:round-of-128:single:0','era':'edition-2025',
                   'stage':'round-of-128','tie_format':'single','leg':None}
            artifact={**scope,'schema':'WCPP-JCUP-POISSON-1','version':'SYNTHETIC_TEST',
                      'core':{'teams':[],'coefficients':[0.,0.,0.],'fit_end':'2025-01-01'}}
            for k in ('protocol','dataset','engine'):artifact[k+'_path']=str(dep);artifact[k+'_sha256']=e.sha(dep)
            path=root/'artifact.json';path.write_text(json.dumps(artifact))
            f={**scope,'competition':'jpn.league_cup','gender':'men','age':'senior','date':'2025-02-01','neutral':None,'home':'a','away':'b'}
            self.assertFalse(model.predict(path,f)['formal_kpi_eligible'])
            for change in ({'neutral':0},{'date':'tomorrow'},{'era':'edition-2026'},{'home_goals':1}):
                with self.subTest(change=change),self.assertRaises(ValueError):model.predict(path,{**f,**change})


if __name__=='__main__':unittest.main()
