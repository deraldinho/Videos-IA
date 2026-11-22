"""Dashboard administrativo simples (HTML estático + dados JSON).

Exibe a fila de vídeos por status, métricas, health check e últimos erros.
Não depende de servidor web externo: gera um arquivo HTML estático que pode
ser aberto no navegador ou servido por qualquer servidor HTTP simples.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.database import Database, DEFAULT_DB_PATH, VIDEO_STATES
from src.health import run_health_checks
from src.metrics import metrics


def _status_counts(db: Database) -> dict[str, int]:
    counts: dict[str, int] = {status: 0 for status in VIDEO_STATES}
    for status in VIDEO_STATES:
        rows = db.list_by_status(status)
        counts[status] = len(rows)
    return counts


def _recent_videos(db: Database, limit: int = 10) -> list[dict[str, Any]]:
    rows = []
    with db.transaction() as conn:
        for status in VIDEO_STATES:
            for r in conn.execute(
                "SELECT video_id, topic, status, created_at, updated_at, error_message, retry_count "
                "FROM videos WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ):
                rows.append(dict(r))
    rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
    return rows[:limit]


def _recent_errors(db: Database, limit: int = 10) -> list[dict[str, Any]]:
    rows = []
    with db.transaction() as conn:
        for r in conn.execute(
            "SELECT video_id, from_status, to_status, actor, note, created_at "
            "FROM video_history WHERE note LIKE 'erro:%' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ):
            rows.append(dict(r))
    return rows


def generate_dashboard(
    db_path: Path | str = DEFAULT_DB_PATH,
    output: Path | str = "output/dashboard.html",
) -> Path:
    """Gera o dashboard HTML estático e devolve o caminho do arquivo."""
    db = Database(db_path)
    status_counts = _status_counts(db)
    recent_videos = _recent_videos(db)
    recent_errors = _recent_errors(db)
    health = run_health_checks(db_path)
    metrics_snapshot = metrics.snapshot()

    total_videos = sum(status_counts.values())
    published = status_counts.get("PUBLISHED", 0)
    failed = status_counts.get("FAILED", 0)
    awaiting = status_counts.get("AWAITING_APPROVAL", 0)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TikTok AI Autopilot — Dashboard</title>
<style>
  body {{ font-family: system-ui, -apple-family, Segoe UI, Roboto, sans-serif; margin: 24px; background: #f5f5f5; color: #111; }}
  h1 {{ font-size: 24px; margin-bottom: 8px; }}
  .subtitle {{ color: #666; font-size: 14px; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .card h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: #666; margin: 0 0 10px 0; }}
  .big {{ font-size: 32px; font-weight: 700; color: #111; }}
  .delta {{ font-size: 13px; color: #666; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th {{ background: #f2f2f2; text-align: left; padding: 10px 12px; font-size: 12px; text-transform: uppercase; color: #666; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 13px; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover {{ background: #fafafa; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
  .badge-ok {{ background: #e7f7ed; color: #1a7a39; }}
  .badge-warn {{ background: #fdf3e3; color: #9a6200; }}
  .badge-err {{ background: #fdecea; color: #b3261e; }}
  .health-ok {{ color: #1a7a39; }}
  .health-err {{ color: #b3261e; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  .section {{ margin-bottom: 24px; }}
  .section h3 {{ font-size: 16px; margin-bottom: 8px; color: #333; }}
  .muted {{ color: #888; font-size: 12px; }}
</style>
</head>
<body>
<h1>TikTok AI Autopilot</h1>
<p class="subtitle">Dashboard administrativo — fila de vídeos, métricas e saúde do sistema</p>

<div class="grid">
  <div class="card">
    <h2>Total de vídeos</h2>
    <div class="big">{total_videos}</div>
    <div class="delta">todos os status</div>
  </div>
  <div class="card">
    <h2>Publicados</h2>
    <div class="big">{published}</div>
    <div class="delta">status PUBLISHED</div>
  </div>
  <div class="card">
    <h2>Aguardando aprovação</h2>
    <div class="big">{awaiting}</div>
    <div class="delta">status AWAITING_APPROVAL</div>
  </div>
  <div class="card">
    <h2>Falhas</h2>
    <div class="big">{failed}</div>
    <div class="delta">status FAILED</div>
  </div>
</div>

<div class="section">
  <h3>Vídeos por status</h3>
  <table>
    <thead><tr><th>Status</th><th>Quantidade</th></tr></thead>
    <tbody>
      {''.join(f"<tr><td>{s}</td><td>{status_counts.get(s, 0)}</td></tr>" for s in VIDEO_STATES if status_counts.get(s, 0))}
    </tbody>
  </table>
</div>

<div class="section">
  <h3>Vídeos recentes</h3>
  <table>
    <thead><tr><th>Vídeo</th><th>Tópico</th><th>Status</th><th>Criado em</th><th>Erros</th></tr></thead>
    <tbody>
      {''.join(
        f"<tr><td class='mono'>{v['video_id']}</td><td>{v['topic'] or '-'}</td>"
        f"<td><span class='badge {'badge-ok' if v['status']=='PUBLISHED' else 'badge-warn' if v['status']=='AWAITING_APPROVAL' else 'badge-err' if v['status']=='FAILED' else ''}'>{v['status']}</span></td>"
        f"<td>{v['created_at'] or '-'}</td><td>{v['retry_count'] or 0}</td></tr>"
        for v in recent_videos
      )}
    </tbody>
  </table>
</div>

<div class="section">
  <h3>Erros recentes</h3>
  <table>
    <thead><tr><th>Vídeo</th><th>De → Para</th><th>Ator</th><th>Nota</th><th>Em</th></tr></thead>
    <tbody>
      {''.join(
        f"<tr><td class='mono'>{e['video_id']}</td><td>{e['from_status']} → {e['to_status']}</td>"
        f"<td>{e['actor']}</td><td>{e['note']}</td><td>{e['created_at']}</td></tr>"
        for e in recent_errors
      ) or "<tr><td colspan='5' class='muted'>Sem erros registrados.</td></tr>"}
    </tbody>
  </table>
</div>

<div class="section">
  <h3>Health check</h3>
  <table>
    <thead><tr><th>Componente</th><th>Saudável</th><th>Detalhe</th><th>Latência</th></tr></thead>
    <tbody>
      {''.join(
        f"<tr><td>{c['name']}</td><td><span class='badge {'badge-ok' if c['healthy'] else 'badge-err'}'>{'ok' if c['healthy'] else 'falha'}</span></td>"
        f"<td>{c['detail']}</td><td class='mono'>{c['latency_ms']:.0f} ms</td></tr>"
        for c in health.to_dict()['checks']
      )}
    </tbody>
  </table>
</div>

<div class="section">
  <h3>Métricas</h3>
  <table>
    <thead><tr><th>Métrica</th><th>Valor</th></tr></thead>
    <tbody>
      {''.join(f"<tr><td class='mono'>{k}</td><td>{v}</td></tr>" for k, v in metrics_snapshot.get('counters', {}).items())}
      {''.join(f"<tr><td class='mono'>{k}</td><td>{v}</td></tr>" for k, v in metrics_snapshot.get('gauges', {}).items())}
      <tr><td class='mono'>uptime_seconds</td><td>{metrics_snapshot.get('uptime_seconds', 0):.1f}</td></tr>
    </tbody>
  </table>
</div>

<p class="muted">Gerado automaticamente pelo TikTok AI Autopilot. Não contém credenciais.</p>
</body>
</html>"""

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


__all__ = ["generate_dashboard"]
