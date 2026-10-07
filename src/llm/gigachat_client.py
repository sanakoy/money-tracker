import ssl
from collections.abc import Sequence

import httpx
from gigachat import GigaChatAsyncClient
from gigachat.exceptions import GigaChatException, ResponseError
from gigachat.models.chat_completions import (
    ChatCompletionRequest,
    ChatContentPart,
    ChatMessage,
    ChatModelOptions,
)
from pydantic import ValidationError

from src.llm.base import Completion, LLMError, Message
from src.settings import Settings


class GigaChatClient:
    """LLMClient поверх официального SDK GigaChat."""

    def __init__(self, sdk: GigaChatAsyncClient, model: str) -> None:
        self._sdk = sdk
        self._model = model

    async def complete(
        self, messages: Sequence[Message], *, temperature: float | None = None
    ) -> Completion:
        request = ChatCompletionRequest(
            model=self._model,
            messages=[
                ChatMessage(role=m.role, content=[ChatContentPart(text=m.content)])
                for m in messages
            ],
            model_options=(
                None
                if temperature is None
                else ChatModelOptions(temperature=temperature)
            ),
        )
        try:
            response = await self._sdk.achat.create(request)
        # httpx: сетевые ошибки и таймауты SDK пропускает как есть, не оборачивая.
        # ValidationError: сервер ответил 200, но не в том формате, который ждёт
        # SDK, — так бывает с ответом без поля messages
        except (GigaChatException, httpx.HTTPError, ValidationError) as error:
            raise LLMError(_describe(error)) from error

        if not response.messages:
            raise LLMError("GigaChat прислал ответ без сообщений")
        parts = response.messages[0].content or []
        usage = response.usage
        return Completion(
            text="".join(part.text or "" for part in parts),
            total_tokens=usage.total_tokens if usage else None,
        )

    async def aclose(self) -> None:
        await self._sdk.aclose()


def _describe(error: Exception) -> str:
    # str(ResponseError) содержит все заголовки ответа: берём только код и тело
    if isinstance(error, ResponseError):
        body = (error.content or b"").decode("utf-8", errors="replace")
        return f"GigaChat ответил {error.status_code}: {body}"
    return f"GigaChat недоступен: {error!r}"


def create_gigachat_client(settings: Settings) -> GigaChatClient | None:
    """Клиент по настройкам. Без ключа — None: функции на основе LLM выключены."""
    if not settings.GIGACHAT_CREDENTIALS:
        return None
    if not settings.GIGACHAT_CA_BUNDLE_FILE.is_file():
        # Проверяем сразу: иначе приложение запустится, а упадёт первый же
        # запрос к модели, и не с понятной ошибкой, а где-то в недрах httpx
        raise RuntimeError(
            f"Нет корневого сертификата для GigaChat: {settings.GIGACHAT_CA_BUNDLE_FILE}"
        )
    sdk = GigaChatAsyncClient(
        credentials=settings.GIGACHAT_CREDENTIALS.get_secret_value(),
        scope=settings.GIGACHAT_SCOPE,
        # Доверяем только корневому сертификату Минцифры. Готовый контекст, а не
        # путь: путь SDK передал бы в httpx строкой, а этот способ httpx 0.28
        # объявил устаревшим
        ssl_context=ssl.create_default_context(cafile=settings.GIGACHAT_CA_BUNDLE_FILE),
        timeout=settings.GIGACHAT_TIMEOUT,
        # Без повторов: на 429 SDK ждёт столько, сколько сервер указал в
        # Retry-After (бывает 30 с), а ответа ждёт человек. Пусть лучше
        # сразу увидит «попробуйте позже»
        max_retries=0,
    )
    return GigaChatClient(sdk, model=settings.GIGACHAT_MODEL)
