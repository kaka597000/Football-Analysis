"""Cloud-local scheduled updater. Process restart is required after environment restart."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

BJT=ZoneInfo('Asia/Shanghai')

def next_run(current):
    options=[current.replace(hour=h,minute=0,second=0,microsecond=0) for h in (4,8,18)]
    return next((x for x in options if x>current),options[0]+dt.timedelta(days=1))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='/workspace/WCPP');parser.add_argument('--once',action='store_true');args=parser.parse_args()
    root=Path(args.root);state=root/'data/unified/scheduler';state.mkdir(parents=True,exist_ok=True)
    lock=(state/'daemon.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Updater already running')
    (state/'pid').write_text(str(os.getpid()))
    try:status=json.loads((state/'status.json').read_text())
    except (FileNotFoundError,ValueError):status={}
    status.update(pid=os.getpid(),started_at=dt.datetime.now(BJT).isoformat(),schedule_bjt=['04:00','08:00','18:00'])
    while True:
        if args.once:
            with (state/'refresh.log').open('a') as log:
                result=subprocess.run([sys.executable,str(Path(__file__).with_name('cli.py')),'refresh','--root',str(root)],stdout=log,stderr=subprocess.STDOUT)
            status.update(last_attempt_at=dt.datetime.now(BJT).isoformat(),last_exit_code=result.returncode)
            (state/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))
            raise SystemExit(result.returncode)
        target=next_run(dt.datetime.now(BJT));status['next_run_at']=target.isoformat()
        (state/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))
        while dt.datetime.now(BJT)<target:time.sleep(min(30,max(0,(target-dt.datetime.now(BJT)).total_seconds())))
        with (state/'refresh.log').open('a') as log:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('cli.py')),'refresh','--root',str(root)],stdout=log,stderr=subprocess.STDOUT)
        status.update(last_attempt_at=dt.datetime.now(BJT).isoformat(),last_exit_code=result.returncode)

if __name__=='__main__':main()
