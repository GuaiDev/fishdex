"""Request-only SQL tripwire. SQLite resolves table names; SQL retains bindings."""

import re
import sqlite3
from contextvars import ContextVar

from src.storage.ownership import PERSONAL_TABLES

# Shared across the TestClient caller, its portal and AnyIO worker threads.
request_trace = ContextVar("request_tenancy_trace", default=None)

_TOKEN = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
    r"`[^`]*`|\[[^]]*\]|:[\w]+|@[\w]+|\$[\w]+|\?\d*|[\w]+|[^\s]",
    re.S,
)


def tokens(sql):
    return [
        t.lower() if not t.startswith("'") else t
        for t in _TOKEN.findall(sql)
        if not t.startswith(("--", "/*"))
    ]


def shape(sql):
    return " ".join(tokens(sql)).rstrip(" ;")


# Exact shapes only, each justified here. Populated only by exercised route SQL.
EXCEPTIONS = {
    # Authentication must resolve a bearer token before its owner is known.
    shape("select * from [user_sessions] where token = ?"): "token authentication",
    # Logout revokes the session resolved from the authenticated bearer token.
    shape("DELETE FROM user_sessions WHERE id = ?"): "token revocation",
    # Signup authenticates the unused invite before a user id exists.
    shape(
        "select * from [invite_codes] where code = ? AND is_active = 1 AND used_by IS NULL"
    ): "signup invite validation",
    # Usernames must be unique across accounts before allocating an id.
    shape("select * from [users] where LOWER(username) = ?"): "signup username uniqueness",
    # SQLite allocates the new owner's primary key during account creation.
    shape(
        "INSERT INTO [users] ([daily_message_limit], [display_name], [role], [username]) "
        "VALUES (?, ?, ?, ?)"
    ): "signup account creation",
    # sqlite-utils reads the validated invite before updating it by primary key.
    shape("select * from [invite_codes] where [id] = ?"): "signup invite update lookup",
    # Signup consumes the validated invite for the newly allocated user.
    shape(
        "update [invite_codes] set [used_by] = ?, [used_at] = ?, [is_active] = ? where [id] = ?"
    ): "signup invite consumption",
    # Readiness reports the global schema version, not an angler's data.
    shape("SELECT MAX(version) FROM schema_migrations"): "schema readiness",
}


def has_owner_binding(sql, parameters, tables):
    parts = tokens(sql)
    bound = {}
    index = 0
    for pos, part in enumerate(parts):
        if part.startswith(("?", ":", "@", "$")):
            if isinstance(parameters, dict):
                value = parameters.get(part[1:])
            else:
                if part.startswith("?") and part[1:]:
                    index = int(part[1:]) - 1
                value = parameters[index] if index < len(parameters) else None
                index += 1
            bound[pos] = value is not None
    owner_keys = {"user_id"}
    if tables == {"users"}:
        owner_keys.add("id")
    for pos, part in enumerate(parts):
        if "where" not in parts[:pos]:
            continue
        if part.strip('"`[]') not in owner_keys:
            continue
        # Owner predicates, not a projected column, string, or comment.
        if parts[pos + 1 : pos + 2] == ["="] and bound.get(pos + 2):
            return True
        if parts[pos + 1 : pos + 3] == ["in", "("] and bound.get(pos + 3):
            if parts[pos + 4 : pos + 5] == [")"]:
                return True
    # INSERT's column list maps each owner column to its VALUES binding.
    if parts[:1] in (["insert"], ["replace"]) and "values" in parts:
        values = parts.index("values")
        try:
            start = parts.index("(")
            end = parts.index(")", start)
            columns = [p.strip('"`[]') for p in parts[start + 1 : end] if p != ","]
            value_start = parts.index("(", values)
            value_end = parts.index(")", value_start)
            expressions = []
            expr = []
            for pos in range(value_start + 1, value_end):
                if parts[pos] == ",":
                    expressions.append(expr)
                    expr = []
                else:
                    expr.append(pos)
            expressions.append(expr)
            for column, expression in zip(columns, expressions):
                if column in owner_keys and len(expression) == 1 and bound.get(expression[0]):
                    return True
        except ValueError:
            pass
    return False


class TraceConnection(sqlite3.Connection):
    """Trace callbacks cannot raise, so collect violations for the request boundary."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tables = set()
        self._statement = ("", ())
        self.set_authorizer(self._authorize)
        self._observer = None
        super().set_trace_callback(self._dispatch)

    def set_trace_callback(self, callback):
        # Existing route tests also collect SQL; their observer must not disable enforcement.
        self._observer = callback

    def _dispatch(self, expanded):
        self._trace(expanded)
        if self._observer is not None:
            self._observer(expanded)

    def _authorize(self, action, table, column, database, trigger):
        if action in (
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
        ):
            if table in PERSONAL_TABLES:
                self._tables.add(table)
        return sqlite3.SQLITE_OK

    def _trace(self, expanded):
        trace = request_trace.get()
        tables = set(self._tables)
        if not self._statement[0]:
            self._tables.clear()
        if trace is None:
            return
        sql, parameters = self._statement
        if expanded.strip().upper() in {"BEGIN", "COMMIT", "ROLLBACK"}:
            trace["statements"].append(expanded)
            return
        trace["statements"].append(sql or expanded)
        if tables and shape(sql) not in EXCEPTIONS:
            if not has_owner_binding(sql, parameters, tables):
                trace["violations"].append((sorted(tables), sql or expanded))

    def cursor(self, *args, **kwargs):
        kwargs.setdefault("factory", TraceCursor)
        return super().cursor(*args, **kwargs)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql):
        return self.cursor().executescript(sql)


class TraceCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        self.connection._tables.clear()
        self.connection._statement = (sql, parameters)
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters):
        self.connection._tables.clear()
        self.connection._statement = (sql, ())

        # Preserve per-row bindings (including generators) for each trace callback.
        def rows():
            for row in parameters:
                self.connection._statement = (sql, row)
                yield row

        return super().executemany(sql, rows())

    def executescript(self, sql):
        # Scripts have no bindings. Each expanded statement is checked separately.
        self.connection._tables.clear()
        self.connection._statement = ("", ())
        return super().executescript(sql)
