"""Разбор фразы «кофе 350 вчера» в черновик записи.

Модель предлагает, код проверяет, человек подтверждает: модель заполняет
аргументы по схеме, код сверяет их со списком категорий пользователя и
приводит к допустимым значениям, а в БД запись попадает только после того,
как человек проверит черновик и сохранит его обычным /create.
"""

import logging
import math
from collections import Counter
from collections.abc import Sequence
from datetime import date

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.schemas import UserToken
from src.category.models import Category
from src.category.schemas import CategoryView
from src.database import get_session
from src.llm.base import LLMClient, LLMError, Message, OutputSchema
from src.llm.client import get_llm_client
from src.operation.phrase_dates import resolve_date
from src.operation.phrase_prompt import UNKNOWN_CATEGORY, build_system_prompt
from src.operation.schemas import COMMENT_MAX_LENGTH, OperationDraft, ParsePhraseRequest

logger = logging.getLogger(__name__)

# Подобрана в песочнице: ответы стабильные, но не одинаковые до символа.
# Ноль GigaChat не принимает
TEMPERATURE = 0.1


class PhraseArguments(BaseModel):
    """Аргументы, которые заполнила модель. Схема — подсказка, а не гарантия,
    поэтому ответ проверяется, как любой внешний ввод.

    Поля даты — то, что сказано во фразе, а не сама дата: её считает
    resolve_date. Значения вне календаря здесь не отбрасываются, иначе
    «day: 32» обернулся бы отказом всей фразы, а не датой «сегодня».
    """

    category: str
    sum: float | None = None
    day_offset: int | None = None
    day: int | None = None
    month: int | None = None
    weekday: int | None = None
    future: bool | None = None
    comment: str | None = None


class PhraseParser:
    def __init__(self, session: AsyncSession, llm: LLMClient):
        self.session = session
        self.llm = llm

    async def parse(
        self, request: ParsePhraseRequest, auth_user: UserToken
    ) -> OperationDraft:
        # Порядок постоянный: одинаковое начало промпта GigaChat берёт из кэша
        categories = (
            await self.session.scalars(
                select(Category)
                .filter(Category.user_id == auth_user.id)
                .order_by(Category.is_profit, Category.name)
            )
        ).all()
        return await draft_from_phrase(
            self.llm, categories, request.text, request.today or date.today()
        )


async def draft_from_phrase(
    llm: LLMClient, categories: Sequence[Category], text: str, today: date
) -> OperationDraft:
    """Черновик по фразе и категориям пользователя.

    Без БД: этим же кодом набор фраз прогоняет evals/phrase_parsing/run.py,
    поэтому проверка качества идёт по тому, что на самом деле отдаёт ручка.
    """
    if not categories:
        # Модель звать незачем: фразу всё равно не к чему отнести
        raise HTTPException(
            status_code=422,
            detail="Сначала заведите категорию: записи пока не к чему относиться.",
        )
    labels = category_labels(categories)

    messages = [
        Message("system", build_system_prompt(categories_text(labels))),
        Message("user", text),
    ]
    try:
        completion = await llm.complete_json(
            messages, output_schema(list(labels)), temperature=TEMPERATURE
        )
        arguments = PhraseArguments.model_validate(completion.data)
    except LLMError as error:
        # Саму фразу в лог не пишем: в ней личные траты пользователя
        logger.warning("Модель не ответила: %s", error)
        raise unavailable() from error
    except ValidationError as error:
        logger.warning("Модель ответила не по схеме")
        raise unavailable() from error

    answer = arguments.category.strip().casefold()
    category = {label.casefold(): item for label, item in labels.items()}.get(answer)
    # «неизвестно» проверяется отдельно: если у пользователя есть категория
    # с таким названием, ответ модели совпал бы с ней
    if answer == UNKNOWN_CATEGORY or category is None:
        raise HTTPException(
            status_code=422,
            # Неразрывный пробел: «кофе» и «350» не разъезжаются по строкам
            detail="Не получилось понять запись. Напишите иначе, например "
            "«кофе 350», или выберите категорию плиткой.",
        )

    return OperationDraft(
        category=CategoryView.model_validate(category),
        sum=draft_sum(arguments.sum),
        date=resolve_date(
            today,
            day_offset=arguments.day_offset,
            day=arguments.day,
            month=arguments.month,
            weekday=arguments.weekday,
            future=arguments.future is True,
        ),
        # split/join схлопывает пробелы: модель вырезает сумму из середины
        # фразы, и от неё остаётся двойной пробел
        comment=" ".join((arguments.comment or "").split())[:COMMENT_MAX_LENGTH]
        or None,
    )


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Не получилось разобрать фразу: сервис недоступен. Попробуйте ещё раз "
        "или выберите категорию плиткой.",
    )


