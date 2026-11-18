"""Read-only access to the analytics warehouse (local DuckDB file or MotherDuck)."""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb

from sqlagent.config import Settings
from sqlagent.db.guard import guard_sql

log = logging.getLogger(__name__)


class QueryTimeoutError(RuntimeError):
    pass


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int
    tables: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


class Warehouse:
    """Thread-safe wrapper: one read-only connection, a cursor per query."""

    def __init__(self, settings: Settings, allowed_tables: set[str]):
        self.settings = settings
        self.allowed_tables = {t.lower() for t in allowed_tables}
        url = settings.warehouse_url
        config = {
            "threads": settings.duckdb_threads,
            "memory_limit": settings.duckdb_memory_limit,
        }
        if settings.duckdb_home:
            home = settings.duckdb_home
            config |= {
                "home_directory": home,
                "extension_directory": f"{home}/extensions",
                "temp_directory": f"{home}/tmp",
            }
        if url.startswith("md:"):
            # MotherDuck. Defence in depth for the hosted demo:
            #  - a read-scaling (read-only) token in MOTHERDUCK_TOKEN
            #  - saas_mode=true in the URL (no local files or extension installs from SQL)
            #  - the SQL guard, as everywhere
            # The extension needs network access to connect, so lock down after connecting.
            self._con = duckdb.connect(url, config=config)
            for stmt in ("SET enable_external_access = false", "SET lock_configuration = true"):
                try:
                    self._con.execute(stmt)
                except duckdb.Error as e:  # some MotherDuck modes manage these settings
                    log.warning("Could not apply %r on MotherDuck: %s", stmt, e)
        else:
            # Block filesystem/network access from SQL and freeze settings so a query can't
            # undo it. DuckDB shares one instance per file in-process, so these must be
            # identical for every connection.
            config |= {"enable_external_access": False, "lock_configuration": True}
            self._con = duckdb.connect(url, read_only=True, config=config)

    def run(self, sql: str) -> QueryResult:
        s = self.settings
        guarded = guard_sql(sql, self.allowed_tables, s.max_result_rows)
        cur = self._con.cursor()
        timed_out = threading.Event()

        def _interrupt() -> None:
            timed_out.set()
            cur.interrupt()

        timer = threading.Timer(s.query_timeout_s, _interrupt)
        start = time.perf_counter()
        timer.start()
        try:
            cur.execute(guarded.sql)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(s.max_result_rows + 1)
        except duckdb.InterruptException as e:
            if timed_out.is_set():
                raise QueryTimeoutError(
                    f"Query exceeded the {s.query_timeout_s:.0f}s time limit. Add filters or aggregate more."
                ) from e
            raise
        finally:
            timer.cancel()
            cur.close()
        elapsed = int((time.perf_counter() - start) * 1000)
        truncated = len(rows) > s.max_result_rows
        return QueryResult(
            sql=guarded.sql,
            columns=columns,
            rows=[[_jsonable(v) for v in r] for r in rows[: s.max_result_rows]],
            truncated=truncated,
            elapsed_ms=elapsed,
            tables=guarded.tables,
        )

    def close(self) -> None:
        self._con.close()


def _jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v
