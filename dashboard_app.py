"""Dashboard UI para TikTok AI Autopilot - Interface web de monitoramento e controle."""
from __future__ import annotations

from flask import Flask, render_template_string, request, jsonify
from src.orchestrator import Orchestrator
from src.metrics import metrics
from src.settings import load_settings

app = Flask(__name__)
orch = Orchestrator()
settings = load_settings()

# Instância singleton de métricas (já criada em src.metrics)
metrics_obj = metrics


@app.route("/")
def index():
    """Página inicial com overview do sistema."""
    videos_by_status = {}
    for status in [
        "IDEA_CREATED",
        "SCRIPT_READY",
        "RENDERING",
        "RENDERED",
        "VALIDATED",
        "AWAITING_APPROVAL",
        "APPROVED",
        "UPLOADING",
        "UPLOADED",
        "PUBLISHING",
        "PUBLISHED",
    ]:
        videos = orch.list_by_status(status)
        if videos:
            videos_by_status[status] = videos

    s = metrics_obj.snapshot()
    counters = s.get("counters", {})

    # Contadores de erro
    errors_count = (
        counters.get("errors_script", 0)
        + counters.get("errors_tts", 0)
        + counters.get("errors_render", 0)
        + counters.get("errors_validate", 0)
    )

    return render_template_string(
        """<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TikTok AI Autopilot - Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
      :root {
        --tailwind-text: #e8e8f0;
        --tailwind-bg: #0a0a0f;
        --tailwind-card: #12121a;
        --tailwind-border: #2a2a3a;
        --tailwind-primary: #3b82f6;
      }
      body { font-family: system-ui, -apple-system, sans-serif; background: var(--tailwind-bg); color: var(--tailwind-text); }
      .card { background: var(--tailwind-card); border: 1px solid var(--tailwind-border); border-radius: 8px; }
      .btn { padding: 6px 12px; border-radius: 4px; font-size: 14px; }
      .btn-secondary { background: var(--tailwind-border); color: var(--tailwind-text); border: none; }
      .btn-outline { background: transparent; color: var(--tailwind-text); border: 1px solid var(--tailwind-border); }
      .text-muted-foreground { color: #6b7280; }
      .mt-4 { margin-top: 1rem; }
      .grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid-cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    </style>
</head>
<body class="min-h-screen">
    <nav class="border-b border-[--tailwind-border] bg-[--tailwind-bg] shadow-sm">
      <div class="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded bg-[--tailwind-card] flex items-center justify-center">
            <svg class="w-5 h-5 text-[--tailwind-text]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h1m5-3h1m-5-3h1M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
            </svg>
          </div>
          <div>
            <h1 class="text-xl font-bold">TikTok AI Autopilot</h1>
            <p class="text-sm text-muted-foreground">Modo: {{ mode }}</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <a href="/metrics" class="text-sm text-[--tailwind-text] hover:text-primary">Métricas</a>
          <a href="/health" class="text-sm text-[--tailwind-text] hover:text-primary">Health</a>
        </div>
      </div>
    </nav>

    <main class="max-w-7xl mx-auto py-6 px-6">
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
        <div class="card bg-[--tailwind-card] border --tailwind-border rounded-xl p-6">
          <h3 class="font-semibold text-primary mb-2">Vídeos por Status</h3>
          <p class="text-sm text-muted-foreground mb-4">{{ total_videos }} total</p>
          <div class="space-y-2">
            {% for status, videos in videos_by_status.items() %}
            <div class="flex items-center gap-2 text-sm">
              <span class="px-2 py-1 rounded text-xs font-medium bg-[--tailwind-border] text-[--tailwind-text]">{{ status }}</span>
              <span>{{ videos|length }}</span>
            </div>
            {% endfor %}
          </div>
        </div>

        <div class="card bg-[--tailwind-card] border --tailwind-border rounded-xl p-6">
          <h3 class="font-semibold text-primary mb-2">Métricas Principais</h3>
          <p class="text-sm text-muted-foreground mb-4">Ticks totais: {{ ticks_total }}</p>
          <p class="text-sm text-muted-foreground">Uptime: {{ uptime }}s</p>
          <p class="text-sm text-muted-foreground">Erros por estágio: {{ errors_count }} estágios</p>
        </div>

        <div class="card bg-[--tailwind-card] border --tailwind-border rounded-xl p-6">
          <h3 class="font-semibold text-primary mb-2">Configuração</h3>
          <p class="text-sm text-muted-foreground">Modo: {{ mode }}</p>
          <p class="text-sm text-muted-foreground">Live: {{ live }}</p>
          <p class="text-sm text-muted-foreground">Auto: {{ auto }}</p>
        </div>
      </div>

      <div class="card bg-[--tailwind-card] border --tailwind-border rounded-xl p-6">
        <h3 class="font-semibold text-primary mb-2">Ações Rápidas</h3>
        <div class="grid grid-cols-2 gap-3 mt-4">
          {% for vid in recent_videos[:3] %}
          <form action="/advance/{{ vid }}/script" method="POST" style="display:inline">
            <button type="submit" class="btn btn-secondary text-sm">Próximo Script</button>
          </form>
          {% endfor %}
        </div>
      </div>
    </main>

    <footer class="border-t border-[--tailwind-border] pt-6 text-xs text-muted-foreground">
      <div class="max-w-7xl mx-auto px-6">
        <p>TikTok AI Autopilot — Dashboard de Monitoramento</p>
      </div>
    </footer>
</body>
</html>""",
        mode=settings.mode,
        live="Sim" if settings.live_enabled else "Não",
        auto="Sim" if settings.auto_enabled else "Não",
        total_videos=sum(len(orch.list_by_status(s)) for s in [
            "IDEA_CREATED",
            "SCRIPT_READY",
            "RENDERING",
            "RENDERED",
            "VALIDATED",
            "AWAITING_APPROVAL",
            "APPROVED",
            "UPLOADING",
            "UPLOADED",
            "PUBLISHING",
            "PUBLISHED",
        ]),
        ticks_total=metrics_obj.snapshot().get("counters", {}).get("ticks_total", 0),
        errors_count=errors_count,
        recent_videos=[
            v["video_id"]
            for v in orch.list_by_status("SCRIPT_READY")[:3]
        ]
        + [v["video_id"] for v in orch.list_by_status("AWAITING_APPROVAL")[:3]],
        videos_by_status=videos_by_status,
    )


