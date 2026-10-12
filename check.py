"""uv run python check.py --n 3: 10 tasks x 3 repeats x 2 description variants."""
import argparse
import hashlib
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import urllib.request

from dotenv import load_dotenv
from openai import OpenAI
from assignment.tools import TOOLS
from assignment.cases import make_cases,evaluate
from assignment.loop import Journal,run_agent
from assignment.metrics import aggregate
from assignment.reporting import make_report

ROOT=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--n',type=int,default=3)
    parser.add_argument('--max-steps',type=int,default=6)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--no-diagnostics',action='store_true')
    args=parser.parse_args()
    if args.n<1 or args.max_steps<1:
        parser.error('--n and --max-steps must be positive')
    load_dotenv(ROOT/'.env')
    key=os.getenv('OPENROUTER_API_KEY') or os.getenv('OPENAI_API_KEY') or getpass.getpass('OpenRouter API key: ')
    if not key.strip():
        parser.error('API key is required')
    model=os.getenv('MODEL',os.getenv('OPENROUTER_MODEL','openai/gpt-6-luna'))
    url=os.getenv('OPENAI_BASE_URL','https://openrouter.ai/api/v1')
    client=OpenAI(api_key=key,base_url=url,timeout=60,max_retries=0)
    folder=args.output or ROOT/'runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder.mkdir(parents=True,exist_ok=False)
    try:
        catalog=json.load(urllib.request.urlopen('https://openrouter.ai/api/v1/models',timeout=30))
        entry=next(m for m in catalog['data'] if m['id']==model)
        pricing=entry['pricing']
    except Exception:
        # Never guess prices for a different model/provider.
        if not (os.getenv('PRICE_INPUT_PER_TOKEN') and os.getenv('PRICE_OUTPUT_PER_TOKEN')):
            raise RuntimeError('Model pricing unavailable; specify PRICE_INPUT_PER_TOKEN and PRICE_OUTPUT_PER_TOKEN') from None
        pricing={'prompt':os.environ['PRICE_INPUT_PER_TOKEN'],'completion':os.environ['PRICE_OUTPUT_PER_TOKEN']}
    cases=make_cases()
    meta={'model':model,'base_url':url,'n':args.n,'max_steps':args.max_steps,'pricing':pricing,
          'timestamp_utc':datetime.now(timezone.utc).isoformat(),
          'dataset_sha256':hashlib.sha256((ROOT/'week2/practice/poi.sqlite').read_bytes()).hexdigest(),
          'code_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'assignment').glob('*.py'))},'temperature':0.2,'reasoning_effort':'none',
          'schedule':'paired AB/BA, alternates by task and repeat','dataset':'week2/practice/poi.sqlite'}
    (folder/'experiment.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    records=[]

    def execute(case,variant,repeat,diagnostic=None):
        name=f'{case.id}_{variant}_{repeat}'+(f'_{diagnostic}' if diagnostic else '')+'.jsonl'
        journal=Journal(folder/name)
        try:
            result=run_agent(client,model,case.question,variant,journal,pricing,
                             max_steps=args.max_steps if not diagnostic else 3,diagnostic=diagnostic)
            evaluation=evaluate(case,result)
            journal.write('evaluation',task=case.id,repeat=repeat,**evaluation)
        finally:
            journal.close()
        record={'task':case.id,'variant':variant,'repeat':repeat,'diagnostic':diagnostic,
                'log':name,**result,'evaluation':evaluation}
        records.append(record)
        (folder/'records.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f"{case.id} {variant:5s} #{repeat}: {'PASS' if evaluation['passed'] else 'FAIL'} "
              f"steps={result['steps']} tokens={result['input_tokens']+result['output_tokens']} "
              f"cost=${result['cost_usd']:.6f}"+(f' diagnostic={diagnostic}' if diagnostic else ''),flush=True)

    try:
        for number,case in enumerate(cases):
            for repeat in range(1,args.n+1):
                order=('short','full') if (number+repeat)%2 else ('full','short')
                for variant in order:
                    execute(case,variant,repeat)
        # Fault injection is kept separate from the fixed A/B test, never changes its metrics.
        failures=[r for r in records if not r['evaluation']['passed']]
        if len(failures)<3 and not args.no_diagnostics:
            for diagnostic in ('database_unavailable','invalid_arguments','retry_loop')[:3-len(failures)]:
                execute(cases[0],'full',1,diagnostic)
        summary=aggregate(records,args.n)
        (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        report=make_report(meta,records,summary,folder,ROOT)
        (folder/'report.md').write_text(report.replace('../'+folder.relative_to(ROOT).as_posix()+'/', './'),encoding='utf-8')
        (ROOT/'week2'/'REPORT.md').write_text(report,encoding='utf-8')
        print(f'Results: {folder}',flush=True)
        print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    finally:
        client.close()


if __name__=='__main__':
    main()
