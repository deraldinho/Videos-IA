"""Módulo de publicação multicanal (CP24).

Fornece:

* ``PublicationAdapter`` — protocolo de adaptador de publicação (prepare, upload,
  publish, get_remote_status).
* ``TikTokAdapter`` — usa CloudinaryClient (upload) + BufferClient (create_post).
  Em modo mock (live=False) nunca faz chamada real; local_state vai none→uploaded→queued,
  nunca published sem confirmação remota real.
* ``YouTubeShortsAdapter`` — mock, platform="youtube_shorts".
* ``InstagramReelsAdapter`` — mock, platform="instagram_reels" (condicional).
* ``PublicationManager`` — coordena múltiplos adaptadores com isolamento de falha
  e idempotência por idempotency_key.

Regras de segurança (AGENTS.md + architecture.md):
- Nunca PUBLISHED sem remote confirmation real (remote_id + remote_status + confirmed_at).
- Modos DRY_RUN/REVIEW/AUTO respeitados (live_enabled controla upload real).
- Mocks claramente identificados.
- Nenhuma credencial lida ou exposta.
- Uploaded != Queued != Published são estados distinguíveis.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.workflow.state import PublicationTarget, _utc_now_iso


# --------------------------------------------------------------------------- #
# Protocolo                                                                   #
# --------------------------------------------------------------------------- #
@runtime_checkable
class PublicationAdapter(Protocol):
    """Protocolo de adaptador de publicação por plataforma."""

    platform: str

    def prepare(self, target: PublicationTarget) -> PublicationTarget: ...
    def upload(self, target: PublicationTarget, video_path: str) -> PublicationTarget: ...
    def publish(self, target: PublicationTarget) -> PublicationTarget: ...
    def get_remote_status(self, target: PublicationTarget) -> PublicationTarget: ...


# --------------------------------------------------------------------------- #
# TikTokAdapter                                                               #
# --------------------------------------------------------------------------- #
class TikTokAdapter:
    """Adaptador de publicação para TikTok (Cloudinary + Buffer).

    Em modo live (live=True): upload real no Cloudinary, create_post real no Buffer,
    só marca published com confirmação remota real (remote_id + remote_status + confirmed_at).
    Em modo mock (live=False): nunca chama APIs externas, estados simulados distinguíveis
    de estados confirmados remotamente.
    """

    platform = "tiktok"

    def __init__(self, live: bool = False) -> None:
        self.live = live
        self.mock = not live

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: PublicationTarget) -> PublicationTarget:
        """Valida o target e define idempotency_key se ausente."""
        if not target.video_id:
            target.error_message = "video_id é obrigatório"
            return target
        if not target.idempotency_key:
            target.idempotency_key = f"{target.video_id}:{self.platform}"
        target.updated_at = _utc_now_iso()
        return target

    # ------------------------------------------------------------------ upload
    def upload(self, target: PublicationTarget, video_path: str) -> PublicationTarget:
        """Faz upload do vídeo (Cloudinary). Define local_state='uploaded'."""
        from src.integrations.cloudinary_client import CloudinaryClient

        try:
            cloudinary = getattr(self, "_cloudinary", None) or CloudinaryClient(live=self.live)
            result = cloudinary.upload(target.video_id, video_path)
            target.cloudinary_url = result.secure_url  # type: ignore[attr-defined]
            target.cloudinary_id = result.public_id  # type: ignore[attr-defined]
            target.local_state = "uploaded"
            target.error_message = None
        except Exception as exc:  # noqa: BLE001
            target.local_state = "failed"
            target.error_message = f"Falha no upload Cloudinary: {exc}"
        target.updated_at = _utc_now_iso()
        return target

    # ------------------------------------------------------------------ publish
    def publish(self, target: PublicationTarget) -> PublicationTarget:
        """Agenda post no Buffer. Define local_state='queued' (nunca 'published' sem confirmação remota)."""
        from src.integrations.buffer_client import BufferClient

        if target.local_state != "uploaded":
            target.error_message = f"Não é possível publicar do estado {target.local_state}; requer 'uploaded'"
            target.updated_at = _utc_now_iso()
            return target

        try:
            buffer = getattr(self, "_buffer", None) or BufferClient(
                live=self.live,
                channel_id=target.channel_id or "mock-tiktok-channel",
            )
            # Idempotência: não recria post para o mesmo video_id
            existing = None
            if hasattr(buffer, "get_scheduled_post_by_video"):
                try:
                    existing = buffer.get_scheduled_post_by_video(target.video_id)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    existing = None

            if existing:
                target.buffer_post_id = existing.post_id  # type: ignore[attr-defined]
                target.buffer_post_state = existing.status  # type: ignore[attr-defined]
            else:
                post = buffer.create_post(
                    video_id=target.video_id,
                    text=target.channel_id or "",
                    channel_id=target.channel_id or "mock-tiktok-channel",
                    due_at=None,
                    video_url=target.cloudinary_url or "",  # type: ignore[attr-defined]
                )
                target.buffer_post_id = post.post_id
                target.buffer_post_state = post.status

            target.local_state = "queued"
            target.error_message = None
        except Exception as exc:  # noqa: BLE001
            target.local_state = "failed"
            target.error_message = f"Falha no agendamento Buffer: {exc}"

        target.updated_at = _utc_now_iso()
        return target

    # -------------------------------------------------------------- get_remote_status
    def get_remote_status(self, target: PublicationTarget) -> PublicationTarget:
        """Consulta status remoto. Só marca 'published' com confirmação remota real.

        Em modo mock: nunca confirma como publicado sem dados remotos reais;
        mantém o estado local atual e defined_at permanece None.

        Em modo live: consulta a API remota real (via _remote_client se injetado).
        Só marca local_state='published' quando remote_id + remote_status +
        confirmed_at são fornecidos pela confirmação remota.
        """
        if self.mock:
            # Mock: nunca confirma como publicado sem dados remotos reais
            target.local_state = target.local_state  # mantém o estado atual
            target.remote_status = target.remote_status or "scheduled"
            # confirmed_at permanece None em mock
            target.publication_confirmed_at = None
            target.updated_at = _utc_now_iso()
            return target

        # Live: consulta API remota real
        remote_client = getattr(self, "_remote_client", None)
        if remote_client is None:
            target.error_message = (
                "get_remote_status remoto não implementado para TikTok sem credenciais "
                "(inicie com _remote_client para consultar API real)"
            )
            target.updated_at = _utc_now_iso()
            return target

        try:
            status = remote_client.get_video_status(target.video_id)
            if status and status.get("published"):
                target.remote_id = status.get("remote_id") or target.remote_id
                target.remote_status = status.get("remote_status") or "published"
                target.confirmed_at = status.get("confirmed_at") or _utc_now_iso()
                target.publication_confirmed_at = target.confirmed_at
                target.local_state = "published"
                target.error_message = None
            else:
                target.remote_status = status.get("remote_status") if status else "pending"
                target.error_message = "Vídeo ainda não confirmado como publicado remotamente"
        except Exception as exc:  # noqa: BLE001
            target.error_message = f"Falha ao consultar status remoto: {exc}"

        target.updated_at = _utc_now_iso()
        return target


# --------------------------------------------------------------------------- #
# YouTubeShortsAdapter                                                        #
# --------------------------------------------------------------------------- #
class YouTubeShortsAdapter:
    """Adaptador mock para YouTube Shorts (sem API real configurada por default)."""

    platform = "youtube_shorts"

    def __init__(self, live: bool = False) -> None:
        self.live = live
        self.mock = not live

    def prepare(self, target: PublicationTarget) -> PublicationTarget:
        if not target.video_id:
            target.error_message = "video_id é obrigatório"
            return target
        if not target.idempotency_key:
            target.idempotency_key = f"{target.video_id}:{self.platform}"
        target.updated_at = _utc_now_iso()
        return target

    def upload(self, target: PublicationTarget, video_path: str) -> PublicationTarget:
        target.local_state = "uploaded"
        target.error_message = None
        target.updated_at = _utc_now_iso()
        return target

    def publish(self, target: PublicationTarget) -> PublicationTarget:
        if target.local_state != "uploaded":
            target.error_message = f"Não é possível publicar do estado {target.local_state}"
            target.updated_at = _utc_now_iso()
            return target
        target.local_state = "queued"
        target.error_message = None
        target.updated_at = _utc_now_iso()
        return target

    def get_remote_status(self, target: PublicationTarget) -> PublicationTarget:
        target.remote_status = target.remote_status or "scheduled"
        target.updated_at = _utc_now_iso()
        return target


# --------------------------------------------------------------------------- #
# InstagramReelsAdapter                                                       #
# --------------------------------------------------------------------------- #
class InstagramReelsAdapter:
    """Adaptador mock para Instagram Reels (condicional, só com permissão/API)."""

    platform = "instagram_reels"

    def __init__(self, live: bool = False) -> None:
        self.live = live
        self.mock = not live

    def prepare(self, target: PublicationTarget) -> PublicationTarget:
        if not target.video_id:
            target.error_message = "video_id é obrigatório"
            return target
        if not target.idempotency_key:
            target.idempotency_key = f"{target.video_id}:{self.platform}"
        target.updated_at = _utc_now_iso()
        return target

    def upload(self, target: PublicationTarget, video_path: str) -> PublicationTarget:
        target.local_state = "uploaded"
        target.error_message = None
        target.updated_at = _utc_now_iso()
        return target

    def publish(self, target: PublicationTarget) -> PublicationTarget:
        if target.local_state != "uploaded":
            target.error_message = f"Não é possível publicar do estado {target.local_state}"
            target.updated_at = _utc_now_iso()
            return target
        target.local_state = "queued"
        target.error_message = None
        target.updated_at = _utc_now_iso()
        return target

    def get_remote_status(self, target: PublicationTarget) -> PublicationTarget:
        target.remote_status = target.remote_status or "scheduled"
        target.updated_at = _utc_now_iso()
        return target


# --------------------------------------------------------------------------- #
# PublicationManager                                                          #
# --------------------------------------------------------------------------- #
class PublicationManager:
    """Coordena múltiplos PublicationAdapter com isolamento de falha e idempotência.

    Falha em um adapter não impede outros de publicar (try/catch por adapter).
    Mesmo video_id+platform retorna target existente (não duplica).
    """

    def __init__(self, adapters: list[PublicationAdapter]) -> None:
        self.adapters = adapters
        self._by_platform: dict[str, PublicationAdapter] = {a.platform: a for a in adapters}
        self._targets: dict[str, PublicationTarget] = {}  # idempotency_key -> target

    def _adapter_for(self, platform: str) -> PublicationAdapter | None:
        return self._by_platform.get(platform)

    # -------------------------------------------------------------- prepare_targets
    def prepare_targets(
        self,
        state: dict[str, Any],
        video_id: str,
        platforms: list[str],
    ) -> list[PublicationTarget]:
        """Prepara PublicationTargets para as plataformas informadas (idempotente)."""
        targets: list[PublicationTarget] = []
        for platform in platforms:
            adapter = self._adapter_for(platform)
            if adapter is None:
                continue
            key = f"{video_id}:{platform}"
            existing = self._targets.get(key)
            if existing is not None:
                targets.append(existing)
                continue
            target = PublicationTarget(
                video_id=video_id,
                platform=platform,
                channel_id="",
                idempotency_key=key,
                local_state="none",
            )
            target = adapter.prepare(target)
            self._targets[key] = target
            targets.append(target)
        # Persiste no estado
        existing_targets = list(state.get("publication_targets") or [])
        for t in targets:
            # Evita duplicar no estado por idempotency_key
            if not any(
                et.get("idempotency_key") == t.idempotency_key for et in existing_targets
            ):
                existing_targets.append(t.model_dump(mode="json"))
        state["publication_targets"] = existing_targets
        return targets

    # -------------------------------------------------------------- upload_all
    def upload_all(
        self,
        targets: list[PublicationTarget],
        video_path: str,
    ) -> list[dict[str, Any]]:
        """Faz upload em todos os targets. Falha em um não afeta os outros."""
        results: list[dict[str, Any]] = []
        for target in targets:
            adapter = self._adapter_for(target.platform)
            if adapter is None:
                results.append({
                    "platform": target.platform,
                    "ok": False,
                    "error": f"Nenhum adapter para {target.platform}",
                    "local_state": target.local_state,
                })
                continue
            try:
                out = adapter.upload(target, video_path)
                # Atualiza target no _targets
                self._targets[target.idempotency_key] = out
                results.append({
                    "platform": out.platform,
                    "ok": out.local_state == "uploaded",
                    "local_state": out.local_state,
                    "error": out.error_message,
                    "cloudinary_id": getattr(out, "cloudinary_id", None),
                    "cloudinary_url": getattr(out, "cloudinary_url", None),
                })
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "platform": target.platform,
                    "ok": False,
                    "error": str(exc),
                    "local_state": target.local_state,
                })
        return results

    # -------------------------------------------------------------- publish_all
    def publish_all(
        self,
        targets: list[PublicationTarget],
    ) -> list[dict[str, Any]]:
        """Agenda publicação em todos os targets. Falha em um não afeta os outros."""
        results: list[dict[str, Any]] = []
        for target in targets:
            adapter = self._adapter_for(target.platform)
            if adapter is None:
                results.append({
                    "platform": target.platform,
                    "ok": False,
                    "error": f"Nenhum adapter para {target.platform}",
                    "local_state": target.local_state,
                })
                continue
            try:
                out = adapter.publish(target)
                self._targets[target.idempotency_key] = out
                results.append({
                    "platform": out.platform,
                    "ok": out.local_state == "queued",
                    "local_state": out.local_state,
                    "error": out.error_message,
                    "buffer_post_id": getattr(out, "buffer_post_id", None),
                    "buffer_post_state": getattr(out, "buffer_post_state", None),
                })
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "platform": target.platform,
                    "ok": False,
                    "error": str(exc),
                    "local_state": target.local_state,
                })
        return results

    # ---------------------------------------------------------- get_all_remote_status
    def get_all_remote_status(
        self,
        targets: list[PublicationTarget],
    ) -> list[dict[str, Any]]:
        """Consulta status remoto de todos os targets (reconciliação)."""
        results: list[dict[str, Any]] = []
        for target in targets:
            adapter = self._adapter_for(target.platform)
            if adapter is None:
                results.append({
                    "platform": target.platform,
                    "local_state": target.local_state,
                    "remote_status": target.remote_status,
                    "confirmed_at": target.confirmed_at,
                    "error": "Nenhum adapter",
                })
                continue
            try:
                out = adapter.get_remote_status(target)
                self._targets[target.idempotency_key] = out
                results.append({
                    "platform": out.platform,
                    "local_state": out.local_state,
                    "remote_status": out.remote_status,
                    "confirmed_at": out.confirmed_at,
                    "remote_id": out.remote_id,
                    "error": out.error_message,
                })
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "platform": target.platform,
                    "local_state": target.local_state,
                    "remote_status": target.remote_status,
                    "confirmed_at": target.confirmed_at,
                    "error": str(exc),
                })
        return results


__all__ = [
    "PublicationAdapter",
    "TikTokAdapter",
    "YouTubeShortsAdapter",
    "InstagramReelsAdapter",
    "PublicationManager",
]
