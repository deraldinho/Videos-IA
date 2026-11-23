"""Regressao do comando schedule do CLI."""

import datetime as dt
from types import SimpleNamespace

import main as cli
from src.settings import PublicationSettings, Settings


def test_schedule_uses_publication_settings(monkeypatch, capsys):
    settings = Settings(
        publication=PublicationSettings(
            timezone="Asia/Tokyo",
            production_days=(5,),
            publish_days=(5,),
            publish_times=(dt.time(1, 20), dt.time(2, 15)),
            videos_per_day=1,
            queue_limit=4,
            min_lead_minutes=45,
        )
    )
    orch = SimpleNamespace(settings=settings, close=lambda: None)

    # Quinta 16:00 UTC e sexta 01:00 em Tokio; o lead time elimina o primeiro slot.
    now = dt.datetime(2026, 11, 19, 16, tzinfo=dt.timezone.utc)

    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now.replace(tzinfo=None) if tz is None else now.astimezone(tz)

    with monkeypatch.context() as patch:
        patch.setattr(cli, "Orchestrator", lambda: orch)
        patch.setattr(dt, "datetime", FrozenDateTime)
        assert cli.main(["schedule"]) == 0

    assert capsys.readouterr().out == (
        "Dia de produção: 2026-11-20 (fuso Asia/Tokyo)\n"
        "Slots configurados: 01:20:00, 02:15:00\n"
        "Limite: 1 vídeos/dia | Fila: 4 pendentes\n"
        "Próximo slot disponível: 2026-11-19T17:15:00+00:00 UTC\n"
    )
