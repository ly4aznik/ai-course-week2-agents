"""Ten fixed tasks and database-grounded evaluators, independent of model text."""
import re
from dataclasses import dataclass
from .tools import REGISTRY, db


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    expected: list[tuple[str, dict]]
    final_facts: tuple[str, ...]


def make_cases():
    home = db.address_point('Белорусская', '44')
    chain = db.nearest(home['lat'], home['lon'], 'pharmacy', 1)[0]
    pharmacy = db.nearest(47.2357, 39.7015, 'pharmacy', 1)[0]
    bank = db.nearest(47.22, 39.71, 'bank', 1)[0]
    outpost = db.nearest(47.23, 39.70, 'outpost', 1)[0]
    cafes = REGISTRY['find_places'].function('cafe', 'Пушкин', None, 5)['places']
    ozon = REGISTRY['find_places'].function('outpost', None, 'Ozon', 5)['places']
    count = REGISTRY['count_places'].function
    return [
        Case('T01', 'Сколько всего аптек в Ростове?', [('count_places', {'kind':'pharmacy','only_24_7':False})], (str(count('pharmacy')['count']),)),
        Case('T02', 'Сколько в Ростове круглосуточных аптек?', [('count_places', {'kind':'pharmacy','only_24_7':True})], (str(count('pharmacy',True)['count']),)),
        Case('T03', 'Покажи пять кафе на Пушкинской улице.', [('find_places', {'kind':'cafe','street':'Пушкин','brand':None,'limit':5})], tuple(str(p['name'] or p['id']) for p in cafes)),
        Case('T04', 'Покажи пять пунктов выдачи сети Ozon в Ростове.', [('find_places', {'kind':'outpost','street':None,'brand':'Ozon','limit':5})], tuple(str(p['name'] or p['id']) for p in ozon)),
        Case('T05', 'Какая аптека ближе всего к 47.2357, 39.7015? Назови расстояние в метрах.', [('nearest_place', {'lat':47.2357,'lon':39.7015,'kind':'pharmacy'})], (str(pharmacy['distance_m']),)),
        Case('T06', 'Найди ближайший банк к 47.2200, 39.7100 и скажи расстояние в метрах.', [('nearest_place', {'lat':47.22,'lon':39.71,'kind':'bank'})], (str(bank['distance_m']),)),
        Case('T07', 'Найди в базе координаты дома на Белорусской улице, 44.', [('geocode', {'street':'Белорус','housenumber':'44'})], (str(home['lat']),str(home['lon']))),
        Case('T08', 'Какие часы работы указаны в карточке места с id 1728?', [('place_details', {'place_id':1728})], (str(db.details(1728)['opening_hours']),)),
        Case('T09', 'Я у дома Белорусская, 44. Найди ближайшую аптеку и узнай её часы работы по карточке.', [('geocode', {'street':'Белорус','housenumber':'44'}),('nearest_place', {'lat':home['lat'],'lon':home['lon'],'kind':'pharmacy'}),('place_details', {'place_id':chain['id']})], (str(db.details(chain['id'])['opening_hours']),)),
        Case('T10', 'Найди ближайший пункт выдачи к 47.23, 39.70, а затем его телефон по карточке. Если телефон отсутствует, скажи об этом.', [('nearest_place', {'lat':47.23,'lon':39.70,'kind':'outpost'}),('place_details', {'place_id':outpost['id']})], (str(db.details(outpost['id'])['phone'] or 'не указан'),)),
    ]


def match_args(got, want):
    for key, expected in want.items():
        value = got.get(key)
        if isinstance(expected, float):
            if not isinstance(value, (float,int)) or abs(value-expected)>1e-5:
                return False
        elif key in ('street','brand') and expected is not None:
            if expected.casefold() not in str(value or '').casefold():
                return False
        elif value != expected:
            return False
    return True


def fact_in_answer(fact, answer):
    if fact.startswith('+') and len(re.sub(r'\D','',fact))>=10:
        expected = re.sub(r'\D','',fact)
        phones = re.findall(r'\+?\d[\d ()-]{7,}\d',answer)
        return any(re.sub(r'\D','',phone)==expected for phone in phones)
    # Coordinate answers may reasonably round to five decimal places.
    if re.fullmatch(r'\d+\.\d+',fact):
        return any(abs(float(x)-float(fact))<1e-5 for x in re.findall(r'\d+\.\d+',answer.replace(',','.')))
    if fact.isdigit():
        return re.search(r'(?<!\d)'+re.escape(fact)+r'(?!\d)',answer) is not None
    if fact == 'не указан':
        return any(word in answer.casefold() for word in ('не указан','отсутств','нет телефон','не найден'))
    # Accept equivalent Russian schedules, but still require both boundaries and daily scope.
    schedule = re.fullmatch(r'Mo-Su (\d{2}:\d{2})-(\d{2}:\d{2})', fact)
    if schedule:
        text = answer.casefold().replace('–','-').replace('—','-')
        daily = any(word in text for word in ('ежедневно','каждый день','mo-su','пн-вс','пн - вс'))
        interval = re.search(re.escape(schedule[1])+r'\s*(?:-|до)\s*'+re.escape(schedule[2]), text)
        return bool(daily and interval)
    return fact.casefold() in answer.casefold()


def evaluate(case, result):
    calls = result['calls']
    # Order, number, names and arguments are checked, not guessed from final prose.
    selection = len(calls)==len(case.expected) and all(
        c['name']==name and c.get('ok') and match_args(c.get('arguments',{}),args)
        for c,(name,args) in zip(calls,case.expected))
    final = result['final']
    facts = [fact_in_answer(f,final) for f in case.final_facts]
    fact_ok = all(facts) if case.id=='T07' else any(facts)
    passed = bool(selection and result['status']=='completed' and fact_ok)
    if passed:
        category = None
    elif any(c.get('error_type')=='ValidationError' for c in calls):
        category = 'не те аргументы'
    elif result['status']=='api_error' or any(c.get('error_type')=='OSError' for c in calls):
        category = 'ошибка среды'
    elif result['status']=='max_steps' and len({(c['name'],c['requested_arguments']) for c in calls})<len(calls):
        category = 'зацикливание'
    elif [c['name'] for c in calls] != [name for name,_ in case.expected]:
        category = 'не тот инструмент' if calls else 'ложное «готово»'
    elif not selection:
        category = 'не те аргументы'
    else:
        category = 'ложное «готово»'
    return {'passed':passed,'tool_args_correct':bool(selection),'facts_correct':bool(fact_ok),
            'category':category,'expected_calls':case.expected,'expected_facts':case.final_facts}
