"""Offline engineering tests; stub responses are never used in the A/B experiment."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from assignment.loop import Journal,run_agent
from assignment.tools import TOOLS,REGISTRY
from assignment.metrics import wilson,aggregate
from assignment.cases import make_cases,evaluate,fact_in_answer


class Usage:
    def model_dump(self,**kwargs):
        return {'prompt_tokens':100,'completion_tokens':20,'cost':0.001}


class Call:
    id='test-call'
    type='function'
    def __init__(self,name,args):
        self.function=SimpleNamespace(name=name,arguments=json.dumps(args))
    def model_dump(self,**kwargs):
        return {'id':self.id,'type':self.type,'function':vars(self.function)}


class FakeClient:
    def __init__(self,messages):
        self.messages=iter(messages)
        self.chat=SimpleNamespace(completions=self)
        self.requests=[]
    def create(self,**kwargs):
        self.requests.append(json.loads(json.dumps(kwargs['messages'])))
        return SimpleNamespace(id='offline-test',model='test',usage=Usage(),choices=[SimpleNamespace(message=next(self.messages))])


class Tests(unittest.TestCase):
    def test_schema_ab_same_except_descriptions(self):
        for tool in TOOLS:
            a,b=tool.schema('short'),tool.schema('full')
            a['function'].pop('description'); b['function'].pop('description')
            self.assertEqual(a,b)
            self.assertFalse(a['function']['parameters']['additionalProperties'])
        self.assertLess(len(TOOLS[0].short),len(TOOLS[0].full))

    def test_usage_error_observation_and_recovery(self):
        bad=SimpleNamespace(content=None,tool_calls=[Call('count_places',{'kind':'аптека','only_24_7':False})])
        valid=SimpleNamespace(content=None,tool_calls=[Call('count_places',{'kind':'pharmacy','only_24_7':False})])
        number=REGISTRY['count_places'].function('pharmacy')['count']
        finish=SimpleNamespace(content=str(number),tool_calls=None)
        client=FakeClient([bad,valid,finish])
        with tempfile.TemporaryDirectory() as folder:
            log=Journal(Path(folder)/'test.jsonl')
            try:
                result=run_agent(client,'test','Сколько аптек?','full',log,{'prompt':0,'completion':0})
            finally:
                log.close()
            events=[json.loads(s) for s in log.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(result['status'],'completed')
        self.assertFalse(result['calls'][0]['ok'])
        self.assertTrue(result['calls'][1]['ok'])
        self.assertIn('ValidationError',client.requests[1][-1]['content'])
        self.assertEqual(result['input_tokens'],300)
        self.assertAlmostEqual(result['cost_usd'],0.003)
        self.assertEqual(events[-1]['event'],'finish')

    def test_loop_is_bounded(self):
        message=SimpleNamespace(content=None,tool_calls=[Call('count_places',{'kind':'pharmacy','only_24_7':False})])
        with tempfile.TemporaryDirectory() as folder:
            log=Journal(Path(folder)/'test.jsonl')
            try:
                result=run_agent(FakeClient([message]*10),'test','Сколько аптек?','short',log,{'prompt':0,'completion':0},max_steps=2)
            finally:
                log.close()
        self.assertEqual(result['status'],'max_steps')
        self.assertEqual(result['steps'],2)

    def test_ground_truth_and_false_ready(self):
        cases=make_cases()
        self.assertEqual(len(cases),10)
        self.assertEqual(cases[1].final_facts,('19',))
        self.assertFalse(evaluate(cases[0],{'calls':[],'final':'Готово','status':'completed'})['passed'])

    def test_equivalent_schedule_and_wrong_schedule(self):
        self.assertTrue(fact_in_answer('Mo-Su 08:00-21:00','Ежедневно с 08:00 до 21:00.'))
        self.assertTrue(fact_in_answer('Mo-Su 08:00-21:00','Mo-Su 08:00–21:00'))
        self.assertFalse(fact_in_answer('Mo-Su 08:00-21:00','Понедельник с 08:00 до 21:00'))
        self.assertFalse(fact_in_answer('Mo-Su 08:00-21:00','Ежедневно с 08:00 до 20:00'))

    def test_phone_formatting_without_changing_digits(self):
        self.assertTrue(fact_in_answer('+7 800 6000001','Телефон: **+7 800 600-00-01**.'))
        self.assertFalse(fact_in_answer('+7 800 6000001','Телефон: +7 800 600-00-02.'))

    def test_metrics_repeated_tasks_not_power_of_mean(self):
        records=[]
        for variant in ('short','full'):
            for task,passes in [('A',[True,True,True]),('B',[True,False,True])]:
                for i,p in enumerate(passes,1):
                    records.append({'variant':variant,'task':task,'repeat':i,'evaluation':{'passed':p,'tool_args_correct':p},
                                    'input_tokens':10,'output_tokens':2,'steps':2,'cost_usd':0.001,'cost_sources':['test']})
        summary=aggregate(records,3)['short']
        self.assertAlmostEqual(summary['pass_at_1'],5/6)
        self.assertEqual(summary['pass_power_n'],0.5)
        low,high=wilson(0,30)
        self.assertAlmostEqual(low,0)
        self.assertAlmostEqual(high,0.113513393,places=8)


if __name__=='__main__':
    unittest.main()
