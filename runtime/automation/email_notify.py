"""Chinese training digests and failure notices; no credentials in files/logs.

SMTP acceptance is recorded separately from delivery to a recipient's inbox.
Run from a private workflow after the source workflow has completed.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr, format_datetime
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import sys
from zoneinfo import ZoneInfo

WORKFLOW='WCPP private unattended research'
HOSTS={'qq.com':'smtp.qq.com'}


def aware(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone required')
    return d.astimezone(timezone.utc)


def address(value):
    if not isinstance(value,str) or '\r' in value or '\n' in value:
        raise ValueError('single mailbox required')
    name,mailbox=parseaddr(value)
    if name or mailbox!=value or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',mailbox):
        raise ValueError('single mailbox required')
    return mailbox


def build(state_dir,event,config,*,test=False):
    """Return a bounded subject/body or an explicit skip; never include raw logs."""
    if config.get('enabled') is not True:return {'status':'SKIPPED_DISABLED'}
    repo=config['repository']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise ValueError('repository required')
    if test:
        return {'status':'READY','subject':'[WCPP] 邮件连通测试',
                'body':'HELLO BOSS\n\n这是WCPP云端邮件连通测试，不表示本次进行了采集、训练或模型晋级。\n\n查看任务：https://github.com/'+repo+'/actions\n'}
    run=event.get('workflow_run',{})
    if (event.get('repository',{}).get('full_name')!=repo or run.get('name')!=WORKFLOW
            or run.get('head_branch')!='main' or run.get('status')!='completed'):
        raise ValueError('completed trusted main workflow required')
    ident=str(run['id']);attempt=str(run.get('run_attempt',1))
    if not ident.isdigit() or not attempt.isdigit():raise ValueError('invalid workflow identity')
    end=aware(run['updated_at']);matches=[];history=[]
    for path in sorted((Path(state_dir)/'runs').glob('*.json')):
        r=json.loads(path.read_text())
        if str(r.get('github_run_id'))==ident and str(r.get('github_run_attempt'))==attempt:
            matches.append(r)
        if r.get('mode')=='collect' and r.get('status')=='COMPLETED':
            t=aware(r['finished_at'])
            if end-timedelta(hours=24)<t<=end:history.append(r)
    if len(matches)>1:raise ValueError('ambiguous workflow status records')
    record=matches[0] if matches else None
    success=(run.get('conclusion')=='success' and record is not None and record.get('status')=='COMPLETED')
    mode=record.get('mode') if record else None
    if success and mode=='collect':return {'status':'SKIPPED_SUCCESSFUL_COLLECTION','reason':'included in next training digest'}
    label='训练汇总' if success and mode=='train' else '任务异常'
    stamp=end.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
    subject=f'[WCPP] {label} {stamp}（北京时间）'
    lines=['HELLO BOSS','',f'任务：{WORKFLOW}',f'完成时间：{stamp} 北京时间',
           f'GitHub状态：{run.get("conclusion","UNKNOWN")}',f'运行ID：{ident}，尝试：{attempt}']
    if record:
        lines += [f'程序状态：{record.get("status","UNKNOWN")}',f'模式：{mode}',
                  f'源码版本：{record.get("source_commit","UNKNOWN")}']
    else:lines += ['没有取得与本次运行ID/尝试匹配的程序记录；可能在初始化、超时或保存阶段失败。未用上一轮成功结果代替。']
    if record and mode=='train':
        result=record.get('result',{});cohorts=result.get('cohorts',[])
        counts=Counter(x['status'] for x in cohorts)
        lines += ['',f'合格训练记录：{result.get("accepted_rows","UNKNOWN")}',
                  f'训练行隔离：{result.get("quarantined","UNKNOWN")}',
                  '队列状态：'+json.dumps(dict(counts),ensure_ascii=False),'',
                  '逐队列：候选/常数基线的历史精确比分Top1（不是未来实盘成绩）']
        for row in cohorts[:100]:
            detail=f'{row["cohort"]}：{row["status"]}，记录{row.get("rows","UNKNOWN")}条'
            if row.get('audit_n'):
                detail+=f'；候选{row.get("candidate_top1_hits","?")}/{row["audit_n"]}，基线{row.get("baseline_top1_hits","?")}/{row["audit_n"]}'
            lines.append(detail)
    if record and mode=='collect':
        result=record.get('result',{})
        lines += ['',f'请求/成功：{result.get("request_count","UNKNOWN")}/{result.get("successful_requests","UNKNOWN")}',
                  f'新增结果观察：{result.get("new_result_observations","UNKNOWN")}',
                  f'完整覆盖：{result.get("coverage_complete","UNKNOWN")}']
    lines += ['',f'过去24小时成功采集轮数：{len(history)}',
              f'新增赛果观察合计：{sum(r.get("result",{}).get("new_result_observations",0) for r in history)}（不是训练净增场数）',
              '',f'任务：https://github.com/{repo}/actions/runs/{ident}',
              f'详细训练：https://github.com/{repo}/blob/main/latest-training.json',
              f'运行记录：https://github.com/{repo}/tree/main/runs','',
              '候选不会自动替换当前模型或test模型。历史诊断不证明未来准确率提高。',
              '邮件不附原始数据、完整矩阵、密钥或模型文件。SMTP接受不等于已确认收件箱送达。']
    return {'status':'READY','subject':subject,'body':'\n'.join(lines)+'\n'}


def send(notice,config,*,password,smtp_factory=None):
    if notice.get('status')!='READY':return {'status':notice['status']}
    sender=address(config['sender']);recipient=address(config['recipient'])
    domain=sender.rsplit('@',1)[1].lower();host=HOSTS.get(domain)
    if host is None:raise ValueError('SMTP provider not configured; supported sender: qq.com')
    if not password:return {'status':'NOT_CONFIGURED_SMTP_PASSWORD'}
    message=EmailMessage();message['Subject']=notice['subject'];message['From']=sender;message['To']=recipient
    message['Date']=format_datetime(datetime.now(timezone.utc));message.set_content(notice['body'])
    factory=smtp_factory or smtplib.SMTP_SSL
    try:
        with factory(host,465,context=ssl.create_default_context(),timeout=25) as client:
            client.login(sender,password)
            refused=client.send_message(message,from_addr=sender,to_addrs=[recipient])
        return {'status':'FAILED_RECIPIENT_REFUSED' if refused else 'SMTP_ACCEPTED_INBOX_UNCONFIRMED'}
    except (smtplib.SMTPException,OSError,ValueError):
        # Authentication/provider exception text may contain identifying or secret data.
        return {'status':'FAILED_SMTP'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--state-dir',required=True);p.add_argument('--event',required=True)
    p.add_argument('--config',required=True);p.add_argument('--preview-out');p.add_argument('--test',action='store_true')
    a=p.parse_args();config=json.loads(Path(a.config).read_text());event=json.loads(Path(a.event).read_text())
    notice=build(a.state_dir,event,config,test=a.test)
    if a.preview_out:
        with Path(a.preview_out).open('x') as f:f.write(notice.get('subject','')+'\n\n'+notice.get('body',notice['status']))
        result={'status':'PREVIEW_ONLY_NO_SEND'}
    else:result=send(notice,config,password=os.environ.get('WCPP_SMTP_PASSWORD'))
    print(json.dumps(result))
    return int(result['status'].startswith('FAILED'))


if __name__=='__main__':sys.exit(main())
