from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from atlas.api.routers.admin import router as admin_router
from atlas.api.routers.auth import router as auth_router
from atlas.api.routers.chat import router as chat_router
from atlas.api.routers.documents import router as documents_router
from atlas.config import Settings
from atlas.db.models import Base
from atlas.repositories.chroma_repo import ChromaChunkStore
from atlas.repositories.user_repo import ROLE_ADMIN, ROLE_USER, UserRepository
from atlas.schemas.chat import DocumentOut
from atlas.services.admin import (
    AdminError,
    AdminNotFoundError,
    apply_admin_usernames,
    parse_admin_usernames,
    set_user_disabled,
    set_user_role,
    user_is_admin,
    user_is_disabled,
)
from atlas.services.auth import seed_demo_users, signup_user
from atlas.services.embeddings import EmbeddingClient
from atlas.services.ingest import IngestService
from atlas.services.rag import RagChatService


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


def test_parse_admin_usernames_strips_and_skips_blanks() -> None:
    settings = Settings(atlas_admin_usernames=" joshua, ,alice,")
    assert parse_admin_usernames(settings) == ["joshua", "alice"]


async def test_apply_admin_usernames_with_empty_setting_is_a_no_op(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await apply_admin_usernames(session_factory, Settings()) == 0


async def test_apply_admin_usernames_promotes_existing_and_skips_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(atlas_demo_users="alice:secret-a", atlas_admin_usernames="alice,nobody")
    await seed_demo_users(session_factory, settings)
    assert await apply_admin_usernames(session_factory, settings) == 1
    assert await apply_admin_usernames(session_factory, settings) == 0

    async with session_factory() as session:
        alice = await UserRepository(session).get_by_username("alice")
    assert alice is not None
    assert alice.role == ROLE_ADMIN


async def test_set_role_refuses_self_and_last_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_user(
        session_factory, "boss", "correct-horse-battery", email="boss@example.com"
    )
    await apply_admin_usernames(
        session_factory, Settings(atlas_admin_usernames="boss")
    )
    with pytest.raises(AdminError, match="your own role"):
        await set_user_role(
            session_factory,
            actor_username="boss",
            username="boss",
            role=ROLE_USER,
        )
    with pytest.raises(AdminError, match="last admin"):
        await set_user_role(
            session_factory,
            actor_username="other",
            username="boss",
            role=ROLE_USER,
        )


async def test_disable_then_enable_a_regular_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_user(
        session_factory, "boss", "correct-horse-battery", email="boss@example.com"
    )
    await signup_user(
        session_factory, "carol", "correct-horse-battery", email="carol@example.com"
    )
    await apply_admin_usernames(
        session_factory, Settings(atlas_admin_usernames="boss")
    )
    disabled = await set_user_disabled(
        session_factory,
        actor_username="boss",
        username="carol",
        disabled=True,
    )
    assert disabled.disabled is True
    enabled = await set_user_disabled(
        session_factory,
        actor_username="boss",
        username="carol",
        disabled=False,
    )
    assert enabled.disabled is False


async def test_disable_refuses_self_and_last_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_user(
        session_factory, "boss", "correct-horse-battery", email="boss@example.com"
    )
    await apply_admin_usernames(
        session_factory, Settings(atlas_admin_usernames="boss")
    )
    with pytest.raises(AdminError, match="your own account"):
        await set_user_disabled(
            session_factory,
            actor_username="boss",
            username="boss",
            disabled=True,
        )
    with pytest.raises(AdminError, match="last admin"):
        await set_user_disabled(
            session_factory,
            actor_username="other",
            username="boss",
            disabled=True,
        )


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    settings = Settings(
        openai_api_key="sk-test",
        atlas_auth_secret="admin-http-test-secret-long-enough",
        atlas_demo_users="alice:secret-a|bob:secret-b",
        atlas_admin_usernames="alice",
        rate_limit_enabled=False,
        login_lockout_enabled=False,
    )
    await seed_demo_users(factory, settings)
    await apply_admin_usernames(factory, settings)
    ingest = AsyncMock(spec=IngestService)
    ingest.ingest_path = AsyncMock(
        return_value=DocumentOut(
            id="doc-1",
            title="Notes",
            original_filename="notes.md",
            chunk_count=1,
            created_at="2026-09-21T00:00:00",
        )
    )
    ingest.delete_document = AsyncMock(return_value=True)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.ingest_service = ingest
    app.state.ingest_queue = None
    app.state.rag_service = RagChatService(
        settings,
        AsyncOpenAI(api_key="sk-test"),
        factory,
        cast(ChromaChunkStore, object()),
        cast(EmbeddingClient, object()),
    )
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(chat_router)
    app.include_router(documents_router)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield http
    await engine.dispose()


async def _login(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "token": body["access_token"],
        "role": body["role"],
        "header": f"Bearer {body['access_token']}",
    }


def _auth(login: dict[str, str]) -> dict[str, str]:
    return {"Authorization": login["header"]}


@pytest.mark.asyncio
async def test_login_returns_role_and_me_matches(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    bob = await _login(client, "bob", "secret-b")
    assert alice["role"] == "admin"
    assert bob["role"] == "user"

    me = await client.get("/api/auth/me", headers=_auth(alice))
    assert me.status_code == 200
    assert me.json() == {"username": "alice", "role": "admin"}


@pytest.mark.asyncio
async def test_regular_user_cannot_list_or_change_people(client: AsyncClient) -> None:
    bob = await _login(client, "bob", "secret-b")
    listed = await client.get("/api/admin/users", headers=_auth(bob))
    assert listed.status_code == 403

    promoted = await client.post(
        "/api/admin/users/alice/role",
        headers=_auth(bob),
        json={"role": "admin"},
    )
    assert promoted.status_code == 403


@pytest.mark.asyncio
async def test_admin_lists_users_and_promotes_then_demotes(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    listed = await client.get("/api/admin/users", headers=_auth(alice))
    assert listed.status_code == 200
    names = {row["username"]: row["role"] for row in listed.json()}
    assert names["alice"] == "admin"
    assert names["bob"] == "user"

    promoted = await client.post(
        "/api/admin/users/bob/role",
        headers=_auth(alice),
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    demoted = await client.post(
        "/api/admin/users/bob/role",
        headers=_auth(alice),
        json={"role": "user"},
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "user"


@pytest.mark.asyncio
async def test_admin_cannot_demote_self_or_last_admin(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    self_demote = await client.post(
        "/api/admin/users/alice/role",
        headers=_auth(alice),
        json={"role": "user"},
    )
    assert self_demote.status_code == 400

    missing = await client.post(
        "/api/admin/users/nobody/role",
        headers=_auth(alice),
        json={"role": "admin"},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_disable_blocks_login_and_existing_token(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    bob = await _login(client, "bob", "secret-b")

    disabled = await client.post(
        "/api/admin/users/bob/disable",
        headers=_auth(alice),
    )
    assert disabled.status_code == 200
    assert disabled.json()["disabled"] is True

    blocked_login = await client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "secret-b"},
    )
    assert blocked_login.status_code == 403
    assert blocked_login.json()["detail"] == "This account is disabled."

    blocked_me = await client.get("/api/auth/me", headers=_auth(bob))
    assert blocked_me.status_code == 403

    enabled = await client.post(
        "/api/admin/users/bob/enable",
        headers=_auth(alice),
    )
    assert enabled.status_code == 200
    assert enabled.json()["disabled"] is False
    restored = await _login(client, "bob", "secret-b")
    assert restored["role"] == "user"


@pytest.mark.asyncio
async def test_admin_cannot_disable_self(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    response = await client.post(
        "/api/admin/users/alice/disable",
        headers=_auth(alice),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_library_mutations_are_admin_only(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    bob = await _login(client, "bob", "secret-b")

    bob_list = await client.get("/api/documents", headers=_auth(bob))
    assert bob_list.status_code == 200

    bob_upload = await client.post(
        "/api/documents/upload",
        headers=_auth(bob),
        files={"file": ("notes.md", b"hello", "text/markdown")},
    )
    assert bob_upload.status_code == 403

    alice_upload = await client.post(
        "/api/documents/upload",
        headers=_auth(alice),
        files={"file": ("notes.md", b"hello", "text/markdown")},
    )
    assert alice_upload.status_code == 200

    bob_delete = await client.delete("/api/documents/doc-1", headers=_auth(bob))
    assert bob_delete.status_code == 403

    alice_delete = await client.delete("/api/documents/doc-1", headers=_auth(alice))
    assert alice_delete.status_code == 200


@pytest.mark.asyncio
async def test_admin_still_cannot_read_another_users_thread(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    bob = await _login(client, "bob", "secret-b")
    created = await client.post("/api/threads", headers=_auth(bob))
    assert created.status_code == 200
    thread_id = created.json()["id"]

    forbidden = await client.get(f"/api/threads/{thread_id}", headers=_auth(alice))
    assert forbidden.status_code == 403

    listed = await client.get("/api/threads", headers=_auth(alice))
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.asyncio
async def test_unknown_user_disable_or_enable_is_404(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    disabled = await client.post(
        "/api/admin/users/missing/disable",
        headers=_auth(alice),
    )
    assert disabled.status_code == 404
    enabled = await client.post(
        "/api/admin/users/missing/enable",
        headers=_auth(alice),
    )
    assert enabled.status_code == 404


@pytest.mark.asyncio
async def test_me_requires_login(client: AsyncClient) -> None:
    response = await client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_role_payload_is_422(client: AsyncClient) -> None:
    alice = await _login(client, "alice", "secret-a")
    response = await client.post(
        "/api/admin/users/bob/role",
        headers=_auth(alice),
        json={"role": "superuser"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_signup_creates_a_user_role_not_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await signup_user(
        session_factory, "carol", "correct-horse-battery", email="carol@example.com"
    )
    async with session_factory() as session:
        carol = await UserRepository(session).get_by_username("carol")
    assert carol is not None
    assert carol.role == ROLE_USER
    assert carol.disabled_at is None


async def test_demo_login_refuses_a_disabled_demo_account(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(
        atlas_auth_secret="admin-http-test-secret-long-enough",
        atlas_demo_users="alice:secret-a",
        rate_limit_enabled=False,
        login_lockout_enabled=False,
    )
    await seed_demo_users(session_factory, settings)
    async with session_factory() as session:
        repo = UserRepository(session)
        alice = await repo.get_by_username("alice")
        assert alice is not None
        assert user_is_admin(alice) is False
        assert user_is_disabled(alice) is False
        await repo.set_disabled_at(alice.id, datetime.now(UTC))
        alice = await repo.get_by_username("alice")
        assert alice is not None
        assert user_is_disabled(alice) is True

    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(auth_router)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post("/api/auth/demo")
    assert response.status_code == 403


async def test_set_disabled_rejects_blank_and_unknown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(AdminNotFoundError):
        await set_user_disabled(
            session_factory, actor_username="alice", username="   ", disabled=True
        )
    with pytest.raises(AdminNotFoundError):
        await set_user_disabled(
            session_factory, actor_username="alice", username="ghost", disabled=True
        )


async def test_set_role_rejects_blank_and_unknown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(AdminError, match="user or admin"):
        await set_user_role(
            session_factory, actor_username="alice", username="bob", role="owner"
        )
    with pytest.raises(AdminNotFoundError):
        await set_user_role(
            session_factory, actor_username="alice", username="   ", role=ROLE_ADMIN
        )
    with pytest.raises(AdminNotFoundError):
        await set_user_role(
            session_factory, actor_username="alice", username="ghost", role=ROLE_ADMIN
        )


async def test_set_role_and_disable_treat_vanished_row_as_missing(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await signup_user(
        session_factory, "boss", "correct-horse-battery", email="boss@example.com"
    )
    await signup_user(
        session_factory, "carol", "correct-horse-battery", email="carol@example.com"
    )
    await apply_admin_usernames(
        session_factory, Settings(atlas_admin_usernames="boss")
    )

    async def _missing_update(self: UserRepository, user_id: str, *args: object) -> None:
        return None

    monkeypatch.setattr(UserRepository, "set_role", _missing_update)
    with pytest.raises(AdminNotFoundError):
        await set_user_role(
            session_factory,
            actor_username="boss",
            username="carol",
            role=ROLE_ADMIN,
        )
    monkeypatch.setattr(UserRepository, "set_disabled_at", _missing_update)
    with pytest.raises(AdminNotFoundError):
        await set_user_disabled(
            session_factory,
            actor_username="boss",
            username="carol",
            disabled=True,
        )
