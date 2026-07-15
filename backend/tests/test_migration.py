"""The additive SQLite migration must add the ssh password column, idempotently."""

from sqlalchemy import inspect

from zeta.db import engine, _ensure_columns, init_db


def test_ssh_password_column_added_and_idempotent():
    init_db()  # creates tables + runs the migration
    cols = {c["name"] for c in inspect(engine).get_columns("ssh_accounts")}
    assert "password" in cols
    # running again must not raise (column already present)
    _ensure_columns()
    cols2 = {c["name"] for c in inspect(engine).get_columns("ssh_accounts")}
    assert "password" in cols2
