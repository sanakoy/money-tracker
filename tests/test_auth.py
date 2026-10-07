from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from src.auth.jwt_utils import create_access_token, jwt_decode, jwt_encode
from src.auth.models import RefreshToken, User
from src.auth.password_hashing import verify_password
from src.auth.refresh_tokens import hash_refresh_token
from src.settings import settings
from tests.utils import TEST_SESSION_MAKER, auth_headers

PASSWORD = "correct-horse-battery"

PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/categories/spending"),
    ("GET", "/api/v1/categories/profit"),
    ("POST", "/api/v1/categories/create"),
    ("PATCH", "/api/v1/categories/update/1"),
    ("DELETE", "/api/v1/categories/delete/1"),
    ("GET", "/api/v1/operations"),
    ("GET", "/api/v1/operations/totals?date_from=2026-09-01&date_to=2026-09-30"),
    ("POST", "/api/v1/operations/parse"),
    ("POST", "/api/v1/operations/create"),
    ("PATCH", "/api/v1/operations/update/1"),
    ("DELETE", "/api/v1/operations/delete/1"),
]


async def login(client, email: str, password: str = PASSWORD):
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def post_with_refresh(client, url: str, refresh_token: str):
    # Отправляем конкретный токен, а не тот, что httpx запомнил после прошлых ответов
    client.cookies.clear()
    return await client.post(url, headers={"Cookie": f"refresh_token={refresh_token}"})


async def get_refresh_records(user_id: int) -> list[RefreshToken]:
    async with TEST_SESSION_MAKER() as session:
        result = await session.execute(
            select(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .order_by(RefreshToken.id)
        )
        return list(result.scalars())


# ---------- POST /register ----------


async def test_register(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert body == {"id": body["id"], "email": "new@example.com"}
    async with TEST_SESSION_MAKER() as session:
        user = await session.get(User, body["id"])
    # В БД лежит хеш, а не пароль
    assert user.hashed_password != PASSWORD
    assert verify_password(PASSWORD, user.hashed_password)


async def test_register_normalizes_email(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "New.User@Example.COM", "password": PASSWORD},
    )

    assert response.json()["email"] == "new.user@example.com"


@pytest.mark.parametrize("email", ["taken@example.com", "TAKEN@example.com"])
async def test_register_duplicate_email(client, create_user, email):
    await create_user(email="taken@example.com")

    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "json",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "user@example.com", "password": "short"},
        # 37 кириллических символов = 74 байта: больше лимита bcrypt
        {"email": "user@example.com", "password": "я" * 37},
        {"email": "user@example.com"},
        {"password": PASSWORD},
    ],
)
async def test_register_validation(client, json):
    response = await client.post("/api/v1/auth/register", json=json)

    assert response.status_code == 422


# ---------- POST /login ----------


async def test_login(client, create_user):
    user = await create_user(email="user@example.com", password=PASSWORD)

    response = await login(client, "user@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    payload = jwt_decode(body["access_token"])
    assert (payload.sub, payload.type) == (user.id, "access")
    # Refresh приходит только в cookie, в теле его нет
    assert "refresh_token" not in body
    refresh_token = response.cookies["refresh_token"]
    records = await get_refresh_records(user.id)
    assert len(records) == 1
    record = records[0]
    # В БД лежит хеш, а не сам токен
    assert record.token_hash == hash_refresh_token(refresh_token)
    assert record.token_hash != refresh_token
    assert record.revoked_at is None


async def test_login_sets_secure_cookie(client, create_user):
    await create_user(email="user@example.com", password=PASSWORD)

    response = await login(client, "user@example.com")

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api/v1/auth" in cookie
    assert f"max-age={settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60}" in cookie


async def test_login_email_is_case_insensitive(client, create_user):
    await create_user(email="user@example.com", password=PASSWORD)

    response = await login(client, "USER@Example.com")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "email, password",
    [("user@example.com", "wrong-password"), ("nobody@example.com", PASSWORD)],
)
async def test_login_wrong_credentials(client, create_user, email, password):
    await create_user(email="user@example.com", password=PASSWORD)

    response = await login(client, email, password)

    # Одинаковый ответ, чтобы нельзя было узнать, зарегистрирован ли email
    assert response.status_code == 401
    assert response.json() == {"detail": "Неверный email или пароль"}
    assert "set-cookie" not in response.headers


async def test_login_removes_expired_refresh_tokens(client, create_user):
    user = await create_user(email="user@example.com", password=PASSWORD)
    await login(client, "user@example.com")
    async with TEST_SESSION_MAKER() as session:
        await session.execute(
            update(RefreshToken).values(
                expires_at=datetime.now(UTC) - timedelta(days=1)
            )
        )
        await session.commit()

    await login(client, "user@example.com")

    records = await get_refresh_records(user.id)
    assert len(records) == 1
    assert records[0].expires_at > datetime.now(UTC)


