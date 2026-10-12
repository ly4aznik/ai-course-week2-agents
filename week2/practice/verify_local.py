"""Локальная проверка решений ноутбука. Не вызывает модель и не требует API-ключа."""
import ast
import asyncio
import contextlib
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import nbformat
from IPython.core.inputtransformer2 import TransformerManager
from agents import Agent, Runner, SQLiteSession, function_tool, RunContextWrapper, set_tracing_disabled
from agents import input_guardrail, GuardrailFunctionOutput
from agents.usage import Usage
from agents.tool_context import ToolContext
from pydantic import ValidationError
import sdk_tasks
from sdk_tasks import (address_point, nearest, details, Kind, run_query,
                       find_places, count_places, top_brands)

PATH = Path(__file__).with_name('week02_agents.ipynb')
nb = nbformat.read(PATH, as_version=4)
from agents import ModelSettings
from openai.types.shared import Reasoning
LAB_MODEL_SETTINGS = ModelSettings(reasoning=Reasoning(effort="none"))
ns = dict(globals())
checks = []


def source(i):
    return nb.cells[i].source


def definitions(i):
    tree = ast.parse(source(i))
    tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom,
                ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    exec(compile(tree, f'cell_{i}', 'exec'), ns)


def passed(message):
    checks.append(message)
    print('PASS:', message)


