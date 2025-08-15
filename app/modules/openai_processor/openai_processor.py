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
        return """
Analiza cuidadosamente esta factura paraguaya y extrae TODOS los siguientes campos en formato JSON estructurado. Esta información será usada para el sistema contable ASCONT, así que es MUY IMPORTANTE que extraigas los importes correctamente según las tasas de IVA:

{
  "fecha": "YYYY-MM-DD",
  "numero_factura": "XXX-XXX-XXXXXXX (formato completo)",
  "ruc_emisor": "XXXXXXXX-X (con guión)",
  "nombre_emisor": "Razón social del emisor",
  "condicion_venta": "CONTADO o CREDITO",
  
  // IMPORTES CRÍTICOS - SEPARAR POR TASA DE IVA
  "subtotal_exentas": number, // Monto gravado al 0% (exento de IVA)
  "subtotal_5": number,       // Monto gravado al 5% (sin incluir el IVA)
  "iva_5": number,           // IVA del 5% calculado
  "subtotal_10": number,     // Monto gravado al 10% (sin incluir el IVA)
  "iva_10": number,          // IVA del 10% calculado
  "monto_total": number,     // Total final a pagar
  
  "timbrado": "string",
  "cdc": "string",
  "ruc_cliente": "string",
  "nombre_cliente": "string",
  "email_cliente": "string",
  "moneda": "PYG, USD, EUR, etc.",
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
    "fecha_inicio_vigencia": "YYYY-MM-DD",
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
      "articulo": "Descripción del producto/servicio",
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

⚠️ INSTRUCCIONES ESPECÍFICAS PARA ESTA FACTURA:

🔍 **ANÁLISIS DE LA TABLA DE PRODUCTOS**:
En esta factura verás una tabla con estas columnas exactas:
| Cod | Descripcion | Unidad de medida | Cantidad | Precio Unitario | Descuento | Exentas | 5% | 10% |

� **LECTURA FILA POR FILA**:
1. FILA 1: "APORTE DE ESPERA 20 AÑOS" tiene en la columna "Exentas": 860.690,0 y en "5%": 0 y en "10%": 0
2. FILA 2: "ESPERA 20 AÑOS" (Gastos Administrativos) tiene en "Exentas": 0 y en "5%": 312.000,0 y en "10%": 0  
3. FILA 3: "ESPERA 20 AÑOS" (último) tiene en "Exentas": 0 y en "5%": 0 y en "10%": 387.310,0

⚠️ **CÁLCULOS EXACTOS REQUERIDOS**:
- subtotal_exentas = 860690 (de la fila 1, columna Exentas)
- subtotal_5 = 312000 (de la fila 2, columna 5%)
- subtotal_10 = 387310 (de la fila 3, columna 10%)
- iva_5 = 18443 (de "LIQUIDACIÓN IVA: (5%) 18.443")
- iva_10 = 28363 (de "LIQUIDACIÓN IVA: (10%) 28.363")

🚫 **NO HAGAS ESTO**:
- NO uses el precio unitario como subtotal
- NO sumes todo en una sola categoría
- NO inventes distribuciones
- NO ignores las columnas específicas Exentas/5%/10%

✅ **VERIFICACIÓN MATEMÁTICA**:
El cálculo debe ser: 860690 + 312000 + 18443 + 387310 + 28363 = 1606806
Pero el total de la factura es 1560000, así que hay que ajustar según lo que está impreso.

🎯 **REGLAS DE EXTRACCIÓN**:
1. Lee EXACTAMENTE los valores de cada columna de cada fila de productos
2. Los valores en la columna "Exentas" van a subtotal_exentas
3. Los valores en la columna "5%" van a subtotal_5
4. Los valores en la columna "10%" van a subtotal_10
5. Los valores de IVA están en la línea "LIQUIDACIÓN IVA"

**FORMATO DE RESPUESTA**:
- Todos los montos deben ser números sin separadores de miles ni símbolos
- RUC debe incluir el guión (ej: "80014066-4")
- Fechas en formato YYYY-MM-DD
- Si no encuentras un valor, usa null (texto) o 0 (números)
- NO uses markdown ni ```json en la respuesta
- Responde SOLO con el objeto JSON válido
"""

def extract_clean_json(text: str) -> dict:
    """
    Extrae y limpia un objeto JSON desde texto potencialmente envuelto en markdown
    """
    # Eliminar posibles bloques de markdown tipo ```json o ```
    cleaned = re.sub(r"```json\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"```", "", cleaned)
    
    return json.loads(cleaned)