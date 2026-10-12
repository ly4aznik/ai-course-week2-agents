"""A concise report derived solely from saved experiment records."""
from pathlib import Path


def make_report(meta,records,summary,folder,root):
    a,b=summary['short'],summary['full']
    n=meta['n']
    rows=[]
    for label,key,fmt in [
        ('Успешные попытки (pass@1)','pass_at_1','.1%'),
        ('Успех первого повтора','first_repeat_success','.1%'),
        (f'Все {n} повтора успешны (pass^{n})','pass_power_n','.1%'),
        ('Верный инструмент и аргументы','tool_args_rate','.1%'),
        ('Средние входные токены','mean_input_tokens','.1f'),
        ('Средние выходные токены','mean_output_tokens','.1f'),
        ('Средние суммарные токены','mean_total_tokens','.1f'),
        ('Среднее число шагов','mean_steps','.2f'),
        ('Средняя стоимость задачи, USD','mean_cost_usd','.6f')]:
        rows.append(f'| {label} | {format(a[key],fmt)} | {format(b[key],fmt)} |')
    wilson=lambda group: '–'.join(f'{v:.1%}' for v in group['wilson95'])
    rows.insert(4,f"| 95% Вильсон: инструмент и аргументы | {wilson(a)} | {wilson(b)} |")
    c=summary['comparison']
    gained=c['additional_correct_attempts']
    if gained>0:
        economic=f"Добавлено {gained} правильных попыток; цена каждой дополнительной — ${c['cost_per_additional_correct_attempt_usd']:.6f}. "
    else:
        economic=f"Добавлено {gained} правильных попыток; стоимость дополнительного успеха не определена, поскольку прирост не положительный. "
    if c['cost_per_correct_full'] is not None and c['cost_per_correct_short'] is not None:
        economic+=f"Цена правильной попытки: short ${c['cost_per_correct_short']:.6f}, full ${c['cost_per_correct_full']:.6f}. "
        economic+='По этому критерию полные описания '+('окупились' if c['cost_per_correct_full']<c['cost_per_correct_short'] else 'не окупились')+' на данной выборке.'
    failures=[r for r in records if not r['evaluation']['passed']]
    # Prefer different categories and tasks; diagnostic examples are identified explicitly.
    examples=[]
    for r in sorted(failures,key=lambda r: bool(r['diagnostic'])):
        if len(examples)>=3:
            break
        if any(e['evaluation']['category']==r['evaluation']['category'] and e['task']==r['task'] for e in examples):
            continue
        examples.append(r)
    if len(examples)<3:
        examples+= [r for r in failures if r not in examples][:3-len(examples)]
    details=[]
    for index,r in enumerate(examples,1):
        log=(folder/r['log']).relative_to(root).as_posix()
        trace=' → '.join(f"{call['name']}({call['requested_arguments']})" for call in r['calls']) or 'инструменты не вызваны'
        trace=trace[:520]
        errors=', '.join(sorted({call.get('error_type','') for call in r['calls'] if not call['ok']}))
        mode='контролируемая диагностика '+str(r['diagnostic']) if r['diagnostic'] else 'основной A/B-прогон'
        diagnosis=r['evaluation']['category']
        if r['diagnostic']=='invalid_arguments':
            cause='Модель запросила допустимый вызов, но диагностический диспетчер заменил kind на «аптека». Валидатор отклонил аргументы; это искусственно внесённый сбой интерфейса, а не промах модели.'
        elif r['diagnostic']=='database_unavailable':
            cause='Диспетчер имитировал недоступную базу и вернул OSError как наблюдение. Данных для правильного ответа не появилось; цикл продолжил работу и завершился без падения процесса.'
        elif r['diagnostic']=='retry_loop':
            cause='Диагностический инструмент каждый раз требовал повтор, а диагностическая инструкция предписывала повторять retryable-вызов. Реальная модель повторяла его; предохранитель остановил цикл на трёх шагах.'
        elif diagnosis=='не те аргументы':
            cause='Имя инструмента выбрано, но параметры или фильтр не совпали с условиями задачи. Нужны явные единицы и пример аргументов в описании, затем повторный независимый эксперимент.'
        elif diagnosis=='не тот инструмент':
            cause='Набор или порядок вызовов не соответствовал задаче. Для адресной цепочки каждый следующий вызов должен брать координаты или id из предыдущего результата.'
        elif diagnosis=='ложное «готово»':
            cause='Модель закончила текстовым ответом, но обязательные вызовы или проверяемый факт не выполнены. Финальный текст не считается успехом сам по себе.'
        else:
            cause='Сбой или повторные вызовы не дали проверяемого результата. Ошибка сохранена в наблюдениях, лимит не позволяет продолжать бесконечно.'
        details.append(f"**{index}. {r['task']}, {r['variant']}, повтор {r['repeat']} — {diagnosis}.** {mode}. "
                       f"Траектория: `{trace}`. Итог: `{r['status']}`"+(f", ошибки `{errors}`" if errors else '')+f". {cause} [JSONL](../{log}).")
    diagnostics=[r for r in records if r['diagnostic']]
    total=sum(r['cost_usd'] for r in records)
    sources=sorted({s for r in records for s in r['cost_sources']})
    return f'''# Задание 1: инструменты и цикл без фреймворка

**12 октября 2026.** Модель `{meta['model']}`, OpenRouter, Chat Completions, reasoning_effort=none, temperature=0.2. Пять инструментов: count_places, find_places, nearest_place, geocode, place_details. Данные — исходная база Ростова, 8860 мест (OSM/ODbL). Ручной цикл использует только OpenAI-клиент: строгая схема, проверка аргументов, ошибки как наблюдения, JSONL, лимит {meta['max_steps']} шагов (до трёх транспортных попыток на шаг), usage каждого ответа. Результаты поиска ограничены 10 строками, расстояния — метры по прямой.

## Метрики

Десять фиксированных задач, {n} независимых повтора без истории на каждый вариант: {20*n} основных траекторий. Вопросы охватывают подсчёт, фильтры улицы/бренда, ближайшее место, координаты, карточку и две цепочки. Истина вычисляется по базе; проверяются последовательность вызовов, аргументы и факт в финальном ответе. A/B меняет только description пяти инструментов; параметры, схема, вопрос, модель, настройки и лимит одинаковы. Порядок пар AB/BA чередуется.

| Метрика | Короткие | Полные |
|---|---:|---:|
{chr(10).join(rows)}

pass@1 — доля успешных отдельных попыток, усреднённая по всем {10*n} прогонам варианта; pass^{n} — доля из десяти задач, у которых успешны все {n} повтора, а не степень среднего pass@1. Шаг — один логический запрос к модели, включая финальный ответ; транспортные повторы отдельно записаны в журнал; вызовы инструментов отдельно видны в JSONL. Вильсон рассчитан для доли правильных инструментов и аргументов с z=1.96. Повторы одной задачи зависимы: интервал описательный, а не гарантия переноса на другие задачи. Текстовая проверка фактов ограничена заранее заданными числами, названиями и расписаниями; это не независимая оценка всей семантики ответа.

## Окупились ли описания

Полный вариант добавил в среднем **{c['additional_mean_input_tokens']:.1f} входных токенов** и **${c['additional_cost_usd']:.6f}** на {10*n} попыток. {economic} Три повтора и десять задач не позволяют уверенно обобщить эффект. Стоимость взята из `{', '.join(sources)}`; при отсутствии usage.cost код использует отдельно помеченную оценку по каталогу без скидки кэша. Все запросы этого эксперимента вместе с диагностикой: **${total:.6f}**, диагностика — **${sum(r['cost_usd'] for r in diagnostics):.6f}**. Это не расходы прежнего ноутбука.

## Три неудачные траектории

{chr(10).join(chr(10)+d for d in details)}

Диагностические траектории ({len(diagnostics)}) используют настоящую модель и явно внесённые сбои; они не входят в pass@1, pass^{n} или A/B-интервалы. Они добавлены только при нехватке трёх естественных неудач. Категории в оценщике: не тот инструмент, не те аргументы, ошибка среды, зацикливание, ложное «готово».

**Воспроизведение:** из корня `uv run python check.py --n 3`; ключ — через getpass или окружение/.env. Настройки в `.env.example`, пакеты в `uv.lock`. [Сырые результаты](../{folder.relative_to(root).as_posix()}/records.json), [метрики](../{folder.relative_to(root).as_posix()}/summary.json), [параметры и цены](../{folder.relative_to(root).as_posix()}/experiment.json). Ключи и HTTP-заголовки не записываются в журналы.
'''
