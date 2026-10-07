"""Клиент LLM: перевод ответов и ошибок GigaChat, настройка и зависимость.

Обычные тесты подменяют SDK фейком и в сеть не ходят. Тесты с меткой live
обращаются к настоящему GigaChat и запускаются отдельно: uv run pytest -m live
"""

import base64

import httpx
import pytest
from fastapi import HTTPException
from gigachat.exceptions import AuthenticationError, RateLimitError, ServerError
from gigachat.models.chat_completions import (
    ChatCompletionResponse,
    ChatContentPart,
    ChatMessage,
    ChatUsage,
)
from pydantic import SecretStr, ValidationError

from src.llm.base import Completion, LLMError, Message
from src.llm.client import get_llm_client
from src.llm.gigachat_client import GigaChatClient, create_gigachat_client
from src.settings import Settings, settings

MODEL = "GigaChat-2"


class FakeChat:
    """Подмена sdk.achat: отдаёт заготовленный ответ или бросает ошибку."""

    def __init__(self, answer, error):
        self.answer = answer
        self.error = error
        self.requests = []

    async def create(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.answer


class FakeSDK:
    def __init__(self, answer=None, error=None):
        self.achat = FakeChat(answer, error)


def gigachat_answer(text: str) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        messages=[ChatMessage(role="assistant", content=[ChatContentPart(text=text)])],
        usage=ChatUsage(total_tokens=42),
    )


def answer_without_messages() -> ValidationError:
    # Так SDK встречает ответ 200 без поля messages: ответ не проходит его же проверку
    try:
        ChatCompletionResponse.model_validate({"usage": {"total_tokens": 80}})
    except ValidationError as error:
        return error
    raise AssertionError("SDK принял ответ без messages")


def settings_with(**changes) -> Settings:
    return settings.model_copy(update=changes)


async def test_complete_sends_dialog_and_returns_answer():
    sdk = FakeSDK(answer=gigachat_answer("Москва"))
    llm = GigaChatClient(sdk, model=MODEL)

    completion = await llm.complete(
        [Message("system", "Отвечай одним словом"), Message("user", "Столица России?")],
        temperature=0.1,
    )

    assert completion == Completion(text="Москва", total_tokens=42)
    [request] = sdk.achat.requests
    assert request.model == MODEL
    assert [(m.role, m.content[0].text) for m in request.messages] == [
        ("system", "Отвечай одним словом"),
        ("user", "Столица России?"),
    ]
    assert request.model_options.temperature == 0.1


@pytest.mark.parametrize(
    "error",
    [
        ServerError("https://test", 500, b'{"message": "internal"}', None),
        RateLimitError("https://test", 429, b'{"message": "too many requests"}', None),
        httpx.ConnectTimeout("timed out"),
        answer_without_messages(),
    ],
    ids=["ошибка сервера", "лимит запросов", "таймаут", "ответ без messages"],
)
async def test_provider_failure_is_llm_error(error):
    llm = GigaChatClient(FakeSDK(error=error), model=MODEL)

    with pytest.raises(LLMError) as raised:
        await llm.complete([Message("user", "кофе 350")])

    # Исходная ошибка не теряется: по ней видно, что именно случилось
    assert raised.value.__cause__ is error


async def test_answer_without_text_is_llm_error():
    llm = GigaChatClient(
        FakeSDK(answer=ChatCompletionResponse(messages=[])), model=MODEL
    )

    with pytest.raises(LLMError):
        await llm.complete([Message("user", "кофе 350")])


def test_no_key_turns_llm_off():
    assert create_gigachat_client(settings_with(GIGACHAT_CREDENTIALS=None)) is None
    # Пустой ключ — как в .env, скопированном из .env.example
    empty = settings_with(GIGACHAT_CREDENTIALS=SecretStr(""))
    assert create_gigachat_client(empty) is None


async def test_key_turns_llm_on():
    llm = create_gigachat_client(settings_with(GIGACHAT_CREDENTIALS=SecretStr("key")))

    assert isinstance(llm, GigaChatClient)
    await llm.aclose()


def test_missing_certificate_fails_at_startup(tmp_path):
    broken = settings_with(
        GIGACHAT_CREDENTIALS=SecretStr("key"),
        GIGACHAT_CA_BUNDLE_FILE=tmp_path / "missing.crt",
    )

    with pytest.raises(RuntimeError, match="сертификат"):
        create_gigachat_client(broken)


# Ключ авторизации — base64 от «Client ID:Client Secret»
VALID_KEY = base64.b64encode(b"client-id:client-secret").decode()


def test_valid_gigachat_key_is_accepted():
    key = Settings(GIGACHAT_CREDENTIALS=VALID_KEY).GIGACHAT_CREDENTIALS

    assert key is not None
    assert key.get_secret_value() == VALID_KEY


@pytest.mark.parametrize(
    "bad_key",
    [
        f"Basic {VALID_KEY}",
        f"<{VALID_KEY}>",
        "0193b6a4-5f2e-7c1d-9a8b-3e4f5a6b7c8d",
        base64.b64encode(b"client-secret-only").decode(),
    ],
    ids=[
        "с Basic",
        "в угловых скобках",
        "Client ID или Secret",
        "base64 без двоеточия",
    ],
)
def test_malformed_gigachat_key_fails_at_startup(bad_key):
    with pytest.raises(ValidationError, match="не ключ авторизации") as raised:
        Settings(GIGACHAT_CREDENTIALS=bad_key)

    # Сам ключ в текст ошибки не попадает: иначе он ушёл бы в консоль и логи
    assert bad_key not in str(raised.value)


async def test_get_llm_client_without_key_is_503(monkeypatch):
    monkeypatch.setattr("src.llm.client.llm_client", None)

    with pytest.raises(HTTPException) as raised:
        await get_llm_client()
    assert raised.value.status_code == 503

    # Контрольная проверка: когда клиент есть, зависимость его и отдаёт
    llm = GigaChatClient(FakeSDK(), model=MODEL)
    monkeypatch.setattr("src.llm.client.llm_client", llm)
    assert await get_llm_client() is llm


@pytest.mark.live
@pytest.mark.skipif(
    not settings.GIGACHAT_CREDENTIALS, reason="не задан GIGACHAT_CREDENTIALS"
)
async def test_live_gigachat_answers():
    llm = create_gigachat_client(settings)
    assert llm is not None
    try:
        completion = await llm.complete(
            [
                Message("system", "Ответь одним словом."),
                Message("user", "Столица России?"),
            ],
            temperature=0.1,
        )
    finally:
        await llm.aclose()

    assert "москва" in completion.text.lower()
    assert completion.total_tokens


@pytest.mark.live
async def test_live_wrong_key_is_llm_error():
    # Ключ правильного формата, но несуществующий: сервер авторизации отвечает 401.
    # Ключ не нужен, а проверяется вся цепочка до GigaChat: при сломанном TLS
    # (нет сертификата) причиной была бы ошибка соединения, а не AuthenticationError
    zeros = "00000000-0000-0000-0000-000000000000"
    wrong_key = base64.b64encode(f"{zeros}:{zeros}".encode()).decode()
    llm = create_gigachat_client(
        settings_with(GIGACHAT_CREDENTIALS=SecretStr(wrong_key))
    )
    assert llm is not None
    try:
        with pytest.raises(LLMError) as raised:
            await llm.complete([Message("user", "Привет")])
    finally:
        await llm.aclose()

    assert isinstance(raised.value.__cause__, AuthenticationError)
