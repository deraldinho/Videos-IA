"""Integrações externas: Cloudinary e Buffer API (com suporte a mock)."""

from .cloudinary_client import CloudinaryClient
from .buffer_client import BufferClient

__all__ = ["CloudinaryClient", "BufferClient"]
