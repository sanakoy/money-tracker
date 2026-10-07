import json
import ssl
from collections.abc import Sequence

import httpx
from gigachat import GigaChatAsyncClient
from gigachat.exceptions import GigaChatException, ResponseError
from gigachat.models.chat_completions import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatContentPart,
    ChatFunctionCall,
    ChatFunctionSpecification,
    ChatFunctionsTool,
    ChatMessage,
    ChatModelOptions,
    ChatTool,
    ChatToolConfig,
)
from pydantic import ValidationError

from src.llm.base import Completion, JsonCompletion, LLMError, Message, OutputSchema
from src.settings import Settings


class GigaChatClient:
    """LLMClient поверх официального SDK GigaChat."""

    def __init__(self, sdk: GigaChatAsyncClient, model: str) -> None:
        self._sdk = sdk
        self._model = model

    async def complete(
        self, messages: Sequence[Message], *, temperature: float | None = None
    ) -> Completion:
        response = await self._send(
            ChatCompletionRequest(
                model=self._model,
                messages=_sdk_messages(messages),
                model_options=_options(temperature),
            )
        )
        parts = _first_message(response).content or []
        return Completion(
            text="".join(part.text or "" for part in parts),
            total_tokens=_total_tokens(response),
        )

    async def complete_json(
        self,
        messages: Sequence[Message],
        output: OutputSchema,
        *,
        temperature: float | None = None,
    ) -> JsonCompletion:
        # JSON по схеме получаем принудительным вызовом функции: модель заполняет
        # её аргументы, а они описаны схемой. Есть и прямой способ — response_format,
        # но SDK помечает его как бету, и на GigaChat-2 с нашим промптом он на
        # треть фраз возвращал пустой ответ. Вызов функции ответил на все
        response = await self._send(
            ChatCompletionRequest(
                model=self._model,
                messages=_sdk_messages(messages),
                model_options=_options(temperature),
                tools=[
                    ChatTool(
                        functions=ChatFunctionsTool(
                            specifications=[
                                ChatFunctionSpecification(
                                    name=output.name,
                                    description=output.description,
                                    parameters=dict(output.schema),
                                )
                            ]
                        )
                    )
                ],
                tool_config=ChatToolConfig(mode="forced", function_name=output.name),
            )
        )
        call = _function_call(_first_message(response))
        if call is None:
            raise LLMError("GigaChat ответил текстом вместо аргументов функции")
        arguments = call.arguments
        # Документация обещает аргументы JSON-строкой, SDK отдаёт уже разобранными
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise LLMError("GigaChat прислал аргументы не в JSON") from error
        if not isinstance(arguments, dict):
            raise LLMError("GigaChat прислал аргументы не объектом")
        return JsonCompletion(data=arguments, total_tokens=_total_tokens(response))

    async def aclose(self) -> None:
        await self._sdk.aclose()

    async def _send(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        try:
            return await self._sdk.achat.create(request)
        # httpx: сетевые ошибки и таймауты SDK пропускает как есть, не оборачивая.
        # ValidationError: сервер ответил 200, но не в том формате, который ждёт
        # SDK, — так бывает с ответом без поля messages
        except (GigaChatException, httpx.HTTPError, ValidationError) as error:
            raise LLMError(_describe(error)) from error


def _sdk_messages(messages: Sequence[Message]) -> list[ChatMessage]:
    return [
        ChatMessage(role=m.role, content=[ChatContentPart(text=m.content)])
        for m in messages
    ]


def _options(temperature: float | None) -> ChatModelOptions | None:
    return None if temperature is None else ChatModelOptions(temperature=temperature)


def _first_message(response: ChatCompletionResponse) -> ChatMessage:
    if not response.messages:
        raise LLMError("GigaChat прислал ответ без сообщений")
    return response.messages[0]


def _function_call(message: ChatMessage) -> ChatFunctionCall | None:
    # По схеме SDK вызов — поле сообщения, но GigaChat-2 кладёт его в часть
    # содержимого, а поле оставляет пустым. Смотрим в оба места
    if message.function_call is not None:
        return message.function_call
    return next(
        (part.function_call for part in message.content or [] if part.function_call),
        None,
    )


def _total_tokens(response: ChatCompletionResponse) -> int | None:
    return response.usage.total_tokens if response.usage else None


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
