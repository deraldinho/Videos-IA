"""Dashboard de funil de publicação (CP28).

Fornece:

* ``gerar_funil_publicacao(targets)`` — conta PublicationTargets por local_state
  e separa publicados confirmados (remote_status='published' + confirmed_at).
* ``gerar_dashboard_com_funil(targets, output)`` — gera HTML estático com a
  seção de funil de publicação (uploaded → queued → published → failed).

Regras de segurança (AGENTS.md):
- Nunca expor credenciais, tokens ou segredos no HTML.
- Estados simulados (mock) distinguíveis de estados confirmados remotamente.
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.workflow.state import PublicationTarget


# Estados válidos do funil (ordenados do mais antigo para o mais recente)
FUNIL_STATES = ("none", "uploaded", "queued", "publishing", "published", "failed")


def gerar_funil_publicacao(
    targets: list[PublicationTarget] | list[dict[str, Any]],
) -> dict[str, int]:
    """Gera contagem do funil de publicação a partir de targets.

    Args:
        targets: Lista de PublicationTarget ou dicionários (model_dump).

    Returns:
        Dict com contagem por local_state, total, published_confirmed e
        published_not_confirmed.
    """
    funil: dict[str, int] = {s: 0 for s in FUNIL_STATES}
    funil["total"] = 0
    funil["published_confirmed"] = 0
    funil["published_not_confirmed"] = 0

    for t in targets:
        if isinstance(t, dict):
            local_state = t.get("local_state", "none")
            remote_status = t.get("remote_status")
            confirmed_at = t.get("confirmed_at")
        else:
            local_state = t.local_state
            remote_status = t.remote_status
            confirmed_at = t.confirmed_at

        if local_state in funil:
            funil[local_state] += 1
        funil["total"] += 1

        # Publicado confirmado: local_state='published' + remote_status='published' + confirmed_at
        if (
            local_state == "published"
            and remote_status == "published"
            and confirmed_at
        ):
            funil["published_confirmed"] += 1
        elif local_state == "published":
            funil["published_not_confirmed"] += 1

    return funil


def _badges_for_state(state: str) -> str:
    """Retorna classes CSS de badge para um estado do funil."""
    mapping = {
        "none": "badge-warn",
        "uploaded": "badge-warn",
        "queued": "badge-warn",
        "publishing": "badge-warn",
        "published": "badge-ok",
        "failed": "badge-err",
    }
    return mapping.get(state, "badge-warn")


def gerar_dashboard_com_funil(
    targets: list[PublicationTarget] | list[dict[str, Any]],
    output: str | Path = "output/dashboard_funil.html",
) -> Path:
    """Gera dashboard HTML estático com seção de funil de publicação.

    O HTML não contém credenciais, tokens ou segredos. Apenas identificadores
    de vídeo e status do funil.
    """
    funil = gerar_funil_publicacao(targets)

    linhas_targets = ""
    for t in targets:
        if isinstance(t, dict):
            vid = t.get("video_id", "-")
            platform = t.get("platform", "-")
            local_state = t.get("local_state", "-")
            remote_status = t.get("remote_status") or "-"
            confirmed = "Sim" if t.get("confirmed_at") else "Não"
        else:
            vid = t.video_id
            platform = t.platform
            local_state = t.local_state
            remote_status = t.remote_status or "-"
            confirmed = "Sim" if t.confirmed_at else "Não"

        badge = _badges_for_state(local_state)
        linhas_targets += (
            f"<tr><td class='mono'>{vid}</td><td>{platform}</td>"
            f"<td><span class='badge {badge}'>{local_state}</span></td>"
            f"<td>{remote_status}</td><td>{confirmed}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TikTok AI Autopilot — Funil de Publicação</title>
<style>
  body {{ font-family: system-ui, -apple-family, Segoe UI, Roboto, sans-serif; margin: 24px; background: #f5f5f5; color: #111; }}
  h1 {{ font-size: 24px; margin-bottom: 8px; }}
  .subtitle {{ color: #666; font-size: 14px; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .card h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: #666; margin: 0 0 10px 0; }}
  .big {{ font-size: 30px; font-weight: 700; color: #111; }}
  .delta {{ font-size: 12px; color: #666; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th {{ background: #f2f2f2; text-align: left; padding: 10px 12px; font-size: 12px; text-transform: uppercase; color: #666; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 13px; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover {{ background: #fafafa; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
  .badge-ok {{ background: #e7f7ed; color: #1a7a39; }}
  .badge-warn {{ background: #fdf3e3; color: #9a6200; }}
  .badge-err {{ background: #fdecea; color: #b3261e; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  .section {{ margin-bottom: 24px; }}
  .section h3 {{ font-size: 16px; margin-bottom: 8px; color: #333; }}
  .muted {{ color: #888; font-size: 12px; }}
  .arrow {{ color: #999; font-size: 12px; }}
</style>
</head>
<body>
<h1>TikTok AI Autopilot</h1>
<p class="subtitle">Dashboard de funil de publicação — estados locais e confirmação remota</p>

<div class="section">
  <h3>Funil de Publicação</h3>
  <div class="grid">
    <div class="card">
      <h2>Total</h2>
      <div class="big">{{total}}</div>
      <div class="delta">targets no funil</div>
    </div>
    <div class="card">
      <h2>None</h2>
      <div class="big">{{none}}</div>
      <div class="delta">sem upload</div>
    </div>
    <div class="card">
      <h2>Uploaded</h2>
      <div class="big">{{uploaded}}</div>
      <div class="delta">upload Cloudinary</div>
    </div>
    <div class="card">
      <h2>Queued</h2>
      <div class="big">{{queued}}</div>
      <div class="delta">agendado Buffer</div>
    </div>
    <div class="card">
      <h2>Publishing</h2>
      <div class="big">{{publishing}}</div>
      <div class="delta">em publicação</div>
    </div>
    <div class="card">
      <h2>Published</h2>
      <div class="big">{{published}}</div>
      <div class="delta">publicados (locais)</div>
    </div>
    <div class="card">
      <h2>Failed</h2>
      <div class="big">{{failed}}</div>
      <div class="delta">falhas</div>
    </div>
  </div>

  <div class="card" style="margin-top: 16px;">
    <h2>Confirmados remotamente</h2>
    <div class="big">{{published_confirmed}}</div>
    <div class="delta">published + remote_status=published + confirmed_at</div>
  </div>
  <div class="card" style="margin-top: 8px;">
    <h2>Publicados não confirmados</h2>
    <div class="big">{{published_not_confirmed}}</div>
    <div class="delta">sem confirmação remota inequívoca</div>
  </div>
</div>

<div class="section">
  <h3>Detalhe por target</h3>
  <table>
    <thead><tr><th>Vídeo</th><th>Platforma</th><th>Estado local</th><th>Status remoto</th><th>Confirmado</th></tr></thead>
    <tbody>
      {linhas_targets or "<tr><td colspan='5' class='muted'>Nenhum target cadastrado.</td></tr>"}
    </tbody>
  </table>
</div>

<div class="section">
  <h3>Fluxo do funil</h3>
  <div style="display:flex; align-items:center; gap:8px; font-size:13px; color:#555;">
    {' → '.join(f"<span class='badge _badges_for_state(s)'>{s.upper()}</span>" for s in FUNIL_STATES)}
  </div>
  <p class="muted">Estados simulados (mock) são distinguíveis de estados confirmados remotamente (remote_status='published' + confirmed_at).</p>
</div>

<p class="muted">Gerado automaticamente pelo TikTok AI Autopilot. Não contém credenciais.</p>
</body>
</html>""".replace("{{total}}", str(funil["total"])) \
     .replace("{{none}}", str(funil["none"])) \
     .replace("{{uploaded}}", str(funil["uploaded"])) \
     .replace("{{queued}}", str(funil["queued"])) \
     .replace("{{publishing}}", str(funil["publishing"])) \
     .replace("{{published}}", str(funil["published"])) \
     .replace("{{failed}}", str(funil["failed"])) \
     .replace("{{published_confirmed}}", str(funil["published_confirmed"])) \
     .replace("{{published_not_confirmed}}", str(funil["published_not_confirmed"]))

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


__all__ = ["gerar_funil_publicacao", "gerar_dashboard_com_funil", "FUNIL_STATES"]
