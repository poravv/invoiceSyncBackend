from __future__ import annotations
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class OpenAIChatClient:
    """Interfaz simple para chat completions en modo JSON o texto."""

    def __init__(self, api_key: str, flavour: str = "legacy") -> None:
        """
        flavour:
            - "legacy": openai==0.28.x (openai.ChatCompletion.create)
            - "new": openai>=1.x (client.chat.completions.create)
        """
        self.flavour = flavour
        self.client = None

        if flavour == "new":
            try:
                from openai import OpenAI  # type: ignore
                self.client = OpenAI(api_key=api_key)
            except Exception as e:
                logger.error("No se pudo inicializar OpenAI (nuevo SDK): %s", e)
                raise
        else:
            try:
                import openai  # type: ignore
                openai.api_key = api_key
                self.client = openai
            except Exception as e:
                logger.error("No se pudo inicializar OpenAI (legacy): %s", e)
                raise

    def chat_json(self, model: str, messages: List[Dict[str, Any]], temperature: float, max_tokens: int) -> str:
        """Hace una llamada y retorna el content (str). En flavour new intenta forzar JSON."""
        if self.flavour == "new":
            # Nuevo SDK
            resp = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            return resp.choices[0].message.content or ""
        else:
            # Legacy SDK: OpenAI 0.28.x (no hay response_format)
            resp = self.client.ChatCompletion.create(  # type: ignore[attr-defined]
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp["choices"][0]["message"]["content"]

def make_openai_client(api_key: str) -> OpenAIChatClient:
    """
    Si quieres migrar al nuevo SDK, cambia aquí a flavour="new".
    Por ahora se mantiene "legacy" para tu stack actual.
    """
    return OpenAIChatClient(api_key=api_key, flavour="legacy")