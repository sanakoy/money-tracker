"""Дата записи по тому, что модель выписала из фразы.

Модель не знает, какое сегодня число, и плохо считает календарь (длина месяцев,
переход через год), поэтому она только выписывает сказанное: «вчера» →
day_offset=-1, «27 числа» → day=27, «5 октября» → day=5, month=10, «в пятницу» →
weekday=5, и время глагола: «куплю» → future=True. Дату от сегодняшнего дня
пользователя считает этот код: его можно проверить тестами на любой календарь.
"""

from datetime import date, timedelta

# Дальше года в любую сторону фраза вроде «кофе 350» не уводит: такое
# смещение — ошибка модели
MAX_DAY_OFFSET = 366
# 29 февраля бывает раз в четыре года: дальше искать незачем
MAX_YEARS = 8


def resolve_date(
    today: date,
    *,
    day_offset: int | None = None,
    day: int | None = None,
    month: int | None = None,
    weekday: int | None = None,
    future: bool = False,
) -> date:
    """Дата записи. Без будущего времени — прошлое: так решено для фраз, где
    время не понять («27 числа бензин 2000»).

    Число и месяц важнее дня недели, а все они важнее смещения: модель иногда
    заполняет и то и другое. Значения вне календаря (day=32, month=13)
    отбрасываются, и если сказать нечего, дата — сегодня.
    """
    day = day if day is not None and 1 <= day <= 31 else None
    month = month if month is not None and 1 <= month <= 12 else None
    weekday = weekday if weekday is not None and 1 <= weekday <= 7 else None

    if month is not None:
        found = nearest_date(today, month, day, future)
    elif day is not None:
        found = nearest_day(today, day, future)
    elif weekday is not None:
        found = nearest_weekday(today, weekday, future)
    elif day_offset is not None and abs(day_offset) <= MAX_DAY_OFFSET:
        found = today + timedelta(days=day_offset)
    else:
        found = today
    return found or today


def nearest_date(today: date, month: int, day: int | None, future: bool) -> date | None:
    """«13 октября» или «в ноябре» (тогда первое число): тот же день в ближайшем
    году в нужную сторону. Сегодняшняя дата подходит: её назвали явно."""
    if day is None:
        if future and month == today.month:
            # «В октябре куплю» в октябре: первое число уже прошло, а до
            # следующего октября год. Ближе всего к сказанному — сегодня
            return today
        day = 1
    step = 1 if future else -1
    for years in range(MAX_YEARS):
        candidate = safe_date(today.year + step * years, month, day)
        if candidate is not None and (
            candidate >= today if future else candidate <= today
        ):
            return candidate
    return None


def nearest_day(today: date, day: int, future: bool) -> date | None:
    """«27 числа»: ближайшее 27-е в нужную сторону. Сегодняшнее число не
    считается, как и с днями недели: о сегодняшнем дне говорят «сегодня»."""
    step = 1 if future else -1
    year, month = today.year, today.month
    # 31-го нет в половине месяцев, но за год оно точно найдётся
    for _ in range(13):
        candidate = safe_date(year, month, day)
        if candidate is not None and (
            candidate > today if future else candidate < today
        ):
            return candidate
        year, month = shift_month(year, month, step)
    return None


def nearest_weekday(today: date, weekday: int, future: bool) -> date:
    """«В пятницу»: ближайшая пятница в нужную сторону. Если сегодня пятница —
    соседняя неделя: о сегодняшнем дне говорят «сегодня»."""
    if future:
        return today + timedelta(days=(weekday - today.isoweekday()) % 7 or 7)
    return today - timedelta(days=(today.isoweekday() - weekday) % 7 or 7)


def safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        # 31 сентября, 29 февраля в невисокосный год
        return None


def shift_month(year: int, month: int, step: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + step
    return index // 12, index % 12 + 1
