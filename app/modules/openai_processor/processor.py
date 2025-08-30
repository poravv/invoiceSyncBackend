from __future__ import annotations
import logging
import os
from typing import Any, Dict, Optional

from app.config.settings import settings
from .config import OpenAIConfig
from .clients import make_openai_client
from .pdf_text import extract_text_with_fallbacks, has_extractable_text_or_ocr
from .image_utils import pdf_to_base64_first_page, ocr_from_base64_image
from .prompts import build_text_prompt, build_image_prompt, build_xml_prompt, messages_user_only, messages_user_with_image
from .json_utils import extract_and_normalize_json
from .cdc import validate_and_enhance_with_cdc

# Configurar logging específico para este módulo
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Asegurar que se muestren mensajes de debug

class OpenAIProcessor:
    """
    Orquestador público.
    - extract_invoice_data(pdf_path, email_metadata=None) → InvoiceData | dict | None
    - extract_invoice_data_from_xml(xml_path, email_metadata=None) → InvoiceData | dict | None
    Conserva la funcionalidad de tu clase original pero dividida en responsabilidades.
    """

    def __init__(self, cfg: Optional[OpenAIConfig] = None) -> None:
        cfg = cfg or OpenAIConfig(
            api_key=settings.OPENAI_API_KEY,
            model=getattr(settings, "OPENAI_MODEL", "gpt-5-mini"),
            temperature=0.3,
            max_completion_tokens=1500,
        )
        if not cfg.api_key:
            logger.warning("API key de OpenAI no configurada. El procesador no podrá llamar a OpenAI.")
        self.cfg = cfg
        self.client = make_openai_client(cfg.api_key)

    # ------------------------------------------------------------------ API --
    def extract_invoice_data(self, pdf_path: str, email_metadata: Optional[Dict[str, Any]] = None):
        """
        Procesamiento directo por imagen para mayor precisión en facturas paraguayas.
        Comentado: Extracción de texto (menos preciso para formatos específicos)
        """
        try:
            # COMENTADO: Extracción de texto (menos preciso para facturas paraguayas)
            # if has_extractable_text_or_ocr(pdf_path):
            #     result = self._process_as_text(pdf_path, email_metadata)
            #     if result:
            #         return result
            #     logger.warning("Texto falló → intentamos por imagen")

            # PROCESAMIENTO DIRECTO COMO IMAGEN (más preciso)
            logger.info("Procesando PDF como imagen para mayor precisión: %s", pdf_path)
            result = self._process_as_image(pdf_path, email_metadata)
            if result:
                return result

            logger.warning("Procesamiento por imagen falló")
            return None
        except Exception as e:
            logger.exception("Error en extract_invoice_data: %s", e)
            return None

    def extract_invoice_data_from_xml(self, xml_path: str, email_metadata: dict | None = None):
        try:
            if not os.path.exists(xml_path):
                logger.warning("XML no existe: %s", xml_path)
                return None
                
            with open(xml_path, "r", encoding="utf-8") as f:
                xml_content = f.read()

            logger.info("Procesando XML: %s (tamaño: %d caracteres)", xml_path, len(xml_content))
            
            # Verificar si el XML es demasiado largo para el prompt
            if len(xml_content) > 50000:  # 50KB
                logger.warning("XML muy largo (%d chars), truncando para el prompt", len(xml_content))
                # Tomar solo las primeras líneas relevantes
                lines = xml_content.split('\n')
                relevant_lines = []
                for line in lines:
                    if any(keyword in line.lower() for keyword in ['<de', '<gtimb', '<gdatgralope', '<gdtipde', '<gtotsub']):
                        relevant_lines.append(line)
                        if len('\n'.join(relevant_lines)) > 30000:  # 30KB
                            break
                xml_content = '\n'.join(relevant_lines)
                logger.info("XML truncado a %d caracteres", len(xml_content))

            prompt = build_xml_prompt(xml_content)
            messages = messages_user_only(prompt)
            
            logger.debug("Enviando prompt XML a OpenAI (longitud: %d chars)", len(prompt))

            raw = self.client.chat_json(
                model=self.cfg.model,
                messages=messages,
                temperature=self.cfg.temperature,
                max_completion_tokens= self.cfg.max_completion_tokens,
            )
            
            if not raw or raw.strip() == "":
                logger.error("OpenAI devolvió respuesta vacía para XML")
                raise Exception("Respuesta vacía de OpenAI")
                
            logger.debug("Respuesta de OpenAI recibida (longitud: %d chars)", len(raw))
            
            data = extract_and_normalize_json(raw)
            
            if not data:
                logger.error("No se pudo extraer JSON válido de la respuesta de OpenAI")
                raise Exception("JSON inválido en respuesta de OpenAI")
            
            logger.info("Datos extraídos del XML exitosamente: %s", str(data)[:200] + "..." if len(str(data)) > 200 else str(data))

            invoice = _coerce_invoice_model(data, email_metadata)
            invoice = validate_and_enhance_with_cdc(invoice)
            logger.info("Factura procesada exitosamente del XML")
            return invoice
            
        except Exception as e:
            logger.warning("Error procesando XML con OpenAI: %s. Intentando con PDF como respaldo...", e)
            
            # Intentar procesar el PDF correspondiente como respaldo
            try:
                # Buscar el PDF correspondiente en el mismo directorio
                xml_dir = os.path.dirname(xml_path)
                xml_name = os.path.basename(xml_path)
                
                # Buscar PDFs que puedan corresponder al mismo documento
                pdf_files = [f for f in os.listdir(xml_dir) if f.endswith('.pdf')]
                
                if pdf_files:
                    # Intentar encontrar el PDF más apropiado
                    pdf_path = None
                    for pdf_file in pdf_files:
                        if any(keyword in pdf_file.lower() for keyword in ['factura', 'comprobante', 'documento']):
                            pdf_path = os.path.join(xml_dir, pdf_file)
                            break
                    
                    # Si no encontramos uno específico, usar el primero
                    if not pdf_path and pdf_files:
                        pdf_path = os.path.join(xml_dir, pdf_files[0])
                    
                    if pdf_path:
                        logger.info("Intentando procesar PDF como respaldo: %s", pdf_path)
                        return self.extract_invoice_data(pdf_path, email_metadata)
                    else:
                        logger.warning("No se encontró PDF para respaldo")
                else:
                    logger.warning("No hay PDFs disponibles para respaldo")
                
            except Exception as pdf_fallback_error:
                logger.warning("Fallback a PDF también falló: %s", pdf_fallback_error)
            
            return None

    # ----------------------------------------------------------- Estrategias --
    def _process_as_text(self, pdf_path: str, email_metadata: Optional[Dict[str, Any]] = None):
        text = extract_text_with_fallbacks(pdf_path, try_ocr_first_page=True)
        if not text:
            return None

        # filtro Nota de Remisión
        if any(kw in text.lower() for kw in ["remisión", "nota de remisión", "remisión electrónica", "remisión de mercaderías"]):
            logger.warning("Documento detectado como Nota de Remisión. Se omite.")
            return None

        prompt = build_text_prompt(text)
        messages = messages_user_only(prompt)

        raw = self.client.chat_json(
            model=self.cfg.model,
            messages=messages,
            temperature=self.cfg.temperature,
            max_completion_tokens= self.cfg.max_completion_tokens,
        )
        try:
            data = extract_and_normalize_json(raw)
            invoice = _coerce_invoice_model(data, email_metadata)
            invoice = validate_and_enhance_with_cdc(invoice)
            return invoice
        except Exception as e:
            logger.warning("Fallo procesando JSON de texto: %s", e)
            return None

    def _process_as_image(self, pdf_path: str, email_metadata: Optional[Dict[str, Any]] = None):
        """
        Procesamiento directo por imagen usando visión por computadora.
        Más preciso para facturas paraguayas con formatos específicos.
        """
        try:
            # Convertir PDF a imagen base64
            base64_img = pdf_to_base64_first_page(pdf_path)
            logger.debug("PDF convertido a imagen base64 para procesamiento visual")
            
            # Usar directamente la visión por computadora (más preciso que OCR)
            prompt = build_image_prompt()
            messages = messages_user_with_image(prompt, base64_img)
            
            logger.info("Procesando factura con visión por computadora para máxima precisión")
            
            raw = self.client.chat_json(
                model=self.cfg.model,
                messages=messages,
                temperature=0.1,  # Más determinista para extracción de datos
                max_completion_tokens=self.cfg.max_completion_tokens,
            )
            
            if not raw or raw.strip() == "":
                logger.error("OpenAI devolvió respuesta vacía para imagen")
                return None
                
            logger.debug("Respuesta de OpenAI recibida para imagen (longitud: %d chars)", len(raw))
            
            data = extract_and_normalize_json(raw)
            
            if not data:
                logger.error("No se pudo extraer JSON válido de la respuesta de imagen")
                return None
            
            # Aplicar filtro de Nota de Remisión después de procesar
            if data.get('tipo_documento') and 'remisión' in str(data.get('tipo_documento', '')).lower():
                logger.warning("Documento detectado como Nota de Remisión por tipo. Se omite.")
                return None
                
            # Verificar si es factura válida
            if not data.get('numero_factura') or not data.get('ruc_emisor'):
                logger.warning("Documento no parece ser una factura válida (falta número o RUC)")
                return None
            
            invoice = _coerce_invoice_model(data, email_metadata)
            invoice = validate_and_enhance_with_cdc(invoice)
            
            logger.info("Factura procesada exitosamente por imagen")
            return invoice
            
        except Exception as e:
            logger.error("Error procesando PDF como imagen: %s", e)
            return None

# --------------------------------------------------------------- Helpers -----

def _coerce_invoice_model(data: Dict[str, Any], email_metadata: Optional[Dict[str, Any]]):
    """
    Intenta construir app.models.models.InvoiceData; si falla, devuelve dict con metadatos.
    """
    try:
        from app.models.models import InvoiceData  # lazy import evita ciclos
        inv = InvoiceData.from_dict(data, email_metadata)
        return inv
    except Exception:
        # Retornar dict enriquecido si el modelo no está disponible/compatible
        if email_metadata:
            data = {**data, "_email_meta": email_metadata}
        return data