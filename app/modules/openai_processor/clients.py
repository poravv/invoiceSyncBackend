from __future__ import annotations
from typing import List, Dict, Any
import logging
import socket
import time
from requests.exceptions import RequestException, Timeout, ConnectionError

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
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                if self.flavour == "new":
                    # Nuevo SDK con timeout
                    resp = self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                        timeout=60,  # 60 segundos timeout
                    )
                    return resp.choices[0].message.content or ""
                else:
                    # Legacy SDK: OpenAI 0.28.x
                    resp = self.client.ChatCompletion.create(  # type: ignore[attr-defined]
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=60,  # 60 segundos timeout
                    )
                    return resp["choices"][0]["message"]["content"]
                    
            except (socket.timeout, Timeout) as e:
                logger.warning(f"⏱️ Timeout en OpenAI API (intento {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise Exception(f"Timeout en OpenAI API después de {max_retries} intentos")
                time.sleep(retry_delay * (attempt + 1))
                
            except (ConnectionError, socket.error) as e:
                logger.warning(f"🔌 Error de conexión en OpenAI API (intento {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise Exception(f"Error de conexión en OpenAI API después de {max_retries} intentos")
                time.sleep(retry_delay * (attempt + 1))
                
            except Exception as e:
                error_msg = str(e).lower()
                # Errores de rate limit - reintentar
                if "rate limit" in error_msg or "too many requests" in error_msg:
                    logger.warning(f"📊 Rate limit en OpenAI API (intento {attempt + 1}/{max_retries}): {e}")
                    if attempt == max_retries - 1:
                        raise Exception(f"Rate limit en OpenAI API después de {max_retries} intentos")
                    time.sleep(retry_delay * (attempt + 1) * 2)  # Más tiempo para rate limit
                    continue
                    
                # Errores fatales - no reintentar
                elif any(fatal in error_msg for fatal in [
                    "invalid api key", "api key", "authentication", "unauthorized",
                    "insufficient quota", "quota exceeded", "billing"
                ]):
                    logger.error(f"❌ Error fatal de OpenAI API: {e}")
                    raise Exception(f"Error fatal de OpenAI API: {e}")
                    
                # Otros errores - reintentar
                else:
                    logger.warning(f"⚠️ Error en OpenAI API (intento {attempt + 1}/{max_retries}): {e}")
                    if attempt == max_retries - 1:
                        raise Exception(f"Error en OpenAI API después de {max_retries} intentos: {e}")
                    time.sleep(retry_delay * (attempt + 1))
        
        raise Exception("No se pudo completar la llamada a OpenAI API")

def make_openai_client(api_key: str) -> OpenAIChatClient:
    """
    Si quieres migrar al nuevo SDK, cambia aquí a flavour="new".
    Por ahora se mantiene "legacy" para tu stack actual.
    """
    return OpenAIChatClient(api_key=api_key, flavour="legacy")