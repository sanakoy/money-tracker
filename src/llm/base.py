"""Что приложение знает о языковой модели — без привязки к провайдеру.

Код фич зависит только от LLMClient: провайдера можно заменить (YandexGPT вместо
GigaChat) или подставить в тестах фейк, не трогая сами фичи.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    # Сколько токенов списал провайдер — для учёта расходов. None, если не сообщил
    total_tokens: int | None = None


class LLMError(Exception):
    """Модель не дала ответа: сеть, таймаут, лимит запросов, ошибка провайдера
    или ответ не в том формате.

    Для пользователя всё это значит одно — «попробуйте позже», поэтому тип один.
    Исходная ошибка сохраняется в __cause__.
    """


class LLMClient(Protocol):
    async def complete(
        self, messages: Sequence[Message], *, temperature: float | None = None
    ) -> Completion:
        """Ответ модели на диалог. Если ответа нет, бросает LLMError."""
        ...