# ---------- POST /refresh ----------


async def test_refresh_rotates_token(client, create_user):
    user = await create_user(email="user@example.com", password=PASSWORD)
    old_refresh = (await login(client, "user@example.com")).cookies["refresh_token"]

    response = await post_with_refresh(client, "/api/v1/auth/refresh", old_refresh)

    assert response.status_code == 200
    payload = jwt_decode(response.json()["access_token"])
    assert payload.sub == user.id
    new_refresh = response.cookies["refresh_token"]
    assert new_refresh != old_refresh
    records = await get_refresh_records(user.id)
    assert len(records) == 2
    old_record, new_record = records
    assert old_record.revoked_at is not None
    assert new_record.revoked_at is None
    # Новый токен продолжает ту же цепочку входа
    assert new_record.family_id == old_record.family_id


async def test_refresh_reuse_revokes_whole_family(client, create_user):
    user = await create_user(email="user@example.com", password=PASSWORD)
    stolen_refresh = (await login(client, "user@example.com")).cookies["refresh_token"]
    # Злоумышленник первым воспользовался украденным токеном и получил новый
    attacker_refresh = (
        await post_with_refresh(client, "/api/v1/auth/refresh", stolen_refresh)
    ).cookies["refresh_token"]

    # Настоящий пользователь приходит со старым, уже отозванным токеном
    response = await post_with_refresh(client, "/api/v1/auth/refresh", stolen_refresh)

    assert response.status_code == 401
    # Цепочка погашена: токен злоумышленника тоже больше не работает
    attacker_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", attacker_refresh
    )
    assert attacker_response.status_code == 401
    assert all(record.revoked_at for record in await get_refresh_records(user.id))


async def test_refresh_reuse_keeps_other_sessions(client, create_user):
    await create_user(email="user@example.com", password=PASSWORD)
    phone_refresh = (await login(client, "user@example.com")).cookies["refresh_token"]
    laptop_refresh = (await login(client, "user@example.com")).cookies["refresh_token"]
    new_phone_refresh = (
        await post_with_refresh(client, "/api/v1/auth/refresh", phone_refresh)
    ).cookies["refresh_token"]

    # Повторное использование токена телефона
    reuse_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", phone_refresh
    )

    assert reuse_response.status_code == 401
    # Цепочка телефона погашена целиком, включая токен, выданный после ротации
    phone_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", new_phone_refresh
    )
    assert phone_response.status_code == 401
    # А вход с ноутбука — другая цепочка, он продолжает работать
    laptop_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", laptop_refresh
    )
    assert laptop_response.status_code == 200


async def test_refresh_without_cookie(client, create_user):
    # В БД есть живой токен: отказ должен быть именно из-за отсутствия cookie
    user = await create_user(email="user@example.com", password=PASSWORD)
    refresh_token = (await login(client, "user@example.com")).cookies["refresh_token"]
    client.cookies.clear()

    response = await client.post("/api/v1/auth/refresh")

    assert response.status_code == 401
    records = await get_refresh_records(user.id)
    assert len(records) == 1
    assert records[0].revoked_at is None
    # Настоящий токен после этого продолжает работать
    valid_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", refresh_token
    )
    assert valid_response.status_code == 200


async def test_refresh_with_unknown_token(client, create_user):
    # В БД есть живой токен: отказ должен быть именно из-за чужой строки
    user = await create_user(email="user@example.com", password=PASSWORD)
    refresh_token = (await login(client, "user@example.com")).cookies["refresh_token"]

    response = await post_with_refresh(client, "/api/v1/auth/refresh", "made-up")

    assert response.status_code == 401
    # Выдуманный токен не запускает обнаружение кражи и ничего не отзывает
    records = await get_refresh_records(user.id)
    assert len(records) == 1
    assert records[0].revoked_at is None
    valid_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", refresh_token
    )
    assert valid_response.status_code == 200