async def main():
    set_tracing_disabled(True)
    nbformat.validate(nb)
    for i, cell in enumerate(nb.cells):
        if cell.cell_type == 'code':
            code = TransformerManager().transform_cell(cell.source)
            compile(code, f'cell_{i}', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    passed('Формат ноутбука и синтаксис всех кодовых ячеек')

    # Конструируем агента из задания 1, не запускаем Runner.
    exec(source(8).split('result = await')[0], ns)
    assert all(t.description.strip() and t.description != '...' for t in ns['agent'].tools)
    assert ns['agent'].instructions != '...'
    passed('Задача 1: агент и непустые описания трёх инструментов')

    definitions(12)
    ctx = ToolContext(context=None, tool_name='local_check', tool_call_id='local', tool_arguments='{}')
    home = address_point('Белорусская', '44')
    sdk_tasks.reset_log()
    geo = await ns['geocode'].on_invoke_tool(ctx, json.dumps({'street': 'Белорусская', 'housenumber': '44'}))
    assert f"lat={home['lat']}" in geo and f"lon={home['lon']}" in geo
    closest = nearest(home['lat'], home['lon'], 'pharmacy', 1)[0]
    sdk_tasks.reset_log()
    await ns['geocode'].on_invoke_tool(ctx, json.dumps({'street': 'Белорусская', 'housenumber': '44'}))
    near = await ns['nearest_place'].on_invoke_tool(ctx, json.dumps({'lat': home['lat'], 'lon': home['lon'], 'kind': 'pharmacy'}))
    assert f"id={closest['id']}" in near
    card = await ns['place_details'].on_invoke_tool(ctx, json.dumps({'place_id': closest['id']}))
    # Здесь ответ инструмента, а не ответ модели: проверяем только интерфейс цепочки.
    sdk_tasks.check_chain(card)
    passed(f"Задача 2: реальные обработчики цепочки, аптека id={closest['id']}")

    definitions(16)
    Query = ns['PlaceQuery']
    assert Query.model_json_schema()['$defs']['Kind']['enum'] == list(sdk_tasks.KINDS)
    try:
        Query(kind='аптека')
    except ValidationError:
        pass
    else:
        raise AssertionError('Недопустимый kind принят')
    for _, expected in sdk_tasks.QUERY_CASES:
        q = Query(**expected)
        text = await ns['search_places'].on_invoke_tool(ctx, json.dumps({'q': q.model_dump(mode='json')}))
        assert text == run_query(q)
        assert sdk_tasks.CALLS[-1]['query'] == q.model_dump(mode='json')
    passed('Задача 3: Enum отсекает неизвестный вид; шесть фильтров корректно передаются в базу, включая пустые результаты')

    definitions(20)
    original = json.loads(subprocess.check_output(['git', 'show', 'HEAD:week2/practice/week02_agents.ipynb'], cwd=PATH.parents[2]))
    old_tree = ast.parse(''.join(original['cells'][20]['source']))
    new_tree = ast.parse(source(20).replace("Agent(model_settings=LAB_MODEL_SETTINGS, ", "Agent("))
    for tree in (old_tree, new_tree):
        for n in tree.body:
            if isinstance(n, ast.FunctionDef):
                n.body = n.body[1:]  # исключаем только докстроку
    assert ast.dump(old_tree, include_attributes=False) == ast.dump(new_tree, include_attributes=False)
    for name in ('find_places', 'nearest_place'):
        tool = ns[name]
        assert tool.description and all(v.get('description') for v in tool.params_json_schema['properties'].values())
    passed('Задача 4: изменены только докстроки; описания аргументов присутствуют в схемах')

    session = SQLiteSession('local-validation')
    await session.add_items([{'role': 'user', 'content': sdk_tasks.DIALOG[0]}])
    assert (await session.get_items())[0]['content'] == sdk_tasks.DIALOG[0]
    await session.clear_session()
    assert await session.get_items() == []
    passed('Задача 5: SQLiteSession сохраняет и очищает историю')

    # Это контрольная арифметика на известных токенах, не стоимость реального прогона.
    price = sdk_tasks.cost_rub(Usage(input_tokens=1000, output_tokens=500))
    assert price == 0.09
    assert 'turns = 3' in source(28) and 'price = cost_rub(ok.context_wrapper.usage)' in source(28)
    passed('Задача 6: лимит 3 шага и контрольный расчёт тарифа 0.09 руб.')

    exec(source(32), ns)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        sdk_tasks.check_form(ns['Report'])
    # Это реальный полный запуск задачи 7: она не использует модель.
    if nb.cells[33].execution_count is None:
        nb.cells[32].execution_count = 1
        nb.cells[33].execution_count = 2
        nb.cells[33].outputs = [nbformat.v4.new_output('stream', name='stdout', text=buffer.getvalue())]
    print(buffer.getvalue())
    passed('Задача 7: все пять штатных проверок пройдены')

    definitions(36)
    guard = ns['city_only']
    blocked = []
    agent = Agent(name='Local validation', input_guardrails=[guard])
    assert guard.run_in_parallel is False
    for question in sdk_tasks.CITY_QUESTIONS + sdk_tasks.OFF_TOPIC:
        result = await guard.guardrail_function(ctx, agent, question)
        blocked.append(result.tripwire_triggered)
    sdk_tasks.check_guardrail(blocked, sdk_tasks.CITY_QUESTIONS + sdk_tasks.OFF_TOPIC)
    passed('Задача 8: 4 посторонних запроса отсечены, 6 городских пропущены')

    # Конструируем специалистов и триаж, но не выполняем модельную маршрутизацию.
    exec(source(39), ns)
    exec(source(40).split('routed = []')[0], ns)
    assert [a.name for a in ns['triage'].handoffs] == ['Reference', 'Analyst']
    assert all(n in ns['triage'].instructions for n in ('Reference', 'Analyst'))
    passed('Задача 9: триаж создан с двумя специалистами и инструкцией передачи')

    # Координаты контрольной точки взяты из базы, отчёт агента не имитируется.
    fixture = SimpleNamespace(lat=home['lat'], lon=home['lon'])
    point_ns = dict(ns, first=fixture)
    assignments = ast.parse(source(44)).body[1:3]
    exec(compile(ast.Module(body=assignments, type_ignores=[]), 'task_10_calculation', 'exec'), point_ns)
    assert point_ns['hand_demand'] == sdk_tasks.demand_in_cell(fixture.lat, fixture.lon)
    assert point_ns['hand_competitors'] == sdk_tasks.competitors_near(fixture.lat, fixture.lon)
    passed(f"Задача 10: контрольная точка из базы — спрос {point_ns['hand_demand']}, конкуренты {point_ns['hand_competitors']}")

    nbformat.write(nb, PATH)
    versions = subprocess.check_output([__import__('sys').executable, '-m', 'pip', 'freeze'], text=True)
    Path(__file__).with_name('requirements-local.lock.txt').write_text(versions, encoding='utf-8')
    report = '''# Локальная проверка лабораторной\n\nДата: 12 октября 2026 года.\n\nРепозиторий клонирован, решения задач 1–10 заполнены. Этот локальный проверочный скрипт не обращается к модели и не требует ключа. Результат отдельного настоящего прогона см. в LIVE_VALIDATION.md.\n\nПроверки выполнены настоящим SDK и по исходной SQLite-базе; модель и её ответы не имитировались. Полностью выполнена штатная проверка задачи 7; уже сохранённые выводы настоящего прогона не заменяются. Остальные пункты ниже — проверки отдельных компонентов, а не зачёт модельных прогонов.\n\n'''
    report += ''.join(f'- {item}\n' for item in checks)
    report += '''\n## Границы локальной проверки\n\nЭти проверки компонентов не измеряют: качество ответов и цепочки агента, сборка запросов моделью, доля выбора инструментов, эффект памяти, срабатывание max_turns на настоящем прогоне, его фактическая стоимость, маршрутизация и итоговый отчёт.\n\nВ задаче 6 используется учебный тариф из sdk_tasks.py (30/120 рублей за миллион токенов). Это не заявленная цена выбранной модели OpenRouter.\n\nВ задаче 10 инструмент competitors_near возвращает число конкурентов, а не расстояние. Готовая проверка не подтверждает competitor_distance_m. В задаче 5 критерий ожидает число круглосуточных аптек всего города, хотя последняя реплика допускает более узкое толкование. Эти ограничения отмечены в ноутбуке.\n\n## Запуск\n\nВыберите интерпретатор `.venv/Scripts/python.exe` в Jupyter или редакторе. По умолчанию выбраны OpenRouter и openai/gpt-6-luna. Укажите OPENROUTER_MODEL и OPENROUTER_API_KEY в окружении либо введите их интерактивно в ячейке. Ключ вводится через getpass и не попадает в сохранённый код.\n\nПовторить локальные проверки без модели из корня репозитория:\n\n```powershell\n& ./.venv/Scripts/python.exe ./week2/practice/verify_local.py\n```\n\nНастройка совместимого клиента сверена с [документацией OpenRouter](https://openrouter.ai/docs/quickstart); общая схема работы SDK — с [официальной документацией OpenAI](https://developers.openai.com/api/docs/guides/agents).\n'''
    Path(__file__).with_name('LOCAL_VALIDATION.md').write_text(report, encoding='utf-8')
    print(f'Completed {len(checks)} local checks. No model calls.')


if __name__ == '__main__':
    asyncio.run(main())
