from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_utc_now_lock = Lock()
_last_utc_timestamp: datetime | None = None


class Base(DeclarativeBase):
    pass


def _utc_now() -> datetime:
    """Monotonic UTC timestamp with microsecond resolution.

    SQLite's ``CURRENT_TIMESTAMP`` is only one second wide, and fast back-to-back
    inserts can still tie on the same microsecond. Bump by 1µs so chat order stays
    stable under load and in tests.
    """
    global _last_utc_timestamp
    with _utc_now_lock:
        now = datetime.now(UTC)
        if _last_utc_timestamp is not None and now <= _last_utc_timestamp:
            now = _last_utc_timestamp + timedelta(microseconds=1)
        _last_utc_timestamp = now
        return now


class ChatThread(Base):
    __tablename__ = "chat_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
    )
    thread: Mapped[ChatThread] = relationship(back_populates="messages")


class User(Base):
    """A real account: signup stores a bcrypt hash here, never the password
    itself. Demo accounts (alice/bob) are ordinary rows in this same table,
    seeded on startup — not a special case."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Optional today; required to receive a reset link (step 12). Step 14 may
    # verify it.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class IndexedDocument(Base):
    __tablename__ = "indexed_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    original_filename: Mapped[str] = mapped_column(String(300))
    chunk_count: Mapped[int] = mapped_column(Integer)
    # Upload-time retrieval self-test: how many generated questions found their
    # own passage. Both stay NULL when the test did not run.
    retrieval_check_hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_check_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
