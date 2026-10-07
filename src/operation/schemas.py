from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from src.category.schemas import CategoryView
from src.schemas import BaseSchema, MonthFilter, reject_explicit_nulls

# Сумма всегда положительная: доход это или расход, решает тип категории
OperationSum = Annotated[float, Field(gt=0)]
# Длина совпадает с колонкой operation.comment
COMMENT_MAX_LENGTH = 100
OperationComment = Annotated[str, StringConstraints(max_length=COMMENT_MAX_LENGTH)]
# Фраза короткая, вроде «кофе 350 вчера». Лимит держит запрос к модели
# маленьким: каждый символ — это токены, а токены стоят денег
PhraseText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class CreateOperationRequest(BaseSchema):
    sum: OperationSum
    comment: OperationComment | None = None
    category_id: int
    date: datetime


class UpdateOperationRequest(BaseSchema):
    # Категорию операции менять нельзя: вместе с ней сменился бы и владелец операции.
    # extra="forbid": попытка прислать category_id вернёт 422, а не будет молча проигнорирована
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    sum: OperationSum | None = None
    comment: OperationComment | None = None
    date: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def check_nulls(cls, data: Any) -> Any:
        return reject_explicit_nulls(data, ("sum",))


# Длиннее года ни статистика, ни список записей не нужны, а запрос
# остаётся ограниченным
PERIOD_MAX_DAYS = 366


def check_period(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise ValueError("date_from позже date_to")
    if (date_to - date_from).days + 1 > PERIOD_MAX_DAYS:
        raise ValueError(f"период длиннее {PERIOD_MAX_DAYS} дней")


class OperationListParams(MonthFilter):
    category_id: int | None = None
    operation: Literal["profit", "spending"] | None = None
    # Произвольный период, включительно: для списка записей в статистике.
    # Либо он, либо year/month — два фильтра по дате сразу были бы двусмысленны
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def check_dates(self) -> "OperationListParams":
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError("date_from и date_to передаются только вместе")
        if self.date_from is not None and self.date_to is not None:
            if self.year is not None:
                raise ValueError("нужен либо year/month, либо date_from/date_to")
            check_period(self.date_from, self.date_to)
        return self


class PeriodParams(BaseSchema):
    """Период статистики: с date_from по date_to включительно."""

    date_from: date
    date_to: date

    @model_validator(mode="after")
    def check_period(self) -> "PeriodParams":
        check_period(self.date_from, self.date_to)
        return self


class CategoryTotal(BaseSchema):
    category_id: int
    name: str
    is_profit: bool
    sum: float


class DayCategoryTotal(BaseSchema):
    day: date
    category_id: int
    sum: float


class PeriodTotalsResponse(BaseSchema):
    income: float
    expense: float
    # Категории с операциями за период, от большей суммы к меньшей
    categories: list[CategoryTotal]
    # Суммы категорий по дням для графика: только дни с операциями, по порядку
    days: list[DayCategoryTotal]


class OperationView(BaseSchema):
    id: int
    sum: float
    comment: str | None = None
    date: datetime | None = None
    category_id: int
    cat_name: str
    icon: str | None = None
    is_profit: bool


class OperationsPage(BaseSchema):
    data: list[OperationView]


class ParsePhraseRequest(BaseSchema):
    text: PhraseText
    # Сегодняшняя дата у пользователя: в 00:30 по Москве на сервере в UTC ещё
    # вчера, и «вчера» из фразы уехало бы на день. Не передана — дата сервера
    today: date | None = None


class OperationDraft(BaseSchema):
    """Черновик записи из фразы. В БД он не попадает: человек проверит его
    в окне записи и сохранит обычным /create."""

    category: CategoryView
    # None — суммы во фразе не было, её впишет человек
    sum: float | None
    date: date
    comment: str | None
