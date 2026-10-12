"""Five bounded tools backed by the original read-only Rostov dataset."""
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

PRACTICE = Path(__file__).resolve().parents[1] / 'week2/practice'
sys.path.insert(0, str(PRACTICE))
import sdk_tasks as db


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CountArgs(Args):
    kind: db.Kind
    only_24_7: bool = False


class FindArgs(Args):
    kind: db.Kind
    street: str | None = None
    brand: str | None = None
    limit: int = Field(5, ge=1, le=10)


class NearestArgs(Args):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    kind: db.Kind


class GeocodeArgs(Args):
    street: str = Field(min_length=1, max_length=100)
    housenumber: str = Field(min_length=1, max_length=20)


class DetailsArgs(Args):
    place_id: int = Field(gt=0)


def count_places(kind, only_24_7=False):
    with sqlite3.connect(f'file:{db.DB.as_posix()}?mode=ro', uri=True) as con:
        query = 'SELECT COUNT(*) FROM poi WHERE kind=?'
        if only_24_7:
            query += ' AND is_24_7=1'
        return {'kind': kind, 'only_24_7': only_24_7, 'count': con.execute(query, (kind,)).fetchone()[0]}


def find_places(kind, street=None, brand=None, limit=5):
    query = 'SELECT id,name,street,housenumber,opening_hours,brand FROM poi WHERE kind=?'
    params = [kind]
    if street:
        query += ' AND street LIKE ?'
        params.append(f'%{street}%')
    if brand:
        query += ' AND brand LIKE ?'
        params.append(f'%{brand}%')
    with sqlite3.connect(f'file:{db.DB.as_posix()}?mode=ro', uri=True) as con:
        con.row_factory = sqlite3.Row
        items = [dict(r) for r in con.execute(query + ' ORDER BY id LIMIT ?', (*params, limit))]
    return {'places': items, 'limit': limit, 'scope': 'sample, not total count'}


def nearest_place(lat, lon, kind):
    items = db.nearest(lat, lon, kind, 1)
    if not items:
        return {'found': False}
    best = items[0]
    return {'found': True, **best, 'distance_unit': 'm', 'distance_method': 'straight_line'}


def geocode(street, housenumber):
    result = db.address_point(street, housenumber)
    if result is None:
        return {'found': False}
    return {'found': True, **result}


def place_details(place_id):
    result = db.details(place_id)
    if result is None:
        raise ValueError(f'Место id={place_id} отсутствует в базе; возьмите id из поиска')
    return result


@dataclass(frozen=True)
class Tool:
    name: str
    args: type[Args]
    function: Callable
    short: str
    full: str

    def schema(self, variant):
        params = self.args.model_json_schema()
        # Strict OpenAI tool schemas require all fields, including nullable/defaulted fields.
        params['required'] = list(params['properties'])
        for prop in params['properties'].values():
            prop.pop('default', None)
        return {'type': 'function', 'function': {'name': self.name, 'strict': True,
                'description': getattr(self, variant), 'parameters': params}}

    def invoke(self, arguments):
        args = self.args.model_validate_json(arguments)
        values = args.model_dump(mode='json')
        return values, self.function(**values)


TOOLS = [
    Tool('count_places', CountArgs, count_places,
         'Считает места заданного вида в Ростове.',
         'Возвращает общее число мест заданного вида во всём Ростове, при only_24_7=true только круглосуточных. Используйте для «сколько» и «количество». Не используйте для списка, улицы, бренда или расстояния: для них есть find_places и nearest_place. Единицы: count — штуки, не метры. Пример: аптеки 24/7 -> kind=pharmacy, only_24_7=true.'),
    Tool('find_places', FindArgs, find_places,
         'Возвращает список мест заданного вида в Ростове.',
         'Возвращает ограниченный список мест: id, название, адрес, часы и бренд. Используйте для «покажи», «перечисли», фильтров улицы или сети. Не используйте для общего числа или ближайшего места: count_places считает, nearest_place ищет от координат. limit — число строк 1–10, не общее количество. street — часть названия в именительном падеже, например Пушкин или Доватор; brand — Ozon и т.п.; отсутствующие фильтры — null. Пример: кафе на Пушкинской -> kind=cafe, street=Пушкин, brand=null, limit=5.'),
    Tool('nearest_place', NearestArgs, nearest_place,
         'Находит ближайшее место заданного вида к точке.',
         'Находит ровно одно ближайшее место заданного вида к lat/lon, возвращает id, название, координаты и distance_m. Используйте для «ближайший», «ближе всего» и расстояния от известной точки. Не используйте для списка по улице или геокодирования: find_places даёт список, geocode переводит адрес в координаты. lat/lon — градусы WGS84; расстояние — метры по прямой, не маршрут. Пример: ближайшая аптека к 47.2357,39.7015 -> lat=47.2357, lon=39.7015, kind=pharmacy.'),
    Tool('geocode', GeocodeArgs, geocode,
         'Находит координаты дома по адресу в Ростове.',
         'Переводит улицу и номер дома в координаты lat/lon из базы Ростова. Используйте первым, если дана улица и дом, но нет координат. Не используйте для ближайшего заведения или расписания: после него вызывайте nearest_place, затем place_details по найденному id. Координаты — градусы WGS84, не метры; номер дома — строка. Пример: Белорусская,44 -> street=Белорусская, housenumber=44. При found=false адрес не найден; не выдумывайте координаты.'),
    Tool('place_details', DetailsArgs, place_details,
         'Возвращает карточку места по id.',
         'Возвращает название, адрес, часы работы и телефон конкретного места по place_id. Используйте для расписания, контактов и карточки после поиска. Не используйте для поиска по адресу или расстояния: geocode даёт координаты, nearest_place даёт id ближайшего места. place_id — целое положительное число из результата поиска, не координата. Часы — строка OSM, телефон может отсутствовать. Пример: карточка места 1728 -> place_id=1728. Ошибка неизвестного id требует исправить id, а не придумать карточку.'),
]
REGISTRY = {tool.name: tool for tool in TOOLS}
