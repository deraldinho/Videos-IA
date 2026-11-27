"""Testes de cobertura para src/retry.py (retry_call com backoff)."""

import pytest

from src.retry import retry_call


def test_retry_call_sucesso_na_primeira():
    chamadas = []

    def fn():
        chamadas.append(1)
        return "ok"

    assert retry_call(fn) == "ok"
    assert chamadas == [1]


def test_retry_call_sucesso_apos_falhas():
    estado = {"n": 0}

    def fn():
        estado["n"] += 1
        if estado["n"] < 3:
            raise ValueError("transiente")
        return "feito"

    assert retry_call(fn, max_attempts=5, base_delay_seconds=0) == "feito"
    assert estado["n"] == 3


def test_retry_call_esgota_e_releva_excecao():
    def fn():
        raise RuntimeError("sempre falha")

    with pytest.raises(RuntimeError, match="sempre falha"):
        retry_call(fn, max_attempts=3, base_delay_seconds=0)


def test_retry_call_on_retry_recebe_tentativa_e_excecao():
    chamadas = []

    def fn():
        raise ValueError("x")

    def on_retry(attempt, exc):
        chamadas.append((attempt, exc))

    with pytest.raises(ValueError):
        retry_call(fn, max_attempts=2, base_delay_seconds=0, on_retry=on_retry)
    assert len(chamadas) == 1
    assert chamadas[0][0] == 1
    assert isinstance(chamadas[0][1], ValueError)


def test_retry_call_on_retry_quebra_nao_impede_retry():
    def fn():
        raise ValueError("y")

    def on_retry_quebra(attempt, exc):
        raise RuntimeError("callback quebrou")

    # callback quebrando nao impede o retry de prosseguir ate esgotar
    with pytest.raises(ValueError, match="y"):
        retry_call(fn, max_attempts=2, base_delay_seconds=0, on_retry=on_retry_quebra)


def test_retry_call_erro_sem_excecao_registrada():
    # max_attempts=0 => loop nao executa, last_exc continua None -> levanta RuntimeError
    with pytest.raises(RuntimeError):
        retry_call(lambda: 1, max_attempts=0, base_delay_seconds=0)


__all__ = []
