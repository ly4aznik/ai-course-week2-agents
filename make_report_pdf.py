"""Create the two-page submission report from actual saved measurements."""
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parent
folder=ROOT/'runs/20261012T182559Z'
s=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
records=json.loads((folder/'records.json').read_text(encoding='utf-8'))
a,b=s['short'],s['full']
font_candidates=[('C:/Windows/Fonts/arial.ttf','C:/Windows/Fonts/arialbd.ttf'),('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')]
if os.getenv('REPORT_FONT') and os.getenv('REPORT_FONT_BOLD'):
    font_candidates.insert(0,(os.environ['REPORT_FONT'],os.environ['REPORT_FONT_BOLD']))
font_pair=next((pair for pair in font_candidates if all(Path(f).is_file() for f in pair)),None)
if font_pair is None:
    raise RuntimeError('Set REPORT_FONT and REPORT_FONT_BOLD to Cyrillic TrueType fonts')
pdfmetrics.registerFont(TTFont('Report',font_pair[0]))
pdfmetrics.registerFont(TTFont('ReportBold',font_pair[1]))
pdfmetrics.registerFontFamily('Report',normal='Report',bold='ReportBold')
styles={
 'title':ParagraphStyle('Title',fontName='ReportBold',fontSize=18,leading=21,textColor=colors.HexColor('#163A50'),spaceAfter=8),
 'subtitle':ParagraphStyle('Subtitle',fontName='Report',fontSize=9,leading=12,textColor=colors.HexColor('#506575'),spaceAfter=12),
 'body':ParagraphStyle('Body',fontName='Report',fontSize=10,leading=13.4,spaceAfter=7),
 'h2':ParagraphStyle('H2',fontName='ReportBold',fontSize=12,leading=15,spaceBefore=9,spaceAfter=6,textColor=colors.HexColor('#163A50')),
 'small':ParagraphStyle('Small',fontName='Report',fontSize=8.8,leading=12,spaceAfter=6),
 'cell':ParagraphStyle('Cell',fontName='Report',fontSize=9,leading=12),
}

def P(text,style='body'):
    return Paragraph(text,styles[style])

story=[P('Задание 1. Инструменты и ручной цикл','title'),
 P('12 октября 2026 • GPT-6 Luna / OpenRouter • 60 основных и 3 диагностических траектории','subtitle'),
 P('Пять инструментов над исходной базой Ростова (8860 мест, OSM/ODbL): <b>count_places, find_places, nearest_place, geocode, place_details</b>. Цикл написан без агентного фреймворка: OpenAI-клиент, строгие схемы, проверка аргументов, ошибки как наблюдения, JSONL и usage. Лимит - 6 шагов; на шаг допускаются до 3 транспортных попыток. Списки ограничены 10 строками, расстояния - метры по прямой.'),
 P('Протокол и метрики','h2'),
 P('Десять фиксированных задач по три независимых повтора на вариант. Короткий вариант содержит одну фразу, полный - назначение, границы применимости, единицы и пример. Менялось только описание; схемы и настройки одинаковы. Порядок пар AB/BA чередуется; reasoning_effort=none, temperature=0.2. Проверяются вызовы, аргументы и заранее заданные факты по базе.')]
data=[['Метрика','Короткие','Полные']]
for label,key,fmt in [('pass@1','pass_at_1','.1%'),('pass^3','pass_power_n','.1%'),('Инструмент и аргументы','tool_args_rate','.1%'),('Средние входные токены','mean_input_tokens','.1f'),('Средние выходные токены','mean_output_tokens','.1f'),('Средние суммарные токены','mean_total_tokens','.1f'),('Средние шаги','mean_steps','.2f'),('Средняя цена задачи, USD','mean_cost_usd','.6f')]:
 data.append([label,format(a[key],fmt),format(b[key],fmt)])