@app.route("/health")
def health():
    """Health check endpoint."""
    from src.health import run_health_checks
    report = run_health_checks()
    return jsonify(report.to_dict())


@app.route("/metrics")
def metrics_endpoint():
    """Metrics endpoint."""
    return jsonify(metrics_obj.snapshot())


@app.route("/status/<video_id>")
def status_endpoint(video_id):
    """Check video status endpoint."""
    row = orch.db.get_video(video_id)
    if not row:
        return jsonify({"error": "Video not found"}), 404

    current = row["status"]
    allowed = ALLOWED_TRANSITIONS.get(current, set())

    return jsonify(
        {
            "video_id": video_id,
            "current_status": current,
            "allowed_next": list(allowed),
            "config": {
                "mode": settings.mode,
                "live_enabled": settings.live_enabled,
                "auto_enabled": settings.auto_enabled,
            },
        }
    )


# State transitions via POST
ALLOWED_TRANSITIONS = {
    "IDEA_CREATED": {"SCRIPT_READY", "FAILED"},
    "SCRIPT_READY": {"FAILED", "RENDERING"},
    "RENDERING": {"FAILED", "RENDERED"},
    "RENDERED": {"FAILED", "VALIDATED"},
    "VALIDATED": {"FAILED", "AWAITING_APPROVAL"},
    "AWAITING_APPROVAL": {"FAILED", "APPROVED"},
    "APPROVED": {"UPLOADING", "FAILED"},
    "UPLOADING": {"FAILED", "UPLOADED"},
    "UPLOADED": {"QUEUED", "FAILED"},
    "QUEUED": {"FAILED", "PUBLISHING"},
    "PUBLISHING": {"FAILED", "ACTION_REQUIRED", "PUBLISHING"},
    "PUBLISHED": frozenset(),
    "ACTION_REQUIRED": {"FAILED", "PUBLISHING"},
    "FAILED": {"QUEUED", "UPLOADING", "PUBLISHING", "RENDERING"},
}


@app.route("/advance/<video_id>/<stage>", methods=["POST"])
def advance(video_id, stage):
    """Advance video to next stage."""
    try:
        stage_map = {
            "script": "script",
            "tts": "tts",
            "render": "render",
            "validate": "validate",
            "approve": "approve",
            "upload": "upload",
            "publish": "publish",
        }
        stage_name = stage_map.get(stage)
        if stage_name is None:
            return jsonify({"error": f"Unknown stage: {stage}"}), 400

        res = orch.run_stage(video_id, stage_name)
        return jsonify(
            {
                "success": True,
                "video_id": video_id,
                "new_stage": res.stage,
                "new_status": res.status,
                "note": res.note,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)