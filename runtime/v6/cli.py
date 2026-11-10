import argparse
import json
from pathlib import Path
from ml_model import train
from training_inputs import history_inputs


def main():
    p = argparse.ArgumentParser()
    p.add_argument('history'); p.add_argument('--out',required=True)
    a = p.parse_args()
    from competition_scope import training_rows
    artifact, report = train(history_inputs(training_rows(json.loads(Path(a.history).read_text(encoding='utf-8')))))
    directory = Path(a.out); directory.mkdir(parents=True,exist_ok=False)
    for name,data in [('artifact',artifact),('report',report)]:
        (directory/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    print(str(directory))


if __name__ == '__main__':main()
