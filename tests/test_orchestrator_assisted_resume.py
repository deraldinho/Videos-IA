"""Regressao da retomada assisted com mesmo execution_id.

O contrato: quando o roteiro ja existe gravado no video (operador devolveu o
JSON entre chamadas), generate() nao deve reconsultar o provedor nem falhar
ao ler o registro persistido.

Defeito: src/orchestrator.py:193 chama row.get("script_json") sobre um
sqlite3.Row, que nao tem metodo .get(), lancando AttributeError.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from src.orchestrator import Orchestrator
from src.settings import load_settings


def _tmp_settings(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: REVIEW\nniche: teste\nscript_kind: factual\n"
        "video:\n  min_seconds: 30\n  max_seconds: 60\n  target_seconds: 45\n  ffmpeg: ffmpeg\n  ffprobe: ffprobe\n  node: node\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    return load_settings(cfg, env)


SCRIPT_DATA = {
    "topic": "teste",
    "template": "curiosidade",
    "kind": "ficcao",
    "title": "Titulo",
    "description": "Descricao",
    "hashtags": ["#ia", "#teste", "#automacao"],
    "scenes": [
        {"role": "gancho", "seconds": 3.0, "heading": "H",
         "narration": ["Oi."], "source_ids": []},
        {"role": "corpo", "seconds": 20.0, "heading": "H",
         "narration": ["Texto."], "source_ids": []},
        {"role": "cta", "seconds": 7.0, "heading": "H",
         "narration": ["Tchau."], "source_ids": []},
    ],
}


class _FakeProvider:
    """Provedor que retorna o mesmo roteiro (simula resposta do operador ja lida)."""

    def complete(self, prompt):
        return dict(SCRIPT_DATA)


def test_generate_retomada_assisted_nao_reconsulta_provedor(monkeypatch, tmp_path):
    settings = _tmp_settings(tmp_path)
    db_path = tmp_path / "v.sqlite3"
    orch = Orchestrator(settings=settings, db_path=db_path)
    try:
        # Video ja existe, em SCRIPT_READY, com roteiro gravado pelo operador.
        orch.db.create_video(
            video_id="exec-resume", execution_id="exec-resume",
            topic="t", template="curiosidade", kind="factual", title="T",
            description="D", hashtags=[], script_json={"topic": "x"},
            total_seconds=10.0,
        )
        with orch.db.transaction() as conn:
            conn.execute(
                "UPDATE videos SET status = 'SCRIPT_READY' WHERE video_id = ?",
                ("exec-resume",),
            )

        # Simula o provedor ja tendo a resposta (operador devolveu JSON).
        monkeypatch.setattr(
            "src.orchestrator.build_provider",
            lambda *a, **k: _FakeProvider(),
        )

        # Segunda chamada com o mesmo execution_id: idempotente,
        # sem reconsultar o provedor e sem AttributeError no sqlite3.Row.
        res = orch.generate(execution_id="exec-resume", topic="qualquer",
                            kind="ficcao")

        assert res.status == "SCRIPT_READY"
        assert res.changed is False
        assert "já gravado" in res.note
    finally:
        orch.close()


__all__ = []
