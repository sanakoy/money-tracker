"""POST /operations/parse: черновик записи из фразы.

Модель подменена фейком (фикстура fake_llm): тесты проверяют, что код делает
с её ответом, — сверку категорий, приведение суммы, даты и комментария, отказы.
Тест с меткой live ходит в настоящий GigaChat: uv run pytest -m live
"""

import re
from datetime import date, timedelta

import pytest

from src.llm.base import LLMError
from src.llm.client import get_llm_client
from src.llm.gigachat_client import create_gigachat_client
from src.main import app
from src.operation.phrase_parser import output_schema
from src.operation.phrase_prompt import SYSTEM_PROMPT, UNKNOWN_CATEGORY
from src.settings import settings
from tests.utils import auth_headers

URL = "/api/v1/operations/parse"
TODAY = date(2026, 10, 7)


async def parse(client, user, text="кофе 350", today=TODAY):
    body = {"text": text}
    if today is not None:
        body["today"] = today.isoformat()
    return await client.post(URL, json=body, headers=auth_headers(user))


def answer(category="Кафе", **fields):
    """Аргументы, которыми «модель» заполнила функцию."""
    return {"category": category, **fields}


async def test_parse_phrase(client, fake_llm, create_user, create_category):
    user = await create_user()
    cafe = await create_category(user, name="Кафе", icon="coffee")
    await create_category(user, name="Зарплата", is_profit=True, icon=None)
    other_user = await create_user()
    await create_category(other_user, name="Чужая категория")
    fake_llm.answers.append(
        {"category": "Кафе", "sum": 350, "day_offset": -1, "comment": "кофе с собой"}
    )

    response = await parse(client, user, "вчера кофе с собой 350")

    assert response.status_code == 200
    assert response.json() == {
        "category": {
            "id": cafe.id,
            "name": "Кафе",
            "cat_sum": None,
            "is_profit": False,
            "icon": "coffee",
            "user_id": user.id,
        },
        "sum": 350.0,
        "date": "2026-10-06",
        "comment": "кофе с собой",
    }
    [(messages, output)] = fake_llm.requests
    system, phrase = messages
    # Модель видит категории пользователя — и только его
    assert "Расходы: Кафе\nДоходы: Зарплата" in system.content
    assert "Чужая категория" not in system.content
    assert (phrase.role, phrase.content) == ("user", "вчера кофе с собой 350")
    assert output.schema["properties"]["category"]["enum"] == [
        "Кафе",
        "Зарплата",
        UNKNOWN_CATEGORY,
    ]


async def test_draft_is_not_saved(client, fake_llm, create_user, create_category):
    user = await create_user()
    await create_category(user, name="Кафе")
    fake_llm.answers.append(answer(sum=350))

    assert (await parse(client, user)).status_code == 200

    # Черновик только предлагается: запись появится, когда человек его сохранит
    response = await client.get("/api/v1/operations", headers=auth_headers(user))
    assert response.json() == {"data": []}


@pytest.mark.parametrize(
    "category",
    [UNKNOWN_CATEGORY, "Чужая категория", "Такси"],
    ids=["неизвестно", "категория другого пользователя", "выдуманная"],
)
async def test_unrecognized_category(
    client, fake_llm, create_user, create_category, category
):
    user = await create_user()
    await create_category(user, name="Кафе")
    other_user = await create_user()
    await create_category(other_user, name="Чужая категория")
    fake_llm.answers += [answer(category, sum=350), answer("Кафе", sum=350)]

    response = await parse(client, user)

    assert response.status_code == 422
    assert "Не получилось понять запись" in response.json()["detail"]
    # Контроль: когда модель называет категорию пользователя, тот же запрос проходит
    assert (await parse(client, user)).status_code == 200


async def test_category_is_matched_ignoring_case(
    client, fake_llm, create_user, create_category
):
    user = await create_user()
    cafe = await create_category(user, name="Кафе")
    fake_llm.answers.append(answer("  кафе ", sum=350))

    response = await parse(client, user)

    assert response.json()["category"]["id"] == cafe.id


