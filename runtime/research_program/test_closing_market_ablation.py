import unittest
import numpy as np
from research_program import closing_market_ablation as c

class ClosingMarketTests(unittest.TestCase):
    def row(self,i=0):
        return dict(id=str(i),x=[1+i%3,.8,1.2,1.1],hg=i%4,ag=(i+1)%3,market=[.5,.3,.2,.6])
    def test_gradient(self):
        x=np.array([[.2,-.3],[1,.4],[-.1,.6]]); y=np.array([0,2,1]); t=np.array([.1,.3,-.2]); _,g=c.objective(t,x,y)
        eps=1e-6
        num=np.array([(c.objective(t+np.eye(3)[i]*eps,x,y)[0]-c.objective(t-np.eye(3)[i]*eps,x,y)[0])/(2*eps) for i in range(3)])
        np.testing.assert_allclose(g,num,atol=1e-8)
    def test_no_open_fallback(self):
        self.assertIsNone(c.closing(dict(PSH=2,PSD=3,PSA=4)))
        self.assertIsNone(c.closing(dict(PSCH=2,PSCD=3,PSCA=4,**{'PC>2.5':1,'PC<2.5':2})))
    def test_role_exchange_and_replay(self):
        rows=[self.row(i) for i in range(220)]; m=c.fit(rows,True); r=self.row(8)
        switched=dict(r,x=[r['x'][2],r['x'][3],r['x'][0],r['x'][1]],market=[r['market'][2],r['market'][1],r['market'][0],r['market'][3]])
        np.testing.assert_allclose(c.predict(m,r),c.predict(m,switched)[::-1],atol=1e-10)
        from scipy.stats import poisson
        lam=c.predict(m,r); rev=c.predict(m,switched); goals=np.arange(30)
        np.testing.assert_allclose(np.outer(poisson.pmf(goals,lam[0]),poisson.pmf(goals,lam[1])),np.outer(poisson.pmf(goals,rev[0]),poisson.pmf(goals,rev[1])).T,atol=1e-12)
        swapped=[dict(q,x=[q['x'][2],q['x'][3],q['x'][0],q['x'][1]],hg=q['ag'],ag=q['hg'],market=[q['market'][2],q['market'][1],q['market'][0],q['market'][3]]) for q in rows]
        n=c.fit(swapped,True); np.testing.assert_allclose(c.predict(m,r),c.predict(n,r),rtol=1e-7)
        import json
        np.testing.assert_array_equal(c.predict(m,r),c.predict(json.loads(json.dumps(m)),r))
        original=m['mean'][:]; c.predict(m,dict(r,x=[100,200,300,400])); self.assertEqual(m['mean'],original)
    def test_same_day_block(self):
        rows=[]
        for day in range(1,5):
            for j in range(2): rows.append(dict(league='E0',date=f'2024-01-{day:02}',home='A',away='B',hg=day,ag=0,id=f'{day}-{j}'))
        fs=c.features(rows); same=[r for r in fs if r['date']=='2024-01-03']; self.assertEqual(len(same),2); self.assertEqual(same[0]['x'],same[1]['x'])
        self.assertEqual(same[0]['x'][0],1.5)
    def test_dates_and_tie(self):
        self.assertEqual(c.parse_date('01/08/24'),'2024-08-01'); self.assertEqual(c.parse_date('01/08/2024'),'2024-08-01'); self.assertEqual(c.lower_mode(2),1)


class IsolationTests(unittest.TestCase):
    def test_holdout_month_expands(self):
        rows=[dict(date='2023-09-01',season='2324'),dict(date='2024-08-10',season='2425'),dict(date='2024-09-10',season='2425')]
        train,test=c.month_split(rows,'2024-09'); self.assertEqual(len(train),2); self.assertEqual(len(test),1)
    def test_2526_excluded_before_read(self):
        self.assertEqual(c.load_inputs({'sources':[dict(season='2526',path='/not/read')]}),[])

class SourceAuditTests(unittest.TestCase):
    def test_source_validation_and_unknown_neutral(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'input.csv'
            p.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,Time\nE0,01/08/2024,A,B,1,0,15:00\n')
            m={'sources':[dict(league='E0',season='2425',path=str(p))]}
            r=c.load_inputs(m)[0]; self.assertIsNone(r['neutral']); self.assertIsNone(r['quoted_at']); self.assertEqual(r['venue_status'],'UNKNOWN'); self.assertEqual(r['source']['time_raw'],'15:00')
            m['sources'][0]['season']='2324'
            p.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,01/08/2025,A,B,1,0\n')
            with self.assertRaisesRegex(ValueError,'season'): c.load_inputs(m)
            p.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nD1,01/08/2024,A,B,1,0\n')
            with self.assertRaisesRegex(ValueError,'division'): c.load_inputs(m)
    def test_duplicate_across_seasons_rejected(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'input.csv'; p.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,01/07/2024,A,B,1,0\n')
            with self.assertRaisesRegex(ValueError,'Duplicate'):
                c.load_inputs({'sources':[dict(league='E0',season=s,path=str(p)) for s in ('2324','2425')]})

if __name__=='__main__': unittest.main()
