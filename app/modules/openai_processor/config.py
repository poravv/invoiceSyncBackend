from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class OpenAIConfig:
    """Configuración para el procesador de facturas vía OpenAI."""
    api_key: str
    model: str = "gpt-5-mini"          # mantiene default que usas
    temperature: float = 0.3       # más conservador para extracción
    max_completion_tokens: int = 4000         # aumentado para evitar truncamiento en respuestas