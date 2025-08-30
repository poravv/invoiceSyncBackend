# clients.py
from __future__ import annotations
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class OpenAIChatClient:
    """Interfaz simple para chat completions en modo JSON o texto."""

    def __init__(self, api_key: str, flavour: str) -> None:
        self.flavour = flavour
        self.client = None

        if flavour == "new":
            from openai import OpenAI  # SDK 1.x
            self.client = OpenAI(api_key=api_key)
        else:
            import openai  # SDK 0.28.x
            openai.api_key = api_key
            self.client = openai

    def _token_args(self, model: str, max_tokens: int) -> dict:
        # gpt-5* exige max_completion_tokens; otros usan max_tokens
        return (
            {"max_completion_tokens": max_tokens}
            if str(model).startswith("gpt-5")
            else {"max_tokens": max_tokens}
        )

    def _supports_json_mode(self, model: str) -> bool:
        # Mini no soporta JSON mode ni temperature≠1
        return not str(model).startswith("gpt-5")

    def chat_json(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_completion_tokens: int,
        temperature: float | None = None,
    ) -> str:
        token_kwargs = self._token_args(model, max_completion_tokens)
        use_json_mode = self._supports_json_mode(model)

        # Para gpt-5-mini, NO mandamos temperature ni response_format
        extra_kwargs: Dict[str, Any] = {}
        if use_json_mode:
            extra_kwargs["response_format"] = {"type": "json_object"}
            if temperature is not None:
                extra_kwargs["temperature"] = temperature  # permitido
        else:
            # mini: no temperature (solo default=1), y sin response_format
            pass

        # Log para debugging
        logger.debug("Enviando request a OpenAI - Model: %s, JSON mode: %s, Temperature: %s, Max tokens: %s", 
                    model, use_json_mode, temperature, max_completion_tokens)

        try:
            if self.flavour == "new":
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **token_kwargs,
                    **extra_kwargs,
                )
                # A veces content viene None; devolvemos string vacío pero logeamos todo
                choice = resp.choices[0]
                content = (choice.message.content or "").strip()
                
                if not content:
                    logger.warning(
                        "Respuesta sin content (model=%s, finish_reason=%s). Devolviendo string vacío.",
                        resp.model, choice.finish_reason
                    )
                    logger.debug("Respuesta cruda: %s", resp.model_dump())
                    
                    # Si la respuesta fue truncada por límite de tokens, intentar con más tokens
                    if choice.finish_reason == "length":
                        logger.warning("Respuesta truncada por límite de tokens. Intentando con más tokens...")
                        try:
                            # Aumentar significativamente el límite de tokens
                            increased_tokens = max_completion_tokens * 2
                            increased_kwargs = self._token_args(model, increased_tokens)
                            
                            logger.info("Reintentando con %d tokens", increased_tokens)
                            retry_resp = self.client.chat.completions.create(
                                model=model,
                                messages=messages,
                                **increased_kwargs,
                                **extra_kwargs,
                            )
                            
                            retry_choice = retry_resp.choices[0]
                            retry_content = (retry_choice.message.content or "").strip()
                            
                            if retry_content:
                                logger.info("Reintento exitoso con más tokens - Longitud: %d", len(retry_content))
                                return retry_content
                            else:
                                logger.error("Reintento con más tokens también falló")
                        except Exception as retry_error:
                            logger.error("Error en reintento con más tokens: %s", retry_error)
                else:
                    logger.debug("Respuesta recibida - Longitud: %d, Primeros 200 chars: %s", 
                               len(content), content[:200])
                
                return content

            else:
                resp = self.client.ChatCompletion.create(  # type: ignore[attr-defined]
                    model=model,
                    messages=messages,
                    **token_kwargs,
                    **extra_kwargs,
                )
                content = (resp["choices"][0]["message"].get("content") or "").strip()
                
                if not content:
                    logger.warning("Respuesta legacy sin content. Devolviendo string vacío.")
                    logger.debug("Respuesta cruda: %s", resp)
                else:
                    logger.debug("Respuesta legacy recibida - Longitud: %d, Primeros 200 chars: %s", 
                               len(content), content[:200])
                
                return content
                
        except Exception as e:
            logger.error("Error en request a OpenAI: %s", e)
            # Para GPT-5-mini, intentar un segundo intento con configuración más simple
            if not use_json_mode and "gpt-5" in str(model).lower():
                logger.info("Intentando segundo intento con GPT-5-mini sin parámetros adicionales")
                try:
                    if self.flavour == "new":
                        resp = self.client.chat.completions.create(
                            model=model,
                            messages=messages,
                            max_completion_tokens=max_completion_tokens,
                        )
                        choice = resp.choices[0]
                        content = (choice.message.content or "").strip()
                        logger.debug("Segundo intento exitoso - Longitud: %d", len(content))
                        return content
                    else:
                        resp = self.client.ChatCompletion.create(  # type: ignore[attr-defined]
                            model=model,
                            messages=messages,
                            max_tokens=max_completion_tokens,
                        )
                        content = (resp["choices"][0]["message"].get("content") or "").strip()
                        logger.debug("Segundo intento legacy exitoso - Longitud: %d", len(content))
                        return content
                except Exception as retry_error:
                    logger.error("Segundo intento también falló: %s", retry_error)
            
            # Si todo falla, devolver string vacío
            return ""


def make_openai_client(api_key: str) -> OpenAIChatClient:
    try:
        import openai as _openai
        ver = getattr(_openai, "__version__", "0.0.0")
        major = int(str(ver).split(".")[0])
        flavour = "new" if major >= 1 else "legacy"
    except Exception:
        flavour = "new"
    return OpenAIChatClient(api_key=api_key, flavour=flavour)