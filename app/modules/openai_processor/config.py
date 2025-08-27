from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class OpenAIConfig:
    """Configuración para el procesador de facturas vía OpenAI."""
    api_key: str
    model: str = "gpt-4o"          # mantiene default que usas
    temperature: float = 0.3       # más conservador para extracción
    max_tokens: int = 1500         # suficiente para respuestas complejas