"""Прогон набора фраз через разбор «записи словами».

Фразы и правильные ответы лежат в phrases.csv рядом. Каждая фраза идёт в
настоящий GigaChat через тот же код, что и ручка POST /operations/parse (промпт,
схема, проверки ответа), а итог сравнивается с ожидаемым: категория, сумма, дата.
Комментарий — свободный текст, его не сравниваем: он есть в CSV с результатами.

Запуск из корня репозитория. Тратит токены, поэтому это не тест pytest:
    uv run python -m evals.phrase_parsing.run
    uv run python -m evals.phrase_parsing.run --model GigaChat-2 --repeat 3
"""

import argparse
import asyncio
import csv
import io
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from src.category.models import Category
from src.llm.base import Completion, JsonCompletion, LLMClient, Message, OutputSchema
from src.llm.gigachat_client import create_gigachat_client
from src.operation.phrase_parser import draft_from_phrase
from src.operation.phrase_prompt import UNKNOWN_CATEGORY
from src.operation.schemas import OperationDraft, ParsePhraseRequest
from src.settings import settings

HERE = Path(__file__).parent
PHRASES_FILE = HERE / "phrases.csv"
RESULTS_DIR = HERE / "results"

# «Сегодня» для набора: от него посчитаны ожидаемые даты в phrases.csv
TODAY = date(2026, 10, 12)
# Категории воображаемого пользователя. Тот же список — в шапке phrases.csv
SPENDING = [
    "Кафе",
    "Продукты",
    "Транспорт",
    "Коммуналка",
    "Подарки",
    "Развлечения",
    "Здоровье",
    "Одежда",
    "Связь",
]
PROFIT = ["Зарплата", "Фриланс"]

COLUMNS = ["phrase", "category", "sum", "date", "kind"]
FIELDS = {"category": "категория", "sum": "сумма", "date": "дата"}
DATE_FORMAT = "%d.%m.%Y"


@dataclass(frozen=True)
class Case:
    """Строка набора: фраза и правильный ответ."""

    line: int
    phrase: str
    # Правильные категории: обычно одна, у спорных фраз несколько («Продукты|Кафе»)
    categories: tuple[str, ...]
    # Сумма и дата не проверяются, если правильный ответ только «неизвестно»
    sum: float | None
    date: date | None
    kind: str


@dataclass(frozen=True)
class Outcome:
    """Что получилось на одном прогоне фразы."""

    case: Case
    attempt: int
    draft: OperationDraft | None
    # Ручка отказала с 422: модель не поняла категорию
    refused: bool
    # Сбой (ручка ответила бы 503): модель не ответила или ответила не по схеме
    error: str | None
    # Ответ модели до проверок кода: по нему видно, кто ошибся — модель или код
    raw: dict[str, Any] | None
    tokens: int | None
    seconds: float

    @property
    def category(self) -> str | None:
        if self.draft is not None:
            return self.draft.category.name
        return UNKNOWN_CATEGORY if self.refused else None

    @property
    def checks(self) -> dict[str, bool]:
        """Поле → верно ли. Непроверяемых полей в словаре нет."""
        checks = {"category": self.category in self.case.categories}
        # Черновика и не должно быть: правильный ответ только «неизвестно» или
        # модель выбрала «неизвестно», и это один из допустимых ответов
        if self.case.categories == (UNKNOWN_CATEGORY,) or (
            self.refused and checks["category"]
        ):
            return checks
        draft = self.draft
        checks["sum"] = draft is not None and same_sum(draft.sum, self.case.sum)
        checks["date"] = draft is not None and draft.date == self.case.date
        return checks

    @property
    def ok(self) -> bool:
        return all(self.checks.values())


