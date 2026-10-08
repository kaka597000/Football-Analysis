"""Generate synthetic parser fixtures. These are not real matches or training data."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'emperor_cup'

def page(number,date,total,periods,completed=False,roster=0,bench=False):
    detail=''.join('<li><span>'+('' if goals is None else str(goals[0]))+'</span><span>'+period+'</span><span>'+('' if goals is None else str(goals[1]))+'</span></li>' for period,goals in periods)
    board='<div id="score-board-header"><div class="flag">Synthetic A</div><div class="flag">Synthetic B</div>'+''.join('<div class="total-score">'+('' if g is None else str(g))+'</div>' for g in total)+'<div class="score-detail">'+detail+'</div></div>'
    rows=''.join('<tr><td>DF</td><td>'+str(i)+'</td><td>Test A'+str(i)+'</td><td>MF</td><td>'+str(i)+'</td><td>Test B'+str(i)+'</td></tr>' for i in range(1,roster+1))
    if bench:rows+='<tr><td>控え選手</td></tr><tr><td>GK</td><td>12</td><td>Bench A</td><td>GK</td><td>12</td><td>Bench B</td></tr>'
    return ('<!-- SYNTHETIC TEST FIXTURE; NOT A REAL MATCH -->\n<div class="text-schedule">['+str(number)+'] Test round '+date+' KickOff Synthetic stadium</div>'+board+('<div class="full-time">試合終了</div><div class="official-record"><a href="/test-only-record.pdf">Synthetic record</a></div>' if completed else '')+('<table class="match-result">'+rows+'</table>' if roster else '')).encode()

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    fixtures={
      '2025-m1.html':page(1,'2025年05月24日 13:00',[2,2],[('前半',[1,0]),('後半',[0,1]),('延前',[1,0]),('延後',[0,1]),('PK',[4,5])],True),
      '2024-m87.html':page(87,'2024年11月23日 14:00',[0,1],[('前半',[0,0]),('後半',[0,1])],True),
      'm62.html':page(62,'2026年10月07日 19:00',[None,None],[('前半',None),('後半',None)],roster=3),
      'm67.html':page(67,'2026年10月07日 19:00',[None,None],[('前半',None),('後半',None)],roster=11,bench=True),
      '2026-m61-synthetic.html':page(61,'2026年10月07日 19:00',[0,0],[('前半',[0,0]),('後半',None)]),
      '2026-m73-synthetic.html':b'<!-- SYNTHETIC TEST FIXTURE -->'+ '<div class="text-schedule">[73] Test future round KickOff</div><div class="flag">Synthetic placeholder</div>'.encode(),
    }
    for name,content in fixtures.items():(ROOT/name).write_bytes(content)

if __name__=='__main__':main()