def category_labels(categories: Sequence[Category]) -> dict[str, Category]:
    """Как категории называются для модели: метка → категория.

    «Подарки» могут быть и в расходах, и в доходах. Тогда у обеих в метке тип,
    иначе по ответу модели не понять, какая из двух имелась в виду.
    """
    counts = Counter(category.name.casefold() for category in categories)
    labels: dict[str, Category] = {}
    for category in categories:
        label = category.name
        if counts[category.name.casefold()] > 1:
            label += " (доход)" if category.is_profit else " (расход)"
        labels[label] = category
    return labels


def categories_text(labels: dict[str, Category]) -> str:
    lines = []
    for title, is_profit in (("Расходы", False), ("Доходы", True)):
        names = [label for label, item in labels.items() if item.is_profit == is_profit]
        if names:
            lines.append(f"{title}: {', '.join(names)}")
    return "\n".join(lines)


def output_schema(labels: list[str]) -> OutputSchema:
    return OutputSchema(
        name="add_operation",
        description="Записать трату или доход",
        schema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [*labels, UNKNOWN_CATEGORY],
                    "description": "Категория из списка; «неизвестно», если "
                    "категорию не понять или фраза не про деньги",
                },
                "sum": {
                    "type": "number",
                    "description": "Сумма в рублях; не заполняй, если суммы во фразе нет",
                },
                # Поля даты — то, что сказано во фразе. Саму дату считает код
                "day_offset": {
                    "type": "integer",
                    "description": "Смещение в днях от сегодня: «вчера» — -1, "
                    "«сегодня» — 0, «завтра» — 1, «3 дня назад» — -3. Если таких "
                    "слов во фразе нет — 0",
                },
                "day": {
                    "type": "integer",
                    "description": "Число месяца, только если оно названо во "
                    "фразе: «27 числа» — 27",
                },
                "month": {
                    "type": "integer",
                    "description": "Номер месяца, только если он назван во фразе: "
                    "«5 октября» — 10",
                },
                "weekday": {
                    "type": "integer",
                    "description": "День недели, только если он назван словом: "
                    "«в пятницу» — 5 (1 — понедельник, 7 — воскресенье). Для "
                    "«вчера», «сегодня», «завтра» не заполняй — это day_offset",
                },
                "future": {
                    "type": "boolean",
                    "description": "true, если глагол в будущем времени: «куплю», "
                    "«заплачу»; false, если в прошедшем или время не понять",
                },
                "comment": {"type": "string", "description": "Что это было, коротко"},
            },
            # day_offset обязательное. Необязательное обе модели пропускали
            # и вместо «вчера» писали weekday — день недели вчерашнего дня,
            # не зная сегодняшнего. Если во фразе названо число, модель ставит
            # смещение 0, а resolve_date всё равно смотрит сначала на число.
            # future обязательное по той же причине: необязательное Lite
            # пропускала на «куплю», и дата уходила в прошлое
            "required": ["category", "day_offset", "future"],
        },
    )


def draft_sum(value: float | None) -> float | None:
    # Ноль, минус и бесконечность — не сумма: пусть человек впишет её сам.
    # Ноль модель ставит, когда суммы во фразе нет, хотя просили не заполнять
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return round(value, 2)


async def get_phrase_parser(
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
) -> PhraseParser:
    return PhraseParser(session=session, llm=llm)
