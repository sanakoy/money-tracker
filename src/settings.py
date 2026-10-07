import base64
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"


class Settings(BaseSettings):
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str

    MODE: str = "DEV"
    SECRET_TOKEN_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    REFRESH_TOKEN_EXPIRE_DAYS: int
    # Secure-cookie браузер шлёт только по HTTPS (для localhost делает исключение).
    # False нужен, только если фронт открывают по http с другого хоста
    REFRESH_COOKIE_SECURE: bool = True

    SERVICE_URL: str

    # 127.0.0.1, а не localhost: на Windows localhost сначала пробует IPv6 (::1),
    # а Redis в docker-compose слушает только IPv4 — каждое подключение ждало бы ~2 с
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # Ограничение попыток входа (POST /auth/login)
    # По email: защита конкретного аккаунта от перебора пароля
    LOGIN_MAX_ATTEMPTS_PER_EMAIL: int = 5
    LOGIN_EMAIL_WINDOW_SECONDS: int = 15 * 60
    # По IP: защита от перебора одного пароля по многим аккаунтам (password spraying)
    LOGIN_MAX_ATTEMPTS_PER_IP: int = 20
    LOGIN_IP_WINDOW_SECONDS: int = 5 * 60

    # В .env.local задаётся JSON-списком: CORS_ORIGINS='["http://localhost:5173"]'
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # GigaChat — языковая модель для функций на основе LLM. Без ключа приложение
    # работает, а эти функции отвечают 503: ключ не нужен ни тестам, ни CI.
    # SecretStr не показывает значение в repr и сообщениях об ошибках
    GIGACHAT_CREDENTIALS: SecretStr | None = None
    # GIGACHAT_API_PERS — доступ для физлиц, в том числе бесплатный
    GIGACHAT_SCOPE: str = "GIGACHAT_API_PERS"
    # GigaChat-2 — это Lite: быстрее и в разы дешевле GigaChat-2-Pro
    GIGACHAT_MODEL: str = "GigaChat-2"
    # Корневой сертификат Минцифры, которым подписаны серверы GigaChat (certs/README.md)
    GIGACHAT_CA_BUNDLE_FILE: Path = (
        BASE_DIR / "certs" / "russian_trusted_root_ca_pem.crt"
    )
    # Ответа ждёт человек: дольше 10 секунд лучше показать ошибку
    GIGACHAT_TIMEOUT: float = 10

    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        # Иначе pydantic печатает в ошибке исходное значение, даже у SecretStr:
        # неверный ключ или пароль попал бы в консоль и логи
        hide_input_in_errors=True,
    )

    @field_validator("GIGACHAT_CREDENTIALS")
    @classmethod
    def check_gigachat_key(cls, key: SecretStr | None) -> SecretStr | None:
        # Ключ авторизации — это base64 от «Client ID:Client Secret». С другим
        # значением сервер отвечает невнятным 400 «Can't decode 'Authorization'
        # header» на первом же запросе к модели, поэтому формат проверяем при старте
        if not key:
            return key
        try:
            decoded = base64.b64decode(key.get_secret_value(), validate=True)
        except ValueError:  # binascii.Error — его подкласс
            decoded = b""
        if b":" not in decoded:
            raise ValueError(
                "это не ключ авторизации. Нужен Authorization Key из личного "
                "кабинета GigaChat: строка base64 без «Basic », кавычек и угловых "
                "скобок, а не Client ID и не Client Secret"
            )
        return key

    @property
    def get_db_url(self) -> URL:
        # URL.create, а не f-строка: он экранирует спецсимволы. Пароль с @, : или /
        # в f-строке ломал бы разбор адреса
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        )


settings = Settings()
