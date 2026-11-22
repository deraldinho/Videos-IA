"""Testes do módulo de agendamento (src/scheduler.py).

Cobre conversão de fuso horario, lead time, limite diario (cap), deduplicacao
e horizonte de busca do próximo slot de publicacao.
"""

from __future__ import annotations

import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# Torna o pacote `src` (e src/scheduler.py) importavel a partir dos testes.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scheduler import next_slot, valid_production_day  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")
UTC = timezone.utc


def _make_settings(**overrides):
    from src.settings import PublicationSettings  # noqa: PLC0415

    base = dict(
        timezone="America/Sao_Paulo",
        production_time=time(8, 0),
        production_days=(1, 2, 3, 4, 5, 6, 7),
        publish_days=(1, 2, 3, 4, 5, 6, 7),
        publish_times=(time(12, 0), time(19, 0)),
        videos_per_day=2,
        queue_limit=10,
        horizon_days=7,
        min_lead_minutes=30,
        tick_minutes=15,
    )
    base.update(overrides)
    return PublicationSettings(**base)


def _sp(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=SP)


def _utc(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ------------------------------------------------------------------ valid_production_day

def test_valid_production_day_returns_local_iso_date():
    settings = _make_settings()
    # 2026-11-20 eh sexta-feira (isoweekday 5); 15:00 UTC = 12:00 SP.
    now = _utc(2026, 11, 20, 15, 0)
    assert valid_production_day(settings, now) == "2026-11-20"


def test_valid_production_day_none_when_not_production_day():
    settings = _make_settings(production_days=(1,))  # apenas segunda-feira
    now = _utc(2026, 11, 20, 15, 0)  # sexta-feira
    assert valid_production_day(settings, now) is None


def test_valid_production_day_if_due_before_production_time_is_none():
    settings = _make_settings(production_time=time(8, 0))
    # 10:00 UTC = 07:00 SP, antes das 08:00.
    now = _utc(2026, 11, 20, 10, 0)
    assert valid_production_day(settings, now, if_due=True) is None


def test_valid_production_day_if_due_after_production_time_returns_date():
    settings = _make_settings(production_time=time(8, 0))
    # 15:00 UTC = 12:00 SP, depois das 08:00.
    now = _utc(2026, 11, 20, 15, 0)
    assert valid_production_day(settings, now, if_due=True) == "2026-11-20"


def test_valid_production_day_converts_timezone_to_local_date():
    # 2026-11-20 02:00 UTC = 2026-11-19 23:00 SP (dia anterior no fuso local).
    settings = _make_settings(production_days=(4, 5, 6, 7))  # qui-dom
    now = _utc(2026, 11, 20, 2, 0)
    assert valid_production_day(settings, now) == "2026-11-19"


# ------------------------------------------------------------------ next_slot

def test_next_slot_returns_first_today_slot_utc():
    settings = _make_settings()
    # Segunda 2026-11-16 08:00 UTC = 05:00 SP; primeiro slot eh 12:00 SP = 15:00 UTC.
    now = _utc(2026, 11, 16, 8, 0)
    slot = next_slot(settings, now)
    assert slot is not None
    assert slot.tzinfo is not None
    assert slot == _utc(2026, 11, 16, 15, 0)


def test_next_slot_respects_lead_time():
    settings = _make_settings()
    # 14:40 UTC = 11:40 SP; lead 30 min => earliest 12:10 SP, entao 12:00 SP eh pulado.
    now = _utc(2026, 11, 16, 14, 40)
    slot = next_slot(settings, now)
    assert slot == _utc(2026, 11, 16, 22, 0)  # 19:00 SP


def test_next_slot_daily_cap_fills_day_with_off_window_times():
    settings = _make_settings(videos_per_day=2)
    now = _utc(2026, 11, 16, 5, 0)  # 05:00 SP, cedo
    # Dois horarios ocupados fora da janela na segunda (contam para o cap).
    unavailable = [_sp(2026, 11, 16, 14, 0), _sp(2026, 11, 16, 16, 0)]
    slot = next_slot(settings, now, unavailable=unavailable)
    assert slot == _utc(2026, 11, 17, 15, 0)  # terca 12:00 SP


def test_next_slot_avoids_duplicate_of_occupied_publish_time():
    settings = _make_settings(videos_per_day=2)
    now = _utc(2026, 11, 16, 5, 0)  # 05:00 SP
    unavailable = [_sp(2026, 11, 16, 12, 0)]  # ja ocupado na janela
    slot = next_slot(settings, now, unavailable=unavailable)
    assert slot == _utc(2026, 11, 16, 22, 0)  # 19:00 SP


def test_next_slot_none_when_horizon_exhausted():
    settings = _make_settings(horizon_days=0, videos_per_day=1)
    now = _utc(2026, 11, 16, 23, 0)  # 20:00 SP, depois do ultimo slot do dia
    # Horizon eh inclusivo: horizon_days=0 busca apenas hoje; sem slot hoje apos o lead.
    assert next_slot(settings, now) is None


def test_next_slot_handles_dst_timezone_without_crashing():
    settings = _make_settings(timezone="America/New_York")
    now = _utc(2026, 3, 8, 7, 0)  # perto da mudanca de horario nos EUA
    slot = next_slot(settings, now)
    assert slot is not None
    assert slot.tzinfo is not None


def test_next_slot_naive_now_treated_as_utc():
    settings = _make_settings()
    # naive 2026-11-16 08:00 interpretado como UTC = 05:00 SP.
    now = datetime(2026, 11, 16, 8, 0)
    slot = next_slot(settings, now)
    assert slot == _utc(2026, 11, 16, 15, 0)


__all__ = []
