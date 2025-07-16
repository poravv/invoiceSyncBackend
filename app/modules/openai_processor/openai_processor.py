import base64
import fitz  # PyMuPDF
import logging
from datetime import datetime
from typing import Dict, Any

from PyPDF2 import PdfReader
from pdfminer.high_level import extract_text
import openai
from datetime import datetime
from app.models.models import InvoiceData
from app.config.settings import settings
import json
import re
#from app.utils.date_utils import try_parse_date

logger = logging.getLogger(__name__)

class OpenAIProcessor:
    def __init__(self):
        openai.api_key = settings.OPENAI_API_KEY

    def extract_invoice_data(self, pdf_path: str, email_metadata: Dict[str, Any] = None) -> InvoiceData:
        if self._pdf_has_text(pdf_path):
            logger.info("Procesando PDF como texto")
            pdf_text = extract_text(pdf_path).strip()
            prompt = self._build_prompt() + "\n\nTexto de la factura:\n" + pdf_text
            messages = [{"role": "user", "content": prompt}]
        else:
            logger.info("Procesando PDF como imagen")
            image_data = self._convert_pdf_to_image(pdf_path)
            prompt = self._build_prompt()
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                ]
            }]

        try:
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=1000,
                temperature=0.3
            )

            raw_output = response.choices[0].message.content
            #logger.info(f"Respuesta recibida desde OpenAI: {raw_output[:200]}...")
            logger.info(f"Respuesta OpenAI bruta: {raw_output}")
            try:
                json_data = extract_clean_json(raw_output)
                if not isinstance(json_data, dict):
                    raise ValueError("La respuesta no es un objeto JSON válido")

                invoice = InvoiceData.from_dict(json_data, email_metadata)
                if invoice is None:
                    raise ValueError("El resultado de from_dict fue None")

                return invoice

            except Exception as e:
                logger.error(f"Error al procesar la factura con OpenAI: {str(e)}")
                return None


        except Exception as e:
            logger.error(f"Error al procesar la factura con OpenAI: {str(e)}")
            raise

    def _pdf_has_text(self, pdf_path: str) -> bool:
        try:
            reader = PdfReader(pdf_path)
            for page in reader.pages:
                text = page.extract_text()
                if text and text.strip():
                    return True
            return False
        except Exception as e:
            logger.warning(f"No se pudo determinar si el PDF tiene texto: {str(e)}")
            return False

    def _convert_pdf_to_image(self, pdf_path: str) -> str:
        try:
            doc = fitz.open(pdf_path)
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=200)
            image_bytes = pix.tobytes("jpeg")
            base64_image = base64.b64encode(image_bytes).decode("utf-8")
            return base64_image
        except Exception as e:
            logger.error(f"Error al convertir PDF a imagen: {str(e)}")
            raise

    def _build_prompt(self) -> str:
        return  """
Analiza cuidadosamente esta factura y extrae TODOS los siguientes campos en formato JSON estructurado (es muy importante que devuelvas TODOS los campos, incluso si están vacíos):

{
  "fecha": "YYYY-MM-DD",
  "ruc_emisor": "string",
  "nombre_emisor": "string",
  "numero_factura": "string",
  "monto_total": number,
  "iva": number,
  "timbrado": "string",
  "cdc": "string",
  "ruc_cliente": "string",
  "nombre_cliente": "string",
  "email_cliente": "string",
  "condicion_venta": "CONTADO o CREDITO",
  "moneda": "string",
  "subtotal_exentas": number,
  "subtotal_5": number,
  "subtotal_10": number,
  "actividad_economica": "string",

  "empresa": {
    "nombre": "string",
    "ruc": "string",
    "direccion": "string",
    "telefono": "string",
    "actividad_economica": "string"
  },
  "timbrado_data": {
    "nro": "string",
    "fecha_inicio_vigencia": "YYYY-MM-DD" puede llamarse inicio de vigencia o vigencia,
    "valido_hasta": "YYYY-MM-DD"
  },
  "factura_data": {
    "contado_nro": "string",
    "fecha": "YYYY-MM-DD",
    "caja_nro": "string",
    "cdc": "string",
    "condicion_venta": "CONTADO o CREDITO"
  },
  "productos": [
    {
      "articulo": "string",
      "cantidad": number,
      "precio_unitario": number,
      "total": number
    }
  ],
  "totales": {
    "cantidad_articulos": number,
    "subtotal": number,
    "total_a_pagar": number,
    "iva_0%": number,
    "iva_5%": number,
    "iva_10%": number,
    "total_iva": number
  },
  "cliente": {
    "nombre": "string",
    "ruc": "string",
    "email": "string"
  }
}

⚠️ REGLAS IMPORTANTES:
- TODOS los campos son obligatorios. Si no encuentras el valor, devuelve null (texto) o 0 (números).
- Los montos deben ser SOLO números, sin símbolos ni separadores de miles.
- Las fechas deben estar en formato YYYY-MM-DD.
- La respuesta debe ser un JSON válido, sin errores de formato.
- NO uses markdown ni encierres la respuesta en bloques ```json.
- NO incluyas explicaciones ni comentarios, solo el objeto JSON limpio.
"""

def extract_clean_json(text: str) -> dict:
    """
    Extrae y limpia un objeto JSON desde texto potencialmente envuelto en ```json ... ``` o ```
    """
    # Eliminar posibles bloques de markdown tipo ```json o ```
    cleaned = re.sub(r"```json\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"```", "", cleaned)
    
    return json.loads(cleaned)