class RecordingLLM:
    """Настоящий клиент, который запоминает последний ответ модели: отчёту
    нужен её сырой ответ и расход токенов, а черновик их не содержит."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.last: JsonCompletion | None = None

    async def complete(
        self, messages: Sequence[Message], *, temperature: float | None = None
    ) -> Completion:
        return await self.llm.complete(messages, temperature=temperature)

    async def complete_json(
        self,
        messages: Sequence[Message],
        output: OutputSchema,
        *,
        temperature: float | None = None,
    ) -> JsonCompletion:
        self.last = await self.llm.complete_json(
            messages, output, temperature=temperature
        )
        return self.last


def same_sum(got: float | None, expected: float | None) -> bool:
    if got is None or expected is None:
        return got is expected
    return abs(got - expected) < 0.005


def load_cases(path: Path) -> list[Case]:
    """Читает набор и проверяет его до прогона: опечатка в категории или дате
    иначе стоила бы токенов и выглядела бы как ошибка модели."""
    # utf-8-sig: если файл сохранили с BOM, заголовок всё равно прочитается
    numbered = [
        (number, line)
        for number, line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), start=1
        )
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not numbered:
        sys.exit(f"{path}: нет строки заголовка {';'.join(COLUMNS)}")
    rows = csv.reader((line for _, line in numbered), delimiter=";")
    header = [cell.strip() for cell in next(rows)]
    if header != COLUMNS:
        sys.exit(
            f"{path}, строка {numbered[0][0]}: заголовок должен быть "
            f"{';'.join(COLUMNS)}, а не {';'.join(header)}"
        )

    cases: list[Case] = []
    problems: list[str] = []
    for (number, _), row in zip(numbered[1:], rows, strict=True):
        try:
            cases.append(parse_case(number, row))
        except ValueError as error:
            problems.append(f"  строка {number}: {error}")
    if problems:
        sys.exit(f"В {path} есть ошибки, прогон не начат:\n" + "\n".join(problems))
    if not cases:
        sys.exit(f"В {path} пока нет ни одной фразы")
    return cases


def parse_case(line: int, row: list[str]) -> Case:
    if len(row) != len(COLUMNS):
        raise ValueError(f"нужно {len(COLUMNS)} колонок через «;», а здесь {len(row)}")
    phrase, categories_text, sum_text, date_text, kind = (cell.strip() for cell in row)
    try:
        # Те же ограничения, что у ручки: фраза, которую ручка не примет, — ошибка набора
        phrase = ParsePhraseRequest(text=phrase).text
    except ValidationError:
        raise ValueError("фраза пустая или длиннее 200 символов") from None

    known = {name.casefold(): name for name in [*SPENDING, *PROFIT, UNKNOWN_CATEGORY]}
    categories: list[str] = []
    for name in (part.strip() for part in categories_text.split("|")):
        if name.casefold() not in known:
            raise ValueError(
                f"категории «{name}» нет в списке (SPENDING и PROFIT в run.py)"
            )
        if known[name.casefold()] not in categories:
            categories.append(known[name.casefold()])
    if categories == [UNKNOWN_CATEGORY]:
        return Case(line, phrase, (UNKNOWN_CATEGORY,), None, None, kind or "без вида")

    expected_sum = None
    if sum_text:
        try:
            expected_sum = float(sum_text.replace(" ", "").replace(",", "."))
        except ValueError:
            raise ValueError(f"сумма «{sum_text}» — не число") from None
        if expected_sum <= 0:
            raise ValueError(f"сумма «{sum_text}» должна быть больше нуля")
    try:
        expected_date = datetime.strptime(date_text, DATE_FORMAT).date()
    except ValueError:
        raise ValueError(f"дата «{date_text}» не в формате ДД.ММ.ГГГГ") from None
    return Case(
        line,
        phrase,
        tuple(categories),
        expected_sum,
        expected_date,
        kind or "без вида",
    )


def make_categories() -> list[Category]:
    # Как в ручке: порядок (is_profit, name), иначе промпт отличался бы от боевого
    pairs = sorted(
        [(False, name) for name in SPENDING] + [(True, name) for name in PROFIT]
    )
    return [
        Category(id=number, name=name, is_profit=is_profit, user_id=0)
        for number, (is_profit, name) in enumerate(pairs, start=1)
    ]


async def run_case(
    llm: RecordingLLM, categories: list[Category], case: Case, attempt: int
) -> Outcome:
    llm.last = None
    draft, refused, error = None, False, None
    started = time.perf_counter()
    try:
        draft = await draft_from_phrase(llm, categories, case.phrase, TODAY)
    except HTTPException as exc:
        if exc.status_code == 422:
            refused = True
        else:
            cause = exc.__cause__
            error = " ".join(str(cause or exc.detail).split())[:300]
    seconds = time.perf_counter() - started
    return Outcome(
        case=case,
        attempt=attempt,
        draft=draft,
        refused=refused,
        error=error,
        raw=llm.last.data if llm.last else None,
        tokens=llm.last.total_tokens if llm.last else None,
        seconds=seconds,
    )


def describe_expected(case: Case) -> str:
    categories = " или ".join(case.categories)
    if case.categories == (UNKNOWN_CATEGORY,):
        return categories
    return describe(categories, case.sum, case.date)


def describe_got(outcome: Outcome) -> str:
    if outcome.error is not None:
        return f"сбой: {outcome.error}"
    if outcome.draft is None:
        return f"{UNKNOWN_CATEGORY} (отказ)"
    draft = outcome.draft
    return describe(draft.category.name, draft.sum, draft.date)


def describe(category: str, amount: float | None, day: date | None) -> str:
    # 85000.0 → «85000»: дробная часть, только если она есть
    amount_text = (
        "без суммы" if amount is None else f"{amount:.2f}".rstrip("0").rstrip(".")
    )
    day_text = day.strftime(DATE_FORMAT) if day else "без даты"
    return f"{category}, {amount_text}, {day_text}"


def model_answer(raw: dict[str, Any] | None) -> str:
    # sort_keys: модель отдаёт поля в разном порядке, а одинаковые ответы
    # должны совпадать и в тексте, иначе отчёт не сложит их в одну строку
    return json.dumps(raw, ensure_ascii=False, sort_keys=True) if raw else ""


def share(count: int, total: int) -> str:
    if total == 0:
        return "—"
    return f"{count} из {total} ({round(100 * count / total)} %)"


def report(outcomes: list[Outcome], model: str, repeat: int) -> None:
    phrases = len({outcome.case.line for outcome in outcomes})
    print(f"\nМодель {model}: фраз {phrases}, ответов {len(outcomes)}")
    print(f"Полностью верно: {share(sum(o.ok for o in outcomes), len(outcomes))}")
    for field, title in FIELDS.items():
        checked = [o.checks[field] for o in outcomes if field in o.checks]
        print(f"  {title + ':':<10} {share(sum(checked), len(checked))}")
    failures = sum(outcome.error is not None for outcome in outcomes)
    if failures:
        print(f"Сбоев (ручка ответила бы 503): {failures}")

    print("\nПо видам фраз:")
    by_kind: dict[str, list[bool]] = defaultdict(list)
    for outcome in outcomes:
        by_kind[outcome.case.kind].append(outcome.ok)
    width = max(len(kind) for kind in by_kind)
    for kind, results in sorted(by_kind.items()):
        print(f"  {kind:<{width}}  {share(sum(results), len(results))}")

    by_case: dict[int, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        by_case[outcome.case.line].append(outcome)
    wrong = [runs for runs in by_case.values() if not all(o.ok for o in runs)]
    if wrong:
        print("\nОшибки:")
    for runs in wrong:
        case = runs[0].case
        failed = [outcome for outcome in runs if not outcome.ok]
        print(f"\n  строка {case.line}, «{case.phrase}» [{case.kind}]")
        if len(runs) > 1:
            print(f"    ошибок {len(failed)} из {len(runs)} прогонов")
        print(f"    ждали:    {describe_expected(case)}")
        # Одинаковые ошибки разных прогонов — одной строкой с числом повторов
        variants = Counter(
            (describe_got(outcome), model_answer(outcome.raw)) for outcome in failed
        )
        for (got, raw), count in variants.most_common():
            times = f" (раз: {count})" if count > 1 else ""
            print(f"    получили: {got}{times}")
            print(f"      модель: {raw or 'нет ответа'}")

    if repeat > 1:
        unstable = sum(len({o.ok for o in runs}) > 1 for runs in by_case.values())
        print(f"\nФраз, где прогоны дали разный итог: {unstable} из {len(by_case)}")
    tokens = [o.tokens for o in outcomes if o.tokens is not None]
    seconds = [outcome.seconds for outcome in outcomes]
    if tokens:
        print(
            f"\nТокенов: {sum(tokens)}, в среднем {round(sum(tokens) / len(tokens))} "
            "на ответ"
        )
    print(
        f"Время ответа: в среднем {sum(seconds) / len(seconds):.1f} с, "
        f"самое долгое {max(seconds):.1f} с"
    )


def save(outcomes: list[Outcome], model: str) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = RESULTS_DIR / f"{stamp}_{model}.csv"
    # utf-8-sig: Excel без BOM открыл бы кириллицу кракозябрами
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(
            [
                "attempt",
                "line",
                "kind",
                "phrase",
                "ok",
                "expected",
                "got",
                *FIELDS,
                "comment",
                "model_answer",
                "tokens",
                "seconds",
            ]
        )
        for outcome in outcomes:
            checks = outcome.checks
            writer.writerow(
                [
                    outcome.attempt,
                    outcome.case.line,
                    outcome.case.kind,
                    outcome.case.phrase,
                    yes_no(outcome.ok),
                    describe_expected(outcome.case),
                    describe_got(outcome),
                    *(
                        yes_no(checks[field]) if field in checks else ""
                        for field in FIELDS
                    ),
                    outcome.draft.comment if outcome.draft else "",
                    model_answer(outcome.raw),
                    outcome.tokens or "",
                    f"{outcome.seconds:.2f}",
                ]
            )
    return path


def yes_no(value: bool) -> str:
    return "да" if value else "нет"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Прогон набора фраз через разбор «записи словами»"
    )
    parser.add_argument(
        "--model",
        default=settings.GIGACHAT_MODEL,
        help="модель GigaChat, например GigaChat-2 (по умолчанию — GIGACHAT_MODEL из настроек)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="сколько раз прогнать каждую фразу: ответы модели между прогонами "
        "различаются (по умолчанию 1)",
    )
    parser.add_argument("--file", type=Path, default=PHRASES_FILE, help="набор фраз")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat должен быть не меньше 1")

    cases = load_cases(args.file)
    client = create_gigachat_client(
        settings.model_copy(update={"GIGACHAT_MODEL": args.model})
    )
    if client is None:
        sys.exit("Нет GIGACHAT_CREDENTIALS в .env: прогонять не на чем")

    llm = RecordingLLM(client)
    categories = make_categories()
    total = len(cases) * args.repeat
    print(f"Модель {args.model}: фраз {len(cases)}, прогонов {args.repeat}")
    outcomes: list[Outcome] = []
    try:
        for attempt in range(1, args.repeat + 1):
            for case in cases:
                outcome = await run_case(llm, categories, case, attempt)
                outcomes.append(outcome)
                mark = "верно " if outcome.ok else "ОШИБКА"
                print(f"  {len(outcomes):>4}/{total}  {mark}  {case.phrase}")
    finally:
        # И при Ctrl+C: уже потраченные токены не должны пропасть
        await client.aclose()
        if outcomes:
            # Сначала файл: если отчёт упадёт, результаты всё равно останутся
            path = save(outcomes, args.model)
            report(outcomes, args.model, args.repeat)
            print(f"\nРезультаты: {path}")


def configure_output(stream: Any) -> None:
    # В консоли Windows вывод в UTF-8, а в канал (| или >) — в cp1251, где нет,
    # например, «₽» из фразы: без replace прогон упал бы на печати
    if isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(errors="replace")


if __name__ == "__main__":
    configure_output(sys.stdout)
    asyncio.run(main())