async def test_refresh_with_expired_token(client, create_user):
    await create_user(email="user@example.com", password=PASSWORD)
    refresh_token = (await login(client, "user@example.com")).cookies["refresh_token"]
    async with TEST_SESSION_MAKER() as session:
        await session.execute(
            update(RefreshToken).values(
                expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await session.commit()

    response = await post_with_refresh(client, "/api/v1/auth/refresh", refresh_token)

    assert response.status_code == 401


# ---------- POST /logout ----------


async def test_logout(client, create_user):
    user = await create_user(email="user@example.com", password=PASSWORD)
    refresh_token = (await login(client, "user@example.com")).cookies["refresh_token"]

    response = await post_with_refresh(client, "/api/v1/auth/logout", refresh_token)

    assert response.status_code == 204
    # Браузеру велено удалить cookie
    cookie = response.headers["set-cookie"].lower()
    assert 'refresh_token=""' in cookie
    assert "max-age=0" in cookie
    records = await get_refresh_records(user.id)
    assert len(records) == 1
    record = records[0]
    assert record.revoked_at is not None
    refresh_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", refresh_token
    )
    assert refresh_response.status_code == 401


async def test_logout_without_cookie(client, create_user):
    # В БД есть живой токен: выход без cookie не должен его задеть
    user = await create_user(email="user@example.com", password=PASSWORD)
    refresh_token = (await login(client, "user@example.com")).cookies["refresh_token"]
    client.cookies.clear()

    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    records = await get_refresh_records(user.id)
    assert len(records) == 1
    assert records[0].revoked_at is None
    valid_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", refresh_token
    )
    assert valid_response.status_code == 200


async def test_logout_keeps_other_sessions(client, create_user):
    await create_user(email="user@example.com", password=PASSWORD)
    phone_refresh = (await login(client, "user@example.com")).cookies["refresh_token"]
    laptop_refresh = (await login(client, "user@example.com")).cookies["refresh_token"]

    logout_response = await post_with_refresh(
        client, "/api/v1/auth/logout", phone_refresh
    )

    assert logout_response.status_code == 204
    # Телефон вышел: его токен больше не работает
    phone_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", phone_refresh
    )
    assert phone_response.status_code == 401
    # А вход с ноутбука — другая цепочка, он продолжает работать
    laptop_response = await post_with_refresh(
        client, "/api/v1/auth/refresh", laptop_refresh
    )
    assert laptop_response.status_code == 200


# ---------- GET /me и полный сценарий ----------


async def test_me(client, create_user):
    user = await create_user(email="user@example.com")

    response = await client.get("/api/v1/auth/me", headers=auth_headers(user))

    assert response.status_code == 200
    assert response.json() == {"id": user.id, "email": "user@example.com"}


async def test_full_flow(client):
    # Регистрация → вход → запрос к API → обновление токена → запрос к API → выход
    register_response = await client.post(
        "/api/v1/auth/register",
        json={"email": "flow@example.com", "password": PASSWORD},
    )
    assert register_response.status_code == 201

    access_token = (await login(client, "flow@example.com")).json()["access_token"]
    response = await client.get(
        "/api/v1/operations", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200

    # httpx, как браузер, сам хранит cookie и сам отправляет её на /auth/*
    refresh_response = await client.post("/api/v1/auth/refresh")
    assert refresh_response.status_code == 200
    # С новым access-токеном API работает так же
    new_access_token = refresh_response.json()["access_token"]
    response = await client.get(
        "/api/v1/operations", headers={"Authorization": f"Bearer {new_access_token}"}
    )
    assert response.status_code == 200

    logout_response = await client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


# ---------- Проверка access-токена ----------


@pytest.mark.parametrize("method, url", PROTECTED_ENDPOINTS)
async def test_protected_endpoint_without_token(client, method, url):
    response = await client.request(method, url)

    # Токена нет — клиент не аутентифицирован: 401 с подсказкой, как аутентифицироваться
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_invalid_token(client):
    response = await client.get(
        "/api/v1/operations", headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert response.status_code == 401


async def test_token_of_other_type_is_rejected(client, create_user):
    user = await create_user()
    token = jwt_encode(
        payload={"sub": str(user.id)},
        token_type="refresh",
        time_delta=timedelta(minutes=5),
    )

    response = await client.get(
        "/api/v1/operations", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


async def test_expired_access_token(client, create_user):
    user = await create_user()
    token = jwt_encode(
        payload={"sub": str(user.id)},
        token_type="access",
        time_delta=timedelta(seconds=-1),
    )

    response = await client.get(
        "/api/v1/operations", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


async def test_valid_token(client, create_user):
    user = await create_user()

    response = await client.get("/api/v1/operations", headers=auth_headers(user))

    assert response.status_code == 200


async def test_token_of_nonexistent_user(client):
    headers = {"Authorization": f"Bearer {create_access_token(999)}"}

    response = await client.get("/api/v1/operations", headers=headers)

    assert response.status_code == 401


@pytest.mark.parametrize("sub", [None, "не число"])
async def test_token_with_broken_sub(client, sub):
    payload = {"sub": sub} if sub is not None else {}
    token = jwt_encode(
        payload=payload, token_type="access", time_delta=timedelta(minutes=5)
    )

    response = await client.get(
        "/api/v1/operations", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
