from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.auth.api.v1.views import auth
from src.category.api.v1.views import category
from src.database import engine
from src.health.views import health
from src.llm.client import llm_client
from src.operation.api.v1.views import operation
from src.redis_client import redis_client
from src.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    yield
    # При остановке закрываем пулы соединений. uvicorn запускает этот код, только
    # когда текущие запросы уже обработаны, поэтому соединения больше никому не нужны
    await redis_client.aclose()
    if llm_client is not None:
        await llm_client.aclose()
    await engine.dispose()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Без этого браузер не даст фронту прочитать Retry-After: по правилам CORS
    # из ответа доступны только несколько «безопасных» заголовков
    expose_headers=["Retry-After"],
)


app.include_router(health, tags=["health"])
app.include_router(category, prefix="/api/v1/categories", tags=["categories"])
app.include_router(auth, prefix="/api/v1/auth", tags=["auth"])
app.include_router(operation, prefix="/api/v1/operations", tags=["operations"])
