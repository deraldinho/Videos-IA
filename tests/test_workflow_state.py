"""Testes dos contratos tipados do estado compartilhado (CP03).

Cobre:
- Criacao de WorkflowState com valores padrao.
- empty_state (IDEA_CREATED).
- Round-trip state_to_dict / dict_to_state (compatibilidade com SqliteSaver).
- mark_updated, add_error, add_cost.
- Validacao de sub-modelos (Script, AudioAsset, VideoAsset, ValidationResult).
- Enums Stage, ApprovalStatus, PublicationStatus.
"""

from __future__ import annotations

import pytest

from src.workflow import (
    ApprovalStatus,
    AudioAsset,
    CostEntry,
    ErrorEntry,
    PublicationStatus,
    Script,
    ScriptScene,
    Stage,
    ValidationResult,
    VideoAsset,
    WorkflowState,
    dict_to_state,
    empty_state,
    state_to_dict,
)


def test_workflow_state_defaults():
    state = WorkflowState(execution_id="exec-1", video_id="v1")
    assert state.execution_id == "exec-1"
    assert state.video_id == "v1"
    assert state.workflow_version == "1.0.0"
    assert state.status == Stage.IDEA_CREATED
    assert state.current_stage == Stage.IDEA_CREATED
    assert state.approval_status == ApprovalStatus.PENDING
    assert state.publication_status == PublicationStatus.NONE
    assert state.script is None
    assert state.audio_assets == []
    assert state.video_assets == []
    assert state.errors == []
    assert state.retries == 0
    assert state.started_at is not None
    assert state.updated_at is not None


def test_empty_state_helpers():
    state = empty_state("exec-x", "vid-x", topic="curiosidade")
    assert state.execution_id == "exec-x"
    assert state.video_id == "vid-x"
    assert state.topic == "curiosidade"
    assert state.status == Stage.IDEA_CREATED
    assert state.current_stage == Stage.IDEA_CREATED


def test_state_round_trip_dict():
    state = empty_state("exec-1", "v1", topic="t")
    state.script = Script(
        topic="t", title="T", description="D",
        scenes=[ScriptScene(role="gancho", seconds=3.0, heading="H",
                            narration=["Oi."], source_ids=["s1"])],
        total_seconds=15.0,
    )
    state.audio_assets.append(
        AudioAsset(scene_role="gancho", index=0, text="Oi.",
                   wav_path="output/drafts/tts/v1_00_gancho_00.wav",
                   duration_seconds=3.0)
    )
    state.video_assets.append(
        VideoAsset(mp4_path="output/drafts/renders/v1.mp4",
                   width=1080, height=1920, fps=30.0,
                   duration_seconds=15.0, has_audio=True, size_bytes=12345)
    )
    state.validation_results = ValidationResult(
        valid=True, width=1080, height=1920, fps=30.0,
        duration_seconds=15.0, has_audio=True,
    )
    state.approval_status = ApprovalStatus.APPROVED
    state.publication_status = PublicationStatus.QUEUED
    state.mark_updated(actor="system", action="advance", note="ok",
                       from_status="IDEA_CREATED", to_status="SCRIPT_READY")
    state.add_error("render", "ffmpeg timeout")
    state.add_cost("VoiceAgent", "tts", tokens=120, cost_usd=0.002)

    data = state_to_dict(state)
    assert isinstance(data, dict)
    assert data["execution_id"] == "exec-1"
    assert data["script"]["title"] == "T"
    assert data["audio_assets"][0]["wav_path"].endswith(".wav")
    assert len(data["audit_history"]) >= 1
    assert len(data["errors"]) == 1
    assert data["errors"][0]["stage"] == "render"
    assert len(data["costs"]) == 1
    assert data["costs"][0]["tokens"] == 120

    restored = dict_to_state(data)
    assert restored.execution_id == "exec-1"
    assert restored.video_id == "v1"
    assert restored.status == Stage.IDEA_CREATED
    assert restored.script is not None
    assert restored.script.title == "T"
    assert restored.validation_results is not None
    assert restored.validation_results.valid is True
    assert restored.approval_status == ApprovalStatus.APPROVED
    assert restored.publication_status == PublicationStatus.QUEUED
    assert restored.retries == 1
    assert len(restored.errors) == 1
    assert len(restored.costs) == 1


def test_stage_enum_values():
    assert Stage.IDEA_CREATED == "IDEA_CREATED"
    assert Stage.PUBLISHED == "PUBLISHED"
    assert Stage.FAILED == "FAILED"
    assert "APPROVED" in {s.value for s in Stage}
    assert "UPLOADING" in {s.value for s in Stage}


def test_approval_and_publication_enums():
    assert ApprovalStatus.PENDING == "pending"
    assert ApprovalStatus.APPROVED == "approved"
    assert ApprovalStatus.REJECTED == "rejected"
    assert PublicationStatus.NONE == "none"
    assert PublicationStatus.PUBLISHED == "published"


def test_script_validation_rejects_negative_seconds():
    with pytest.raises(Exception):
        ScriptScene(seconds=-1.0)


def test_validation_result_defaults_invalid():
    vr = ValidationResult()
    assert vr.valid is False
    assert vr.errors == []


def test_video_asset_defaults():
    va = VideoAsset()
    assert va.mp4_path is None
    assert va.has_audio is False
    assert va.size_bytes == 0


__all__ = []
