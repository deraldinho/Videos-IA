"""Regressao do comando run-daily do CLI.

cmd_run_daily passa o objeto Settings inteiro para valid_production_day(),
que espera um PublicationSettings e acessa production_days diretamente,
causando AttributeError: 'Settings' object has no attribute 'timezone'.
"""

import datetime as dt
from types import SimpleNamespace

import main as cli
from src.settings import PublicationSettings, Settings


def test_run_daily_uses_publication_settings(monkeypatch, capsys):
    settings = Settings(
        publication=PublicationSettings(
            timezone="America/Sao_Paulo",
            production_days=(5,),
            publish_days=(1, 2, 3, 4, 5, 6, 7),
            publish_times=(dt.time(12, 0), dt.time(19, 0)),
            videos_per_day=2,
            queue_limit=10,
            min_lead_minutes=30,
        )
    )
    orch = SimpleNamespace(
        settings=settings,
        list_by_status=lambda s: [],
        close=lambda: None,
    )

    # Quinta-feira: nao e dia de producao (sexta = 5), entao valid_production_day
    # deve retornar None e o comando encerra com 0, sem AttributeError.
    now = dt.datetime(2026, 11, 19, 16, tzinfo=dt.timezone.utc)

    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now.replace(tzinfo=None) if tz is None else now.astimezone(tz)

    with monkeypatch.context() as patch:
        patch.setattr(cli, "Orchestrator", lambda: orch)
        patch.setattr(dt, "datetime", FrozenDateTime)
        assert cli.main(["run-daily"]) == 0

    out = capsys.readouterr().out
    assert "Hoje não é dia de produção" in out
    assert "Nenhuma ação executada." in out
