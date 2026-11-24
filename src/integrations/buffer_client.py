"""Cliente Buffer API (GraphQL): canal TikTok, fila de posts, estados.

Contrato oficial (https://developers.buffer.com/):
- Endpoint único: POST https://api.buffer.com
- Auth: Bearer API key (Authorization header)
- createPost é NÃO idempotente — não há clientMutationId. A idempotência é
  garantida neste cliente por vídeo (não recria o mesmo post).
- CreatePostInput obrigatório: channelId, mode, schedulingType.
  dueAt é obrigatório quando mode=customScheduled.
- ShareMode: addToQueue, customScheduled, shareNext, shareNow.
- SchedulingType: automatic, notification.
- PostStatus: draft, error, needs_approval, scheduled, sending, sent
  (não existem "pending" nem "buffer").
- Vídeo: assets: [{ video: { url, metadata: { thumbnailOffset: ms } } }].

Nenhuma chamada real ocorre enquanto o modo ``live`` estiver desativado.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import requests

from src.errors import PipelineError

GRAPHQL_ENDPOINT = "https://api.buffer.com"

# PostStatus válidos (conforme documentação oficial).
VALID_POST_STATUSES = {"draft", "error", "needs_approval", "scheduled", "sending", "sent"}
PENDING_STATUSES = {"scheduled", "sending"}

VALID_SHARE_MODES = {"addToQueue", "customScheduled", "shareNext", "shareNow"}
VALID_SCHEDULING_TYPES = {"automatic", "notification"}


@dataclass(frozen=True)
class PostResult:
    """Resultado de criação/consulta de um post no Buffer."""
    post_id: str
    status: str
    channel_id: str
    text: str
    due_at: Optional[str]
    mock: bool


class BufferClient:
    """Cliente GraphQL do Buffer com mock para modo offline."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        organization_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        live: bool = False,
        db: Optional[Any] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("BUFFER_API_KEY")
        self.organization_id = organization_id or os.environ.get("BUFFER_ORGANIZATION_ID")
        self.channel_id = channel_id or os.environ.get("BUFFER_CHANNEL_ID")
        self.live = live
        self.db = db
        self._posts: dict[str, PostResult] = {}  # posts por post_id (memória)
        self._video_to_post: dict[str, str] = {}  # idempotência por vídeo (memória)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _require_credentials(self) -> None:
        if not self.live:
            return
        if not self.api_key:
            raise PipelineError("BUFFER_API_KEY é obrigatória para o modo live.")
        if not self.organization_id:
            raise PipelineError("BUFFER_ORGANIZATION_ID é obrigatória para o modo live.")
        if not self.channel_id:
            raise PipelineError("BUFFER_CHANNEL_ID é obrigatória para o modo live.")

    def _graphql(self, query: str, variables: dict) -> dict:
        if not self.live:
            raise PipelineError("Chamadas GraphQL só são permitidas em modo live.")
        try:
            resp = requests.post(
                GRAPHQL_ENDPOINT,
                headers=self._headers(),
                json={"query": query, "variables": variables},
                timeout=60,
            )
        except requests.RequestException as exc:
            raise PipelineError(f"Falha de rede no Buffer: {exc}") from exc
        if resp.status_code >= 400:
            raise PipelineError(f"Buffer retornou HTTP {resp.status_code}") from None
        payload = resp.json()
        if "errors" in payload:
            raise PipelineError(f"Erros do Buffer: {payload['errors']}")
        return payload.get("data", {})

    # ------------------------------------------------------------------ #
    # Descoberta                                                         #
    # ------------------------------------------------------------------ #
    def discover_organizations(self) -> list[dict]:
        if not self.live:
            return [{"id": "mock-org", "name": "Organização Mock"}]
        self._require_credentials()
        query = """
        query GetOrganizations { account { organizations { id name } } }
        """
        data = self._graphql(query, {})
        return data.get("account", {}).get("organizations", [])

    def discover_tiktok_channel(self) -> Optional[dict]:
        """Encontra o canal TikTok da organização configurada."""
        if not self.live:
            return {"id": "mock-tiktok-channel", "name": "TikTok (mock)", "service": "tiktok"}
        self._require_credentials()
        if not self.organization_id:
            raise PipelineError("BUFFER_ORGANIZATION_ID não configurado.")
        query = """
        query GetChannels($input: ChannelsInput!) {
          channels(input: $input) { id name service descriptor displayName }
        }
        """
        data = self._graphql(query, {"input": {"organizationId": self.organization_id}})
        for ch in data.get("channels", []):
            if ch.get("service") == "tiktok" and not ch.get("isDisconnected") and not ch.get("isLocked"):
                return ch
        return None

    # ------------------------------------------------------------------ #
    # Criação de post                                                    #
    # ------------------------------------------------------------------ #
    def create_post(self, video_id: str, text: str, channel_id: Optional[str] = None,
                    due_at: Optional[str] = None,
                    video_url: Optional[str] = None) -> PostResult:
        """Cria (ou devolve, por idempotência) um post agendado no Buffer.

        Idempotência: se já existe um post para este ``video_id`` nesta sessão,
        devolve o existente em vez de recriar (createPost não é idempotente nativamente).
        """
        channel_id = channel_id or self.channel_id
        if not channel_id:
            raise PipelineError("Nenhum canal TikTok configurado (BUFFER_CHANNEL_ID).")

        # Idempotência persistente: se já gravamos um post para este vídeo
        # (inclusive após reinicialização), reutiliza em vez de recriar.
        if self.db is not None:
            existing = self.db.get_scheduled_post_by_video(video_id)
            if existing is not None:
                result = PostResult(
                    post_id=str(existing["buffer_post_id"]),
                    status=str(existing["state"] or "scheduled"),
                    channel_id=channel_id,
                    text=text,
                    due_at=existing["scheduled_at"],
                    mock=(not self.live),
                )
                self._posts[result.post_id] = result
                self._video_to_post[video_id] = result.post_id
                return result

        # Idempotência por vídeo (memória da sessão): não duplica o mesmo vídeo.
        existing_post_id = self._video_to_post.get(video_id)
        if existing_post_id and existing_post_id in self._posts:
            return self._posts[existing_post_id]

        mode = "customScheduled" if due_at else "addToQueue"
        if mode not in VALID_SHARE_MODES:
            raise PipelineError(f"ShareMode inválido: {mode}")
        scheduling_type = "automatic"
        if scheduling_type not in VALID_SCHEDULING_TYPES:
            raise PipelineError(f"SchedulingType inválido: {scheduling_type}")

        if not self.live:
            result = PostResult(
                post_id=video_id,
                status="scheduled",
                channel_id=channel_id,
                text=text,
                due_at=due_at,
                mock=True,
            )
            self._posts[result.post_id] = result
            self._video_to_post[video_id] = result.post_id
            return result

        self._require_credentials()
        mutation = """
        mutation CreatePost($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess { post { id text status channelId dueAt schedulingType } }
            ... on MutationError { message }
          }
        }
        """
        assets = None
        if video_url:
            assets = [{"video": {"url": video_url, "metadata": {"thumbnailOffset": 2000}}}]
        variables = {
            "input": {
                "text": text,
                "channelId": channel_id,
                "mode": mode,
                "schedulingType": scheduling_type,
                "dueAt": due_at,
                "assets": assets,
            },
        }
        data = self._graphql(mutation, variables)
        result = data.get("createPost", {})
        if "message" in result:
            raise PipelineError(f"Buffer rejeitou o post: {result['message']}")
        post = result.get("post", {})
        post_result = PostResult(
            post_id=str(post.get("id", video_id)),
            status=str(post.get("status", "scheduled")),
            channel_id=str(post.get("channelId", channel_id)),
            text=str(post.get("text", text)),
            due_at=post.get("dueAt"),
            mock=False,
        )
        self._posts[post_result.post_id] = post_result
        self._video_to_post[video_id] = post_result.post_id
        return post_result

    # ------------------------------------------------------------------ #
    # Consulta de posts pendentes                                        #
    # ------------------------------------------------------------------ #
    def list_pending_posts(self, channel_id: Optional[str] = None,
                           first: int = 20) -> list[PostResult]:
        """Lista posts pendentes (status scheduled/sending) do canal."""
        channel_id = channel_id or self.channel_id
        if not self.live:
            return [p for p in self._posts.values() if p.status in PENDING_STATUSES]
        self._require_credentials()
        query = """
        query GetPosts($input: PostsInput!) {
          posts(first: $first, input: $input) {
            edges { node { id text status channelId dueAt } }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        data = self._graphql(query, {
            "first": first,
            "input": {"organizationId": self.organization_id,
                      "filter": {"channelIds": [channel_id], "status": list(PENDING_STATUSES)}},
        })
        posts: list[PostResult] = []
        for edge in (data.get("posts") or {}).get("edges", []) or []:
            node = edge.get("node", {})
            posts.append(PostResult(
                post_id=str(node.get("id", "")),
                status=str(node.get("status", "scheduled")),
                channel_id=str(node.get("channelId", channel_id)),
                text=str(node.get("text", "")),
                due_at=node.get("dueAt"),
                mock=False,
            ))
        return posts


__all__ = ["BufferClient", "PostResult", "GRAPHQL_ENDPOINT",
           "VALID_POST_STATUSES", "PENDING_STATUSES",
           "VALID_SHARE_MODES", "VALID_SCHEDULING_TYPES"]
