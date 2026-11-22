"""Entrypoint CLI do TikTok AI Autopilot.

Comandos:
  python main.py generate          # cria vídeo + roteiro (modo assisted/NVIDIA)
  python main.py review            # lista vídeos aguardando aprovação
  python main.py approve <id>      # aprova um vídeo para publicação
  python main.py schedule          # calcula próximos slots de publicação
  python main.py status [<id>]     # status de um vídeo ou da fila
  python main.py run-daily         # executa o pipeline diário (tick do agendador)
  python main.py --help            # esta ajuda

Modos: DRY_RUN | REVIEW (padrão) | AUTO (requer auto_enabled: true).
Nenhuma publicação real ocorre sem live_enabled: true + aprovação explícita.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Garante import de src.* independentemente do cwd.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.settings import load_settings, require_cloudinary_credentials, require_buffer_credentials  # noqa: E402
from src.database import VIDEO_STATES  # noqa: E402
from src.orchestrator import Orchestrator, STAGE_ORDER  # noqa: E402
from src.scheduler import next_slot, valid_production_day  # noqa: E402
from src.errors import PipelineError  # noqa: E402
from src.logging import setup_logging, get_logger  # noqa: E402
from src.health import run_health_checks  # noqa: E402
from src.dashboard import generate_dashboard  # noqa: E402
from src.metrics import metrics, increment_tick, observe_duration  # noqa: E402

# Logger da CLI.
logger = get_logger("cli")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="TikTok AI Autopilot — pipeline de automação de vídeos curtos (PT-BR).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # generate
    g = sub.add_parser("generate", help="Gera um novo vídeo e roteiro.")
    g.add_argument("--topic", help="Tema do vídeo (opcional).")
    g.add_argument("--kind", choices=["factual", "ficcao"], help="Tipo de roteiro.")
    g.add_argument("--template", choices=["curiosidade", "lista", "historia", "explicacao"], help="Template.")
    g.add_argument("--target-seconds", type=int, help="Duração alvo em segundos.")
    g.add_argument("--execution-id", help="ID de execução (para retomada idempotente).")

    # review
    sub.add_parser("review", help="Lista vídeos aguardando aprovação (AWAITING_APPROVAL).")

    # approve
    a = sub.add_parser("approve", help="Aprova um vídeo para publicação.")
    a.add_argument("video_id", help="ID do vídeo a aprovar.")

    # schedule
    sub.add_parser("schedule", help="Mostra próximos slots de publicação disponíveis.")

    # status
    s = sub.add_parser("status", help="Status de um vídeo ou da fila.")
    s.add_argument("video_id", nargs="?", help="ID do vídeo (opcional; sem ele mostra a fila).")

    # run-daily
    r = sub.add_parser("run-daily", help="Executa o pipeline diário (produção + publicação).")

    # tick (alias de run-daily, usado pelo agendador do Windows/cron)
    t = sub.add_parser("tick", help="Executa um tick do agendador (mesmo que run-daily).")

    # health
    sub.add_parser("health", help="Executa health checks do sistema (FFmpeg, banco, credenciais).")

    # dashboard
    d = sub.add_parser("dashboard", help="Gera o dashboard administrativo HTML estático.")
    d.add_argument("--output", default="output/dashboard.html", help="Caminho do arquivo HTML de saída.")

    # metrics
    sub.add_parser("metrics", help="Mostra métricas operacionais coletadas em memória.")

    return p


def cmd_generate(orch: Orchestrator, args: argparse.Namespace) -> int:
    try:
        res = orch.generate(
            topic=args.topic,
            kind=args.kind,
            template=args.template,
            target_seconds=args.target_seconds,
            execution_id=args.execution_id,
        )
    except PipelineError as exc:
        print(f"ERRO: {exc}")
        return 1
    print(f"Execução: {res.execution_id}")
    print(f"Vídeo: {res.video_id}")
    print(f"Etapa: {res.stage} | Status: {res.status}")
    print(f"Nota: {res.note}")
    return 0


def cmd_review(orch: Orchestrator, _: argparse.Namespace) -> int:
    rows = orch.list_by_status("AWAITING_APPROVAL")
    if not rows:
        print("Nenhum vídeo aguardando aprovação.")
        return 0
    print(f"{'Vídeo':<20} {'Título':<40} {'Criado em'}")
    print("-" * 80)
    for r in rows:
        print(f"{r['video_id']:<20} {str(r['title'])[:40]:<40} {r['created_at']}")
    return 0


def cmd_approve(orch: Orchestrator, args: argparse.Namespace) -> int:
    settings = orch.settings
    if settings.mode == "REVIEW" and not settings.live_enabled:
        print("Aviso: modo REVIEW com live_enabled=false — aprovação registrada, mas sem publicação real.")
    row = orch.db.get_video(args.video_id)
    if row is None:
        print(f"ERRO: vídeo não encontrado: {args.video_id}")
        return 1
    if row["status"] != "AWAITING_APPROVAL":
        print(f"ERRO: vídeo {args.video_id} não está em AWAITING_APPROVAL (atual: {row['status']}).")
        return 1
    try:
        res = orch.sm.advance(args.video_id, "APPROVED", actor="operador",
                              note="aprovado via CLI")
    except PipelineError as exc:
        print(f"ERRO: {exc}")
        return 1
    print(f"Vídeo {args.video_id} aprovado (APPROVED).")
    print(f"Transição: {res.from_status} -> {res.to_status}")
    return 0


def cmd_schedule(orch: Orchestrator, _: argparse.Namespace) -> int:
    settings = orch.settings
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    prod_day = valid_production_day(settings, now)
    if prod_day is None:
        print("Hoje não é um dia de produção (fora de production_days).")
        return 0
    print(f"Dia de produção: {prod_day} (fuso {settings.publication.timezone})")
    print(f"Slots configurados: {', '.join(str(t) for t in settings.publication.publish_times)}")
    print(f"Limite: {settings.publication.videos_per_day} vídeos/dia | Fila: {settings.publication.queue_limit} pendentes")
    slot = next_slot(settings, now)
    if slot:
        print(f"Próximo slot disponível: {slot.isoformat()} UTC")
    else:
        print("Nenhum slot disponível dentro do horizonte configurado.")
    return 0


def cmd_status(orch: Orchestrator, args: argparse.Namespace) -> int:
    if args.video_id:
        info = orch.status(args.video_id)
        if not info:
            print(f"ERRO: vídeo não encontrado: {args.video_id}")
            return 1
        print(f"Vídeo: {info['video_id']}")
        print(f"Status: {info['status']}")
        print(f"Tópico: {info['topic']} | Template: {info['template']} | Tipo: {info['kind']}")
        print(f"Título: {info['title']}")
        print(f"Duração alvo: {info['total_seconds']}s")
        if info["error_message"]:
            print(f"Erro: {info['error_message']} (tentativa {info['retry_count']})")
        if info["cloudinary_url"]:
            print(f"Cloudinary: {info['cloudinary_url']}")
        if info["buffer_post_id"]:
            print(f"Buffer post: {info['buffer_post_id']} ({info['buffer_post_state']})")
        return 0
    # Fila geral
    print("Filas por status:")
    for status in VIDEO_STATES:
        rows = orch.list_by_status(status)
        if rows:
            print(f"  {status:<20} {len(rows)} vídeo(s)")
            for r in rows[:5]:
                print(f"    - {r['video_id']:<20} {str(r['title'])[:40]}")
    return 0


def cmd_run_daily(orch: Orchestrator, _: argparse.Namespace) -> int:
    """Executa o pipeline diário: processa vídeos em AWAITING_APPROVAL (se AUTO)
    e avança os que estão em estágios anteriores."""
    settings = orch.settings
    if settings.mode == "AUTO" and not settings.auto_enabled:
        print("ERRO: modo AUTO requer auto_enabled: true no config.yaml.")
        return 1

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    prod_day = valid_production_day(settings, now)
    if prod_day is None:
        print("Hoje não é dia de produção. Nenhuma ação executada.")
        return 0

    # Avança vídeos pendentes em estágios iniciais (até APPROVED se AUTO).
    pending = orch.list_by_status("SCRIPT_READY") + orch.list_by_status("RENDERING") + \
              orch.list_by_status("RENDERED") + orch.list_by_status("VALIDATED")
    for row in pending:
        vid = row["video_id"]
        current = row["status"]
        try:
            if current == "SCRIPT_READY":
                orch.run_stage(vid, "tts")
            elif current == "RENDERING":
                orch.run_stage(vid, "render")
            elif current == "RENDERED":
                orch.run_stage(vid, "validate")
            elif current == "VALIDATED":
                if settings.mode == "AUTO":
                    # Passa para AWAITING_APPROVAL então APPROVED automaticamente
                    orch.sm.advance(vid, "AWAITING_APPROVAL", actor="system", note="validado")
                    if settings.auto_enabled:
                        orch.sm.advance(vid, "APPROVED", actor="system", note="aprovado automático (AUTO)")
                else:
                    orch.sm.advance(vid, "AWAITING_APPROVAL", actor="system", note="validado, aguardando aprovação")
        except PipelineError as exc:
            print(f"Aviso: não foi possível avançar {vid}: {exc}")

    # Se AUTO, também publica os APPROVED que ainda não foram para o Cloudinary.
    if settings.mode == "AUTO" and settings.auto_enabled:
        approved = orch.list_by_status("APPROVED")
        for row in approved:
            vid = row["video_id"]
            try:
                orch.run_stage(vid, "upload")
                orch.run_stage(vid, "publish")
            except PipelineError as exc:
                print(f"Aviso: não foi publicar {vid}: {exc}")

    print("Run-daily concluído.")
    return 0


def cmd_tick(orch: Orchestrator, _: argparse.Namespace) -> int:
    """Tick do agendador: executa o pipeline diário com métricas e health checks.

    Executa o mesmo que run-daily, mas com:
    - Rastreamento de contadores de métricas (increment_tick)
    - Health checks before/after via run_health_checks
    - Medição de tempo e logging estruturado
    - Códigos de retorno baseados na saúde do sistema
    """
    # Incrementa contador de ticks antes da execução
    increment_tick()

    start = __import__("time").time()
    try:
        # Executa o pipeline diário
        result = cmd_run_daily(orch, _)
        duration = __import__("time").time() - start

        # Registra métricas de duração
        observe_duration("tick", duration)

        # Executa health checks pós-execução e reporta
        from src.health import run_health_checks
        health_report = run_health_checks()
        if not health_report.healthy:
            print("Aviso: Health checks falharam após a execução do tick.")
            return 1

        print(f"Tick executado em {duration:.1f}s.")
        return result
    except Exception as exc:
        duration = __import__("time").time() - start
        observe_duration("tick_error", 1.0)
        print(f"ERRO no tick após {duration:.1f}s: {exc}")
        return 1


def cmd_health(_: Orchestrator, __: argparse.Namespace) -> int:
    """Executa health checks do sistema."""
    report = run_health_checks()
    data = report.to_dict()
    status = "SAUDÁVEL" if data["healthy"] else "DEGRADADO"
    print(f"Health check: {status}")
    for c in data["checks"]:
        mark = "[OK]" if c["healthy"] else "[FALHA]"
        lat = f" ({c['latency_ms']:.0f}ms)" if c["latency_ms"] is not None else ""
        print(f"  {mark} {c['name']}: {c['detail']}{lat}")
    return 0 if data["healthy"] else 1


def cmd_dashboard(_: Orchestrator, args: argparse.Namespace) -> int:
    """Gera o dashboard administrativo HTML estático."""
    path = generate_dashboard(output=args.output)
    print(f"Dashboard gerado: {path}")
    return 0


def cmd_metrics(_: Orchestrator, __: argparse.Namespace) -> int:
    """Mostra métricas operacionais."""
    snap = metrics.snapshot()
    print(f"Uptime: {snap.get('uptime_seconds', 0):.1f}s")
    if snap.get("counters"):
        print("Contadores:")
        for k, v in snap["counters"].items():
            print(f"  {k}: {v}")
    if snap.get("gauges"):
        print("Gauges:")
        for k, v in snap["gauges"].items():
            print(f"  {k}: {v}")
    return 0


COMMANDS = {
    "generate": cmd_generate,
    "review": cmd_review,
    "approve": cmd_approve,
    "schedule": cmd_schedule,
    "status": cmd_status,
    "run-daily": cmd_run_daily,
    "tick": cmd_tick,
    "health": cmd_health,
    "dashboard": cmd_dashboard,
    "metrics": cmd_metrics,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    orch = Orchestrator()
    try:
        return COMMANDS[args.command](orch, args)
    finally:
        orch.close()


if __name__ == "__main__":
    sys.exit(main())