data.insert(4,['95% Вильсон (выбор инструмента)','88.6%-100%','88.6%-100%'])
data=[[P(escape(v),'cell') for v in row] for row in data]
table=Table(data,colWidths=[275,115,115],hAlign='LEFT')
table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E6EFF4')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F5F8FA')]),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('LINEBELOW',(0,0),(-1,0),0.7,colors.HexColor('#A9BBC8'))]))
story += [table,Spacer(1,8),P('pass@1 = успешные отдельные попытки / 30. pass^3 = доля из 10 задач с успехом всех трёх повторов, а не куб среднего. Шаг включает финальный ответ. Вильсон: z=1.96, 30/30 правильных выборов; повторы одной задачи зависимы, поэтому интервал описательный. Проверка фактов не оценивает всю семантику ответа.','small'),P('Окупились ли полные описания','h2')]
saving=(1-b['mean_cost_usd']/a['mean_cost_usd'])*100
story += [P(f'Прирост правильных выборов: <b>0 из 30</b>. Дополнительный вход: <b>1303.9 токена</b> на задачу. Фактическая цена правильной попытки снизилась с ${a["mean_cost_usd"]:.6f} до ${b["mean_cost_usd"]:.6f} (<b>{saving:.1f}%</b>); экономия на 30 попытках - <b>$0.004183</b>. По цене полного прогона описания окупились на этой выборке, по качеству прироста нет.'),P('Средние кэшированные входные токены: 179.3 против 3166.3. Кэш провайдера не контролировался: экономия отражает наблюдавшийся кэш, а не доказанную пользу длинного текста. Цена взята из usage.cost; три повтора и десять задач недостаточны для обобщения.','small'),PageBreak(),P('Ошибки, проверка и воспроизведение','title'),P('Три диагностические неудачи не входят в A/B-метрики','subtitle'),P('После исправления оценщика естественных неудач в 60 траекториях не осталось. Для проверки устойчивости выполнены три явно помеченных сценария с настоящей моделью и искусственно внесёнными сбоями. Их нельзя трактовать как частоту ошибок модели на обычных задачах.'),P('1. Недоступная база - ошибка среды','h2'),P('T01, full. Модель дважды вызвала count_places(kind=pharmacy, only_24_7=false). Диспетчер каждый раз вернул OSError как наблюдение. После трёх шагов модель честно завершила ответ без проверяемого числа; задача не решена, процесс не упал. Ложный успех не засчитан. Источник: T01_full_1_database_unavailable.jsonl.'),P('2. Повреждённый kind - не те аргументы','h2'),P('T01, full. Модель запросила корректный kind=pharmacy, но диагностический диспетчер заменил его на «аптека». Валидатор дважды вернул ValidationError. Финальный ответ не выполнил задачу; цикл сохранил ошибки вместо падения. Это контролируемый сбой интерфейса, а не неправильные аргументы, выбранные моделью. Источник: T01_full_1_invalid_arguments.jsonl.'),P('3. Повторяемое наблюдение - зацикливание','h2'),P('T01, full. Инструмент трижды возвращал retryable=true и требование повторить вызов. Диагностическая инструкция предписывала повторять до успеха. Модель трижды запросила тот же count_places; max_steps=3 остановил цикл. Источник: T01_full_1_retry_loop.jsonl. Предохранитель ограничил и число шагов, и стоимость.'),P('Исправление оценщика и прозрачность данных','h2'),P('Первоначальный оценщик ошибочно отверг 18 правильных ответов из-за записи «с 08:00 до 21:00» и дефисов в телефонах. Нормализация применена симметрично, без повторных модельных запросов. Оригинальные оценки сохранены в records.v1.json и summary.v1.json; новые добавлены событиями reevaluation. Вопросы, описания, ответы и tool_calls не менялись. Есть отдельные тесты верных и неверных расписаний и телефонов.'),P('Воспроизведение','h2'),P('<b>uv run python check.py --n 3</b> - из корня репозитория; ключ вводится скрыто или задаётся в окружении/.env. uv.lock фиксирует зависимости. Инженерные проверки: <b>uv run python -m unittest test_assignment -v</b> (7 тестов; изолированное окружение также проверено). Журналы: runs/20261012T182559Z, метрики: summary.json, параметры и отпечатки: experiment.json. Ключи и HTTP-заголовки не сохраняются.'),P(f'Всего в этом эксперименте: <b>${sum(r["cost_usd"] for r in records):.6f}</b>, из них диагностика - <b>${sum(r["cost_usd"] for r in records if r["diagnostic"]):.6f}</b>. Расходы прежнего ноутбука сюда не входят.','small')]

out=ROOT/'output/pdf/assignment_report.pdf'
out.parent.mkdir(parents=True,exist_ok=True)

def footer(canvas,doc):
 canvas.setStrokeColor(colors.HexColor('#CBD6DD')); canvas.line(45,39,A4[0]-45,39)
 canvas.setFont('Report',8); canvas.setFillColor(colors.HexColor('#506575'))
 canvas.drawString(45,26,'Неделя 2 • ai_course_mmcs • воспроизводимый эксперимент')
 canvas.drawRightString(A4[0]-45,26,f'{doc.page} / 2')

SimpleDocTemplate(str(out),pagesize=A4,leftMargin=45,rightMargin=45,topMargin=38,bottomMargin=50).build(story,onFirstPage=footer,onLaterPages=footer)
reader=PdfReader(out)
assert len(reader.pages)==2, f'Expected 2 pages, got {len(reader.pages)}'
assert all(len(page.extract_text())>500 for page in reader.pages)
print('Created verified two-page report:',out)