async def test_same_name_in_both_kinds(client, fake_llm, create_user, create_category):
    user = await create_user()
    await create_category(user, name="Подарки")
    gifts_income = await create_category(user, name="Подарки", is_profit=True)
    fake_llm.answers.append(answer("Подарки (доход)", sum=1000))

    response = await parse(client, user, "подарили 1000")

    assert response.json()["category"]["id"] == gifts_income.id
    # У одноимённых категорий в списке для модели есть тип: иначе их не различить
    [(messages, output)] = fake_llm.requests
    assert "Расходы: Подарки (расход)\nДоходы: Подарки (доход)" in messages[0].content
    assert output.schema["properties"]["category"]["enum"][:2] == [
        "Подарки (расход)",
        "Подарки (доход)",
    ]


@pytest.mark.parametrize(
    "fields, draft_sum",
    [
        ({"sum": 1250.5}, 1250.5),
        ({}, None),
        ({"sum": 0}, None),
        ({"sum": -300}, None),
        ({"sum": 99.999}, 100.0),
    ],
    ids=["дробная", "суммы нет", "ноль", "минус", "округление до копеек"],
)
async def test_draft_sum(
    client, fake_llm, create_user, create_category, fields, draft_sum
):
    user = await create_user()
    await create_category(user, name="Кафе")
    fake_llm.answers.append(answer(**fields))

    response = await parse(client, user)

    assert response.json()["sum"] == draft_sum


@pytest.mark.parametrize(
    "fields, draft_date",
    [
        ({}, "2026-10-07"),
        ({"day_offset": -2}, "2026-10-05"),
        ({"day_offset": 1}, "2026-10-08"),
        ({"day_offset": 400}, "2026-10-07"),
        ({"day": 27}, "2026-09-27"),
        ({"day": 27, "future": True}, "2026-10-27"),
        ({"weekday": 5, "future": True}, "2026-10-09"),
    ],
    ids=[
        "даты нет",
        "позавчера",
        "завтра",
        "больше года назад",
        "27 числа",
        "27 числа, будущее время",
        "в пятницу, будущее время",
    ],
)
async def test_draft_date(
    client, fake_llm, create_user, create_category, fields, draft_date
):
    # Календарь подробно проверяет test_phrase_dates.py. Здесь — что ручка
    # передаёт поля модели в расчёт и считает от даты пользователя
    user = await create_user()
    await create_category(user, name="Кафе")
    fake_llm.answers.append(answer(sum=350, **fields))

    response = await parse(client, user)

    assert response.json()["date"] == draft_date


async def test_draft_date_without_today_uses_server_date(
    client, fake_llm, create_user, create_category
):
    user = await create_user()
    await create_category(user, name="Кафе")
    fake_llm.answers.append(answer(sum=350, day_offset=-1))

    before = date.today()
    response = await parse(client, user, today=None)
    after = date.today()

    # Запрос мог пройти ровно в полночь: тогда годится и вчера от новой даты
    yesterday = {(day - timedelta(days=1)).isoformat() for day in (before, after)}
    assert response.json()["date"] in yesterday


@pytest.mark.parametrize(
    "fields, comment",
    [
        ({"comment": "  продукты  в пятёрочке "}, "продукты в пятёрочке"),
        ({"comment": "   "}, None),
        ({}, None),
        ({"comment": "я" * 150}, "я" * 100),
    ],
    ids=["лишние пробелы", "из пробелов", "без комментария", "длиннее колонки"],
)
async def test_draft_comment(
    client, fake_llm, create_user, create_category, fields, comment
):
    user = await create_user()
    await create_category(user, name="Кафе")
    fake_llm.answers.append(answer(sum=350, **fields))

    response = await parse(client, user)

    assert response.json()["comment"] == comment


