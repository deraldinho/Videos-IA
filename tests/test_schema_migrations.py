"""Testes do mecanismo de migrações de esquema (schema_migrations).

Valida: migrações adicivas, idempotência (mesma versão não reaplica) e
registro de versão/nome/hash para auditoria. Não altera esquema de negócio;
apenas testa o mecanismo de controle de migrações.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.database import Database


def _make_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "videos.sqlite3"
    return Database(db_path)


def test_schema_migrations_tabela_existe_apos_init(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        assert row is not None, "tabela schema_migrations deve existir apos _init_schema"


def test_aplicar_migracao_registra_versao_e_executa_sql(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    sql = "CREATE TABLE IF NOT EXISTS tabela_teste_migracao (id INTEGER PRIMARY KEY)"
    db.apply_migration(1, "cria_tabela_teste", sql)

    # Registro presente em schema_migrations.
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT version, name, sql_hash FROM schema_migrations WHERE version = 1"
        ).fetchone()
        assert row is not None
        assert row["name"] == "cria_tabela_teste"
        assert row["sql_hash"] != ""

    # Tabela foi efetivamente criada no banco.
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tabela_teste_migracao'"
        ).fetchone()
        assert row is not None


def test_aplicar_mesma_migracao_duas_vezes_e_idempotente(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    sql = "CREATE TABLE IF NOT EXISTS tabela_idempotente (id INTEGER PRIMARY KEY)"
    db.apply_migration(2, "cria_tabela_idempotente", sql)
    db.apply_migration(2, "cria_tabela_idempotente", sql)  # segunda aplicacao

    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 2"
        ).fetchone()
        assert rows["total"] == 1, "mesma versao nao deve ser registrada duas vezes"


def test_versoes_diferentes_sao_registradas_independentemente(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    db.apply_migration(3, "migracao_tres", "CREATE TABLE IF NOT EXISTS t3 (id INTEGER)")
    db.apply_migration(4, "migracao_quatro", "CREATE TABLE IF NOT EXISTS t4 (id INTEGER)")

    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations"
        ).fetchone()
        assert rows["total"] == 2


def test_migracao_com_sql_invalido_nao_registra_versao(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    sql_invalido = "CREATE TABLE IF NOT EXISTS t_invalida (id) SQL_INVALIDO_AQUI"
    with pytest.raises(Exception):
        db.apply_migration(5, "migracao_invalida", sql_invalido)

    with db.transaction() as conn:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 5"
        ).fetchone()
        assert row is None, "versao nao deve ser registrada quando SQL falha"
