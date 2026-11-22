"""Provedores de LLM para geração de roteiros (modo assisted / NVIDIA NIM)."""

from .provider import AssistedProvider, NVIDIAProvider

__all__ = ["AssistedProvider", "NVIDIAProvider"]
