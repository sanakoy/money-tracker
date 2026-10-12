"""Дата записи по полям, которые модель выписала из фразы (resolve_date).

Модель здесь не участвует: проверяется календарь — в какую сторону искать,
какой месяц и год подставить, что делать с днём, которого нет в месяце.
"""

from datetime import date

import pytest

from src.operation.phrase_dates import resolve_date

# Понедельник, как «сегодня» в наборе фраз evals/phrase_parsing
TODAY = date(2026, 10, 12)


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({}, date(2026, 10, 12)),
        ({"day_offset": -1}, date(2026, 10, 11)),
        ({"day_offset": 0}, date(2026, 10, 12)),
        ({"day_offset": 1}, date(2026, 10, 13)),
        ({"day_offset": -400}, date(2026, 10, 12)),
        ({"day_offset": 400}, date(2026, 10, 12)),
        ({"day_offset": -1, "future": True}, date(2026, 10, 11)),
    ],
    ids=[
        "даты нет",
        "вчера",
        "сегодня",
        "завтра",
        "больше года назад",
        "больше года вперёд",
        "время глагола не меняет смещение",
    ],
)
def test_day_offset(fields, expected):
    assert resolve_date(TODAY, **fields) == expected


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"day": 27}, date(2026, 9, 27)),
        ({"day": 27, "future": True}, date(2026, 10, 27)),
        ({"day": 5}, date(2026, 10, 5)),
        ({"day": 5, "future": True}, date(2026, 11, 5)),
        ({"day": 12}, date(2026, 9, 12)),
        ({"day": 12, "future": True}, date(2026, 11, 12)),
        ({"day": 31}, date(2026, 8, 31)),
    ],
    ids=[
        "27-е было в прошлом месяце",
        "27-е будет в этом месяце",
        "5-е было в этом месяце",
        "5-е будет в следующем месяце",
        "сегодняшнее число в прошлом — месяц назад",
        "сегодняшнее число в будущем — через месяц",
        "31 сентября нет — 31 августа",
    ],
)
def test_day_of_month(fields, expected):
    assert resolve_date(TODAY, **fields) == expected


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"day": 13, "month": 10}, date(2025, 10, 13)),
        ({"day": 13, "month": 10, "future": True}, date(2026, 10, 13)),
        ({"day": 5, "month": 10}, date(2026, 10, 5)),
        ({"day": 12, "month": 10}, date(2026, 10, 12)),
        ({"day": 12, "month": 10, "future": True}, date(2026, 10, 12)),
        ({"month": 11}, date(2025, 11, 1)),
        ({"month": 11, "future": True}, date(2026, 11, 1)),
        ({"month": 10}, date(2026, 10, 1)),
        ({"month": 10, "future": True}, date(2026, 10, 12)),
        ({"day": 29, "month": 2}, date(2024, 2, 29)),
        ({"day": 29, "month": 2, "future": True}, date(2028, 2, 29)),
        ({"day": 30, "month": 2}, date(2026, 10, 12)),
    ],
    ids=[
        "13 октября было в прошлом году",
        "13 октября будет в этом году",
        "5 октября было в этом году",
        "сегодняшняя дата в прошлом — сегодня",
        "сегодняшняя дата в будущем — сегодня",
        "в ноябре было в прошлом году",
        "в ноябре будет в этом году",
        "в этом месяце было — первое число",
        "в этом месяце будет — сегодня",
        "29 февраля — в прошлый високосный год",
        "29 февраля — в следующий високосный год",
        "30 февраля не бывает — сегодня",
    ],
)
def test_day_and_month(fields, expected):
    assert resolve_date(TODAY, **fields) == expected


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"weekday": 7}, date(2026, 10, 11)),
        ({"weekday": 7, "future": True}, date(2026, 10, 18)),
        ({"weekday": 5}, date(2026, 10, 9)),
        ({"weekday": 1}, date(2026, 10, 5)),
        ({"weekday": 1, "future": True}, date(2026, 10, 19)),
    ],
    ids=[
        "в воскресенье было вчера",
        "в воскресенье будет через 6 дней",
        "в пятницу было",
        "сегодняшний день недели в прошлом — неделю назад",
        "сегодняшний день недели в будущем — через неделю",
    ],
)
def test_weekday(fields, expected):
    assert resolve_date(TODAY, **fields) == expected


@pytest.mark.parametrize(
    "today, fields, expected",
    [
        (date(2026, 1, 5), {"day": 27}, date(2025, 12, 27)),
        (date(2026, 1, 5), {"month": 12}, date(2025, 12, 1)),
        (date(2026, 12, 20), {"day": 5, "future": True}, date(2027, 1, 5)),
        (date(2026, 12, 20), {"month": 1, "future": True}, date(2027, 1, 1)),
        (date(2026, 3, 31), {"day": 31}, date(2026, 1, 31)),
        (date(2026, 1, 31), {"day": 30, "future": True}, date(2026, 3, 30)),
    ],
    ids=[
        "27-е — в декабре прошлого года",
        "в декабре — прошлого года",
        "5-е — в январе следующего года",
        "в январе — следующего года",
        "31-го нет в феврале — 31 января",
        "30-го нет в феврале — 30 марта",
    ],
)
def test_month_and_year_boundaries(today, fields, expected):
    assert resolve_date(today, **fields) == expected


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"day": 27, "day_offset": -1}, date(2026, 9, 27)),
        ({"day": 5, "month": 10, "weekday": 7}, date(2026, 10, 5)),
        ({"weekday": 7, "day_offset": 0}, date(2026, 10, 11)),
    ],
    ids=[
        "число важнее смещения",
        "дата важнее дня недели",
        "день недели важнее смещения",
    ],
)
def test_explicit_date_wins(fields, expected):
    # Модель иногда заполняет и смещение, и число: сказанное явно надёжнее
    assert resolve_date(TODAY, **fields) == expected


@pytest.mark.parametrize(
    "fields",
    [{"day": 0}, {"day": 32}, {"month": 13}, {"month": 0}, {"weekday": 8}],
    ids=["день 0", "день 32", "месяц 13", "месяц 0", "день недели 8"],
)
def test_impossible_values_are_ignored(fields):
    # Такие числа модель выдумала: сказать о дате нечего, значит, сегодня
    assert resolve_date(TODAY, **fields) == TODAY
    # Контроль: рядом со смещением выдуманное поле его не перебивает
    assert resolve_date(TODAY, day_offset=-1, **fields) == date(2026, 10, 11)