def test_prompt_describes_every_schema_field():
    # Поля описаны и в промпте, и в схеме функции. Переименование в одном месте
    # модель получила бы как противоречие: промпт просит одно поле, схема — другое
    for name in output_schema(["Кафе"]).schema["properties"]:
        # \b: «day» не должен засчитаться за счёт «day_offset»
        assert re.search(rf"\b{name}\b", SYSTEM_PROMPT), name


async def test_no_categories(client, fake_llm, create_user, create_category):
    user = await create_user()
    fake_llm.answers.append(answer(sum=350))

    response = await parse(client, user)

    assert response.status_code == 422
    assert "Сначала заведите категорию" in response.json()["detail"]
    # Модель не спрашивали: фразу всё равно не к чему отнести
    assert fake_llm.requests == []
    # Контроль: с категорией тот же запрос проходит
    await create_category(user, name="Кафе")
    assert (await parse(client, user)).status_code == 200


@pytest.mark.parametrize(
    "failure",
    [
        LLMError("GigaChat ответил 500"),
        {"category": "Кафе", "day_offset": "вчера"},
        {"sum": 350, "day_offset": 0},
    ],
    ids=["модель недоступна", "аргумент не того типа", "нет категории"],
)
async def test_model_failure_is_503(
    client, fake_llm, create_user, create_category, failure
):
    user = await create_user()
    await create_category(user, name="Кафе")
    fake_llm.answers += [failure, answer(sum=350)]

    response = await parse(client, user)

    assert response.status_code == 503
    assert "Попробуйте ещё раз" in response.json()["detail"]
    # Контроль: когда модель отвечает как надо, тот же запрос проходит
    assert (await parse(client, user)).status_code == 200


async def test_llm_not_configured_is_503(
    client, fake_llm, monkeypatch, create_user, create_category
):
    # Настоящая зависимость вместо подмены: она и решает, настроена ли модель
    app.dependency_overrides.pop(get_llm_client)
    monkeypatch.setattr("src.llm.client.llm_client", None)
    user = await create_user()
    await create_category(user, name="Кафе")

    response = await parse(client, user)

    assert response.status_code == 503
    assert response.json()["detail"] == "Функции на основе LLM не настроены"
    # Контроль: когда клиент модели есть, тот же запрос проходит
    monkeypatch.setattr("src.llm.client.llm_client", fake_llm)
    fake_llm.answers.append(answer(sum=350))
    assert (await parse(client, user)).status_code == 200


@pytest.mark.parametrize(
    "text", ["", "   ", "я" * 201], ids=["пустая", "из пробелов", "длиннее 200"]
)
async def test_invalid_text(client, fake_llm, create_user, create_category, text):
    user = await create_user()
    await create_category(user, name="Кафе")

    response = await parse(client, user, text)

    assert response.status_code == 422
    assert fake_llm.requests == []
    # Контроль: обычная фраза проходит
    fake_llm.answers.append(answer(sum=350))
    assert (await parse(client, user, "кофе 350")).status_code == 200


@pytest.mark.live
@pytest.mark.skipif(
    not settings.GIGACHAT_CREDENTIALS, reason="не задан GIGACHAT_CREDENTIALS"
)
async def test_live_parse_phrase(client, create_user, create_category):
    user = await create_user()
    await create_category(user, name="Кафе")
    await create_category(user, name="Транспорт")
    await create_category(user, name="Зарплата", is_profit=True)
    # Свой клиент, а не общий из приложения: его нужно закрыть после теста
    llm = create_gigachat_client(settings)
    app.dependency_overrides[get_llm_client] = lambda: llm
    try:
        response = await parse(client, user, "вчера такси 780")
    finally:
        app.dependency_overrides.pop(get_llm_client, None)
        if llm is not None:
            await llm.aclose()

    assert response.status_code == 200
    draft = response.json()
    assert (draft["category"]["name"], draft["sum"], draft["date"]) == (
        "Транспорт",
        780,
        "2026-10-06",
    )
