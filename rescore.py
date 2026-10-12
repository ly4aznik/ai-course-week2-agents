"""Re-score original trajectories without model calls; optional real-model diagnostics."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from assignment.cases import make_cases,evaluate
from assignment.loop import Journal,run_agent
from assignment.metrics import aggregate
from assignment.reporting import make_report

ROOT=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('folder',type=Path)
    parser.add_argument('--diagnostics',action='store_true')
    args=parser.parse_args()
    folder=args.folder.resolve()
    meta=json.loads((folder/'experiment.json').read_text(encoding='utf-8'))
    records=json.loads((folder/'records.json').read_text(encoding='utf-8'))
    cases={case.id:case for case in make_cases()}
    if not (folder/'records.v1.json').exists():
        (folder/'records.v1.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'summary.v1.json').write_text((folder/'summary.json').read_text(encoding='utf-8'),encoding='utf-8')
    for record in records:
        old=record['evaluation']
        record.setdefault('original_evaluation',old)
        new=json.loads(json.dumps(evaluate(cases[record['task']],record),ensure_ascii=False))
        record['evaluation']=new
        if old!=new:
            with (folder/record['log']).open('a',encoding='utf-8') as file:
                file.write(json.dumps({'event':'reevaluation','version':'v2-format-normalization',
                                      'old':old,'new':new},ensure_ascii=False)+'\n')
    changed=sum(r['original_evaluation']!=r['evaluation'] for r in records)
    print(f'Re-scored {len(records)} trajectories; changed {changed}; no new model calls for replay.',flush=True)
    if args.diagnostics and len([r for r in records if not r['evaluation']['passed']])<3:
        load_dotenv(ROOT/'.env')
        key=os.getenv('OPENROUTER_API_KEY') or os.getenv('OPENAI_API_KEY') or getpass.getpass('OpenRouter API key: ')
        with OpenAI(api_key=key,base_url=meta['base_url'],timeout=60,max_retries=0) as client:
            for mode in ('database_unavailable','invalid_arguments','retry_loop'):
                if any(r.get('diagnostic')==mode for r in records):
                    continue
                case=cases['T01']
                name=f'T01_full_1_{mode}.jsonl'
                journal=Journal(folder/name)
                try:
                    result=run_agent(client,meta['model'],case.question,'full',journal,meta['pricing'],max_steps=3,diagnostic=mode)
                    assessment=evaluate(case,result)
                    journal.write('evaluation',task=case.id,repeat=1,**assessment)
                finally:
                    journal.close()
                records.append({'task':case.id,'variant':'full','repeat':1,'diagnostic':mode,'log':name,**result,'evaluation':assessment})
                print(f"Diagnostic {mode}: {assessment['category']}, status={result['status']}, steps={result['steps']}",flush=True)
    summary=aggregate(records,meta['n'])
    meta['evaluation_revision']={'version':'v2-format-normalization','changed_attempts':changed,
        'reason':'Equivalent Russian time intervals and phone separators were incorrectly rejected by v1',
        'cases_sha256':hashlib.sha256((ROOT/'assignment/cases.py').read_bytes()).hexdigest()}
    cached={}
    for variant in ('short','full'):
        sample=[r for r in records if r['variant']==variant and not r.get('diagnostic')]
        values=[]
        for record in sample:
            events=[json.loads(line) for line in (folder/record['log']).read_text(encoding='utf-8').splitlines()]
            values.append(sum((e['usage'].get('prompt_tokens_details') or {}).get('cached_tokens',0) for e in events if e['event']=='response'))
        cached[variant]=sum(values)/len(values)
    summary['cache']={'mean_cached_input_tokens':cached}
    report=make_report(meta,records,summary,folder,ROOT)
    report=report.replace('Полный вариант добавил в среднем', 'Полный вариант изменил расход в среднем на')
    explanation=(f"\nСредние кэшированные входные токены: short **{cached['short']:.1f}**, full **{cached['full']:.1f}**. "
                 'Кэш провайдера не контролировался; более низкая цена полного варианта отражает наблюдавшийся кэш, а не доказанное преимущество длинных описаний. '
                 'По качеству выбора инструментов прироста нет.\n\n'
                 f'**Исправление оценщика:** {changed} ответов первоначально отвергались из-за записи часов «с … до …» и дефисов в телефоне. '
                 'Нормализация применена симметрично к обоим вариантам без повторных запросов. Первые оценки сохранены в records.v1.json/summary.v1.json и в JSONL, новые добавлены событиями reevaluation. '
                 'Содержание траекторий, инструменты и экспериментальные запросы не изменены; текущий оценщик покрыт отдельными тестами расписаний и телефонов.\n')
    report=report.replace('## Три неудачные траектории',explanation+'\n## Три неудачные траектории')
    for name,data in [('records.json',records),('summary.json',summary),('experiment.json',meta)]:
        (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'week2/REPORT.md').write_text(report,encoding='utf-8')
    (folder/'report.md').write_text(report.replace('../'+folder.relative_to(ROOT).as_posix()+'/', './'),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()
