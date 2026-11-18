import duckdb
import pytest

from .conftest import requires_warehouse

pytestmark = requires_warehouse


def test_connection_blocks_filesystem_access_even_without_guard(warehouse):
    # Defence in depth: bypass the SQL guard and hit the raw connection.
    with pytest.raises(duckdb.Error):
        warehouse._con.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()


def test_connection_is_read_only(warehouse):
    with pytest.raises(duckdb.Error):
        warehouse._con.execute("CREATE TABLE hack AS SELECT 1")


def test_settings_are_locked(warehouse):
    with pytest.raises(duckdb.Error):
        warehouse._con.execute("SET enable_external_access = true")


def test_run_truncates_and_serializes(warehouse, settings):
    r = warehouse.run("SELECT d_date, d_year FROM date_dim")
    assert r.truncated and r.row_count == settings.max_result_rows
    assert isinstance(r.rows[0][0], str)  # dates are JSON-friendly


def test_prepare_duckdb_home_sets_home_only_when_unusable(monkeypatch, tmp_path):
    import os

    from sqlagent.db.warehouse import prepare_duckdb_home

    monkeypatch.delenv("HOME", raising=False)
    prepare_duckdb_home(str(tmp_path / "duck"))
    assert os.environ["HOME"] == str(tmp_path / "duck")
    assert (tmp_path / "duck" / "extensions").is_dir()

    monkeypatch.setenv("HOME", str(tmp_path))  # usable home: leave it alone
    prepare_duckdb_home(str(tmp_path / "duck2"))
    assert os.environ["HOME"] == str(tmp_path)
