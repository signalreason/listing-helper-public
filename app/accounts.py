"""Small account store for the two-user application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import psycopg
from psycopg.rows import dict_row

USER_COLUMNS = "id, email, password_hash, password_version, is_owner, enabled"


class AccountStoreUnavailable(Exception):
    pass


class EmailAlreadyExists(Exception):
    pass


class AccountLimitReached(Exception):
    pass


class SetupAlreadyComplete(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: int
    email: str
    password_hash: str
    password_version: int
    is_owner: bool
    enabled: bool


class AccountStore(Protocol):
    def count_users(self) -> int: ...

    def get_user_by_id(self, user_id: int) -> User | None: ...

    def get_user_by_email(self, email: str) -> User | None: ...

    def create_owner(self, email: str, password_hash: str) -> User: ...

    def create_user(self, email: str, password_hash: str) -> User: ...

    def list_users(self) -> list[User]: ...

    def reset_password(self, user_id: int, password_hash: str) -> User | None: ...

    def set_enabled(self, user_id: int, enabled: bool) -> User | None: ...


class PostgresAccountStore:
    """Open short-lived database connections; expected traffic is intentionally tiny."""

    _LOCK_ID = 818_194_503

    def _connect(self):
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise AccountStoreUnavailable("DATABASE_URL is not configured")
        try:
            return psycopg.connect(database_url)
        except psycopg.Error as error:
            raise AccountStoreUnavailable("account database is unavailable") from error

    def count_users(self) -> int:
        try:
            with self._connect() as connection:
                return connection.execute("SELECT count(*) FROM users").fetchone()[0]
        except psycopg.Error as error:
            raise AccountStoreUnavailable("account database is unavailable") from error

    def get_user_by_id(self, user_id: int) -> User | None:
        return self._one(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,))

    def get_user_by_email(self, email: str) -> User | None:
        return self._one(f"SELECT {USER_COLUMNS} FROM users WHERE email = %s", (email,))

    def _one(self, query: str, parameters: tuple[object, ...]) -> User | None:
        try:
            with (
                self._connect() as connection,
                connection.cursor(row_factory=dict_row) as cursor,
            ):
                row = cursor.execute(query, parameters).fetchone()
                return User(**row) if row else None
        except psycopg.Error as error:
            raise AccountStoreUnavailable("account database is unavailable") from error

    def create_owner(self, email: str, password_hash: str) -> User:
        try:
            with self._connect() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (self._LOCK_ID,))
                if connection.execute("SELECT count(*) FROM users").fetchone()[0]:
                    raise SetupAlreadyComplete
                with connection.cursor(row_factory=dict_row) as cursor:
                    row = cursor.execute(
                        f"""
                        INSERT INTO users (email, password_hash, is_owner)
                        VALUES (%s, %s, true)
                        RETURNING {USER_COLUMNS}
                        """,
                        (email, password_hash),
                    ).fetchone()
                    return User(**row)
        except psycopg.errors.UniqueViolation as error:
            raise EmailAlreadyExists from error
        except psycopg.Error as error:
            raise AccountStoreUnavailable("account database is unavailable") from error

    def create_user(self, email: str, password_hash: str) -> User:
        try:
            with self._connect() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (self._LOCK_ID,))
                active = connection.execute("SELECT count(*) FROM users WHERE enabled").fetchone()[
                    0
                ]
                if active >= 2:
                    raise AccountLimitReached
                with connection.cursor(row_factory=dict_row) as cursor:
                    row = cursor.execute(
                        f"""
                        INSERT INTO users (email, password_hash)
                        VALUES (%s, %s)
                        RETURNING {USER_COLUMNS}
                        """,
                        (email, password_hash),
                    ).fetchone()
                    return User(**row)
        except psycopg.errors.UniqueViolation as error:
            raise EmailAlreadyExists from error
        except psycopg.Error as error:
            raise AccountStoreUnavailable("account database is unavailable") from error

    def list_users(self) -> list[User]:
        try:
            with (
                self._connect() as connection,
                connection.cursor(row_factory=dict_row) as cursor,
            ):
                return [
                    User(**row)
                    for row in cursor.execute(
                        f"SELECT {USER_COLUMNS} FROM users ORDER BY id"
                    ).fetchall()
                ]
        except psycopg.Error as error:
            raise AccountStoreUnavailable("account database is unavailable") from error

    def reset_password(self, user_id: int, password_hash: str) -> User | None:
        try:
            with (
                self._connect() as connection,
                connection.cursor(row_factory=dict_row) as cursor,
            ):
                row = cursor.execute(
                    f"""
                    UPDATE users
                    SET password_hash = %s, password_version = password_version + 1
                    WHERE id = %s
                    RETURNING {USER_COLUMNS}
                    """,
                    (password_hash, user_id),
                ).fetchone()
                return User(**row) if row else None
        except psycopg.Error as error:
            raise AccountStoreUnavailable("account database is unavailable") from error

    def set_enabled(self, user_id: int, enabled: bool) -> User | None:
        try:
            with self._connect() as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (self._LOCK_ID,))
                with connection.cursor(row_factory=dict_row) as cursor:
                    row = cursor.execute(
                        f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,)
                    ).fetchone()
                    if row is None:
                        return None
                    user = User(**row)
                    if enabled and not user.enabled:
                        active = connection.execute(
                            "SELECT count(*) FROM users WHERE enabled"
                        ).fetchone()[0]
                        if active >= 2:
                            raise AccountLimitReached
                    row = cursor.execute(
                        f"""UPDATE users SET enabled = %s WHERE id = %s
                        RETURNING {USER_COLUMNS}""",
                        (enabled, user_id),
                    ).fetchone()
                    return User(**row)
        except psycopg.Error as error:
            raise AccountStoreUnavailable("account database is unavailable") from error


class MemoryAccountStore:
    """Deterministic store for tests; production uses PostgreSQL."""

    def __init__(self) -> None:
        self.users: dict[int, User] = {}
        self.next_id = 1

    def count_users(self) -> int:
        return len(self.users)

    def get_user_by_id(self, user_id: int) -> User | None:
        return self.users.get(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        return next((user for user in self.users.values() if user.email == email), None)

    def create_owner(self, email: str, password_hash: str) -> User:
        if self.users:
            raise SetupAlreadyComplete
        return self._create(email, password_hash, is_owner=True)

    def create_user(self, email: str, password_hash: str) -> User:
        if sum(user.enabled for user in self.users.values()) >= 2:
            raise AccountLimitReached
        return self._create(email, password_hash, is_owner=False)

    def _create(self, email: str, password_hash: str, *, is_owner: bool) -> User:
        if self.get_user_by_email(email):
            raise EmailAlreadyExists
        user = User(self.next_id, email, password_hash, 1, is_owner, True)
        self.users[user.id] = user
        self.next_id += 1
        return user

    def list_users(self) -> list[User]:
        return list(self.users.values())

    def reset_password(self, user_id: int, password_hash: str) -> User | None:
        user = self.users.get(user_id)
        if user is None:
            return None
        updated = User(
            user.id,
            user.email,
            password_hash,
            user.password_version + 1,
            user.is_owner,
            user.enabled,
        )
        self.users[user_id] = updated
        return updated

    def set_enabled(self, user_id: int, enabled: bool) -> User | None:
        user = self.users.get(user_id)
        if user is None:
            return None
        if enabled and not user.enabled and sum(item.enabled for item in self.users.values()) >= 2:
            raise AccountLimitReached
        updated = User(
            user.id,
            user.email,
            user.password_hash,
            user.password_version,
            user.is_owner,
            enabled,
        )
        self.users[user_id] = updated
        return updated
