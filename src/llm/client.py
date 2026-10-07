from fastapi import HTTPException

from src.llm.base import LLMClient
from src.llm.gigachat_client import create_gigachat_client
from src.settings import settings

# Один клиент на всё приложение: внутри пул соединений и токен доступа, который
# живёт 30 минут, — получать новый на каждый запрос значило бы упереться в лимиты
# авторизации. Соединения ленивые: первое откроется при первом запросе к модели
llm_client = create_gigachat_client(settings)


async def get_llm_client() -> LLMClient:
    if llm_client is None:
        raise HTTPException(
            status_code=503, detail="Функции на основе LLM не настроены"
        )
    return llm_client
