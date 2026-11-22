"""Agendamento de slots de producao e publicacao.

Responsabilidades:
- valid_production_day: informa se a data local de ``now`` eh um dia de
  producao valido (dia da semana dentro de ``production_days``), opcionalmente
  verificando se o horario de producao ja foi atingido (``if_due``).
- next_slot: calcula o próximo horario de publicacao disponível (UTC),
  respeitando fuso horario, dias/horarios de publicacao, lead time, limite
  diario de videos, janela de horizonte e horarios ja ocupados (mesmo
  fora da janela contam para o limite diario).

Todas as funcoes sao puras (nao gravam no banco de dados); o limite diario real
eh aplicado pelo banco de dados/pipeline. "PublicationSettings" e usado apenas
para anotacao de tipo (sob TYPE_CHECKING); em tempo de execucao as funcoes
fazem duck typing nos atributos de configuracao.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Iterable, TYPE_CHECKING

from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from src.settings import PublicationSettings


def _tz(settings: "PublicationSettings") -> ZoneInfo:
    """Retorna o ZoneInfo configurado, aceitando str, ZoneInfo ou propriedade .tz."""
    try:
        tz = getattr(settings, "tz", None)
    except Exception:
        # settings.tz pode falhar quando .timezone ja e um ZoneInfo (bug do settings).
        tz = None
    if isinstance(tz, ZoneInfo):
        return tz
    if isinstance(tz, str):
        return ZoneInfo(tz)
    return ZoneInfo(str(settings.timezone))


def _to_utc(value: datetime) -> datetime:
    """Normaliza um datetime para UTC (naive eh interpretado como UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_date(value: datetime, tz: ZoneInfo):
    """Data local de um datetime (naive interpretado como UTC)."""
    return _to_utc(value).astimezone(tz).date()


def valid_production_day(
    settings: "PublicationSettings",
    now: datetime,
    *,
    if_due: bool = False,
) -> str | None:
    """Retorna a data local ISO se hoje for dia de producao valido.

    Converte ``now`` para o fuso configurado. Se o dia da semana (ISO,
    1=segunda .. 7=domingo) estiver em ``production_days``, retorna a data
    local no formato ISO ("YYYY-MM-DD"). Com ``if_due=True``, retorna apenas
    se o horario local ja chegou a ``production_time``. Retorna ``None``
    quando nao permitido.
    """
    tz = _tz(settings)
    local = _to_utc(now).astimezone(tz)

    if local.isoweekday() not in settings.production_days:
        return None

    if if_due:
        pt = settings.production_time
        if (local.hour, local.minute) < (pt.hour, pt.minute):
            return None

    return local.date().isoformat()


def next_slot(
    settings: "PublicationSettings",
    now: datetime,
    unavailable: Iterable[datetime] = (),
) -> datetime | None:
    """Calcula o próximo slot de publicacao disponível (UTC).

    Args:
        settings: configuracao de publicacao (duck-tipada; veja
            PublicationSettings).
        now: instante de referencia (naive interpretado como UTC).
        unavailable: horarios ja ocupados (remotos). Horarios fora da janela
            de publicacao contam para o limite diario.

    Returns:
        O próximo horario disponível como datetime aware em UTC, ou ``None``
        se nao houver slot dentro do horizonte.
    """
    tz = _tz(settings)
    now_utc = _to_utc(now)
    now_local = now_utc.astimezone(tz)

    lead = timedelta(minutes=int(getattr(settings, "min_lead_minutes", 0)))
    earliest_utc = (now_local + lead).astimezone(timezone.utc)

    videos_per_day = int(getattr(settings, "videos_per_day", 1))
    horizon_days = int(getattr(settings, "horizon_days", 7))
    publish_days = tuple(getattr(settings, "publish_days", (1, 2, 3, 4, 5, 6, 7)))
    publish_times = tuple(getattr(settings, "publish_times", ()))

    # Horarios ocupados: contagem por data local (limite diario) e instantes
    # UTC (detecao de duplicidade). Horarios fora da janela contam para o cap.
    unavailable_local_dates: dict = {}
    unavailable_utc: set = set()
    for value in unavailable:
        utc = _to_utc(value)
        unavailable_utc.add(utc)
        local_naive = utc.astimezone(tz).replace(tzinfo=None)
        folded = local_naive.replace(tzinfo=tz)
        # Offset do instante original (antes de normalizar para UTC).
        source_offset = value.utcoffset() if value.tzinfo is not None else timedelta(0)
        # Pula instantes que nao existem no fuso (gap do horario de verao).
        # Ambiguidades (overlap) sao normalizadas ao mesmo instante UTC de origem.
        if folded.utcoffset() != source_offset:
            continue
        ld = folded.date()
        unavailable_local_dates[ld] = unavailable_local_dates.get(ld, 0) + 1

    start_date = now_local.date()
    # Inclui o ultimo dia do horizonte (dia 0 + horizon_days).
    for offset in range(horizon_days + 1):
        day = start_date + timedelta(days=offset)
        if day.isoweekday() not in publish_days:
            continue
        used_today = unavailable_local_dates.get(day, 0)
        if used_today >= videos_per_day:
            continue  # dia ja atingiu o limite diario
        for pt in publish_times:
            candidate_local = datetime.combine(day, pt, tzinfo=tz)
            candidate_utc = candidate_local.astimezone(timezone.utc)
            # Pula horario local invalido (gap de horario de verao).
            if candidate_utc.astimezone(tz).utcoffset() != candidate_local.utcoffset():
                continue
            if candidate_utc < earliest_utc:
                continue  # ainda dentro do lead time
            if candidate_utc in unavailable_utc:
                continue  # nao duplica horario ja ocupado
            return candidate_utc
    return None


__all__ = ["valid_production_day", "next_slot"]
