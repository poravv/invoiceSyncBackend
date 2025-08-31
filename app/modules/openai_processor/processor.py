from __future__ import annotations
import logging
from typing import Any, Dict, Optional

from app.config.settings import settings
from .config import OpenAIConfig
from .clients import make_openai_client
from .pdf_text import extract_text_with_fallbacks, has_extractable_text_or_ocr
from .image_utils import pdf_to_base64_first_page, ocr_from_base64_image
from .prompts import build_text_prompt, build_image_prompt, build_xml_prompt, messages_user_only, messages_user_with_image
from .json_utils import extract_and_normalize_json
from .cdc import validate_and_enhance_with_cdc
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

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
            model=getattr(settings, "OPENAI_MODEL", "gpt-4o"),
            temperature=0.3,
            max_tokens=1500,
        )
        if not cfg.api_key:
            logger.warning("API key de OpenAI no configurada. El procesador no podrá llamar a OpenAI.")
        self.cfg = cfg
        self.client = make_openai_client(cfg.api_key)

    # ------------------------------------------------------------------ API --
    def extract_invoice_data(self, pdf_path: str, email_metadata: Optional[Dict[str, Any]] = None):
        """
        Estrategia simplificada (segura):
        1) Procesar como Imagen (OCR/Vision) → OpenAI
        2) Filtro de 'Nota de Remisión'
        """
        try:
            # NOTA: Se desactiva el camino 'texto' por solicitud.
            # Mantener este bloque comentado por si se necesita reactivar en el futuro.
            # if has_extractable_text_or_ocr(pdf_path):
            #     result = self._process_as_text(pdf_path, email_metadata)
            #     if result:
            #         return result
            #     logger.warning("Texto falló → intentamos por imagen")

            # Ir directo a la estrategia por imagen (Vision/OCR)
            result = self._process_as_image(pdf_path, email_metadata)
            if result:
                return result

            logger.warning("Ambas estrategias fallaron")
            return None
        except Exception as e:
            logger.exception("Error en extract_invoice_data: %s", e)
            return None

    def extract_invoice_data_from_xml(self, xml_path: str, email_metadata: dict | None = None):
        try:
            import os
            if not os.path.exists(xml_path):
                logger.warning("XML no existe: %s", xml_path)
                return None
            with open(xml_path, "r", encoding="utf-8") as f:
                xml_content = f.read()

            def _extract_cdc_id(xml_text: str) -> str | None:
                try:
                    root = ET.fromstring(xml_text)
                    # Buscar nodo DE en cualquier namespace (localname == 'DE', no 'rDE')
                    def _find_de(el: ET.Element):
                        if isinstance(el.tag, str) and el.tag.split('}')[-1] == 'DE':
                            return el
                        for ch in el:
                            r = _find_de(ch)
                            if r is not None:
                                return r
                        return None
                    de = _find_de(root)
                    if de is not None:
                        cid = de.attrib.get('Id')
                        if cid and cid.isdigit() and len(cid) == 44:
                            return cid
                    return None
                except Exception:
                    return None

            # 1) Intentar parser nativo SIFEN (rápido y determinista)
            try:
                from .xml_parser import parse_paraguayan_xml
                ok, native = parse_paraguayan_xml(xml_content)
                if ok:
                    logger.info("XML parseado nativamente (SIFEN)")
                    # Asegurar que CDC venga del atributo Id
                    cdc_id = _extract_cdc_id(xml_content)
                    if cdc_id:
                        native['cdc'] = cdc_id
                    try:
                        logger.info(
                            "XML nativo normalizado: fecha=%s, nro=%s, ruc_emisor=%s, nombre_emisor=%s, cond_venta=%s, moneda=%s, exentas=%s, g5=%s, iva5=%s, g10=%s, iva10=%s, total=%s, timbrado=%s, cdc=%s, ruc_cliente=%s, nombre_cliente=%s, productos=%s",
                            native.get('fecha'), native.get('numero_factura'), native.get('ruc_emisor'), native.get('nombre_emisor'),
                            native.get('condicion_venta'), native.get('moneda'), native.get('subtotal_exentas'), native.get('subtotal_5'), native.get('iva_5'),
                            native.get('subtotal_10'), native.get('iva_10'), native.get('monto_total'), native.get('timbrado'), native.get('cdc'),
                            native.get('ruc_cliente'), native.get('nombre_cliente'), len(native.get('productos') or [])
                        )
                    except Exception:
                        pass
                    invoice = _coerce_invoice_model(native, email_metadata)
                    invoice = validate_and_enhance_with_cdc(invoice)
                    try:
                        logger.info(
                            "Invoice mapeada: fecha=%s, nro=%s, ruc=%s, razon=%s, cond=%s, moneda=%s, gra5=%s, iva5=%s, gra10=%s, iva10=%s, exento=%s, total=%s, timbrado=%s, cdc=%s, productos=%s",
                            getattr(invoice, 'fecha', None), getattr(invoice, 'numero_factura', ''), getattr(invoice, 'ruc_emisor', ''), getattr(invoice, 'nombre_emisor', ''),
                            getattr(invoice, 'condicion_venta', ''), getattr(invoice, 'moneda', ''), getattr(invoice, 'gravado_5', 0), getattr(invoice, 'iva_5', 0),
                            getattr(invoice, 'gravado_10', 0), getattr(invoice, 'iva_10', 0), getattr(invoice, 'subtotal_exentas', 0), getattr(invoice, 'monto_total', 0),
                            getattr(invoice, 'timbrado', ''), getattr(invoice, 'cdc', ''), len(getattr(invoice, 'productos', []) or [])
                        )
                    except Exception:
                        pass
                    return invoice
                else:
                    logger.info("Parser nativo no suficiente, fallback a OpenAI")
            except Exception as e:
                logger.warning("Parser nativo falló: %s. Se usa OpenAI como fallback", e)

            # 2) Fallback: usar OpenAI con prompt XML
            prompt = build_xml_prompt(xml_content)
            messages = messages_user_only(prompt)

            raw = self.client.chat_json(
                model=self.cfg.model,
                messages=messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
            )
            data = extract_and_normalize_json(raw)
            logger.info("Datos extraídos del XML (OpenAI): %s", data)
            # Forzar CDC desde atributo Id si está presente
            cdc_id = _extract_cdc_id(xml_content)
            if cdc_id:
                data['cdc'] = cdc_id
            invoice = _coerce_invoice_model(data, email_metadata)
            invoice = validate_and_enhance_with_cdc(invoice)
            return invoice
        except Exception as e:
            logger.exception("Error procesando XML: %s", e)
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
            max_tokens=self.cfg.max_tokens,
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
        base64_img = pdf_to_base64_first_page(pdf_path)
        # OCR rápido como atajo si el Vision falla o para texto dominante
        ocr_text = ocr_from_base64_image(base64_img)
        if ocr_text:
            # filtro Nota de Remisión
            if any(kw in ocr_text.lower() for kw in ["nota de remisión", "remisión electrónica", "nota de entrega", "remisión de mercaderías"]):
                logger.warning("Documento detectado como Nota de Remisión. Se omite.")
                return None
            prompt = build_text_prompt(ocr_text)
            messages = messages_user_only(prompt)
        else:
            prompt = build_image_prompt()
            messages = messages_user_with_image(prompt, base64_img)

        raw = self.client.chat_json(
            model=self.cfg.model,
            messages=messages,
            temperature=0.1 if not ocr_text else self.cfg.temperature,  # más determinista para imagen
            max_tokens=self.cfg.max_tokens,
        )
        try:
            data = extract_and_normalize_json(raw)
            invoice = _coerce_invoice_model(data, email_metadata)
            invoice = validate_and_enhance_with_cdc(invoice)
            return invoice
        except Exception as e:
            logger.warning("Fallo procesando JSON de imagen: %s", e)
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
