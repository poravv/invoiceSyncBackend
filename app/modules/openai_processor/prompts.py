# app/modules/openai_processor/prompts.py

from __future__ import annotations
import json
from typing import Dict, Any

def base_text_schema() -> Dict[str, Any]:
    return {
        "fecha": "YYYY-MM-DD",
        "numero_factura": "XXX-XXX-XXXXXXX",
        "ruc_emisor": "XXXXXXXX-X",
        "nombre_emisor": "Razón social completa",
        "condicion_venta": "CONTADO",
        "tipo_cambio": None,  # si corresponde (USD)
        "subtotal_exentas": 0,
        "subtotal_5": 0,      # = gravado 5% (base imponible, sin IVA)
        "iva_5": 0,
        "subtotal_10": 0,     # = gravado 10% (base imponible, sin IVA)
        "iva_10": 0,
        "monto_total": 0,
        "timbrado": None,
        "cdc": None,
        "ruc_cliente": None,
        "nombre_cliente": None,
        "email_cliente": None,
        "moneda": "PYG",
        "actividad_economica": None,
        "empresa": {
            "nombre": None,
            "ruc": None,
            "direccion": None,
            "telefono": None,
            "actividad_economica": None,
        },
        "timbrado_data": {
            "nro": None,
            "fecha_inicio_vigencia": None,
            "valido_hasta": None,
        },
        "factura_data": {
            "contado_nro": None,
            "fecha": None,
            "caja_nro": None,
            "cdc": None,
            "condicion_venta": "CONTADO",
        },
        "productos": [
            {
                "articulo": None,
                "cantidad": 1,
                "precio_unitario": 0,
                "total": 0,
                "iva": 0   # 0, 5 o 10
            }
        ],
        "totales": {
            "cantidad_articulos": 0,
            "subtotal": 0,
            "total_a_pagar": 0,
            "iva_0%": 0,
            "iva_5%": 0,
            "iva_10%": 0,
            "total_iva": 0,
        },
        "cliente": {"nombre": None, "ruc": None, "email": None},
    }

def build_text_prompt(pdf_text: str) -> str:
    schema = json.dumps(base_text_schema(), ensure_ascii=False, indent=2)
    return f"""
Analiza cuidadosamente el siguiente contenido textual de una factura paraguaya.
Devuelve **solo** un JSON válido y completo (sin explicaciones) con esta estructura:

{schema}

📌 Reglas:
- Usa los valores y tipos EXACTOS que aparecen en el documento.
- **Definición clave**: `subtotal_5` y `subtotal_10` son los **montos gravados (base imponible, sin IVA)**.
- Si el documento **solo muestra el IVA** y no el gravado:
  - Calcula: `subtotal_10 = iva_10 * 11` (en Paraguay, IVA 10% = gravado/11).
  - Calcula: `subtotal_5  = iva_5  * 21` (en Paraguay, IVA 5%  = gravado/21).
- Si hay columna de IVA por ítem, úsala como fuente de verdad; si no hay, aplica el IVA único del resumen; si tampoco hay, asume exento.
- Nunca infieras IVA por nombre del producto.
- Moneda: si la factura está en USD, usa "USD" y agrega "tipo_cambio" si está impreso. No conviertas a PYG.

Texto:
{pdf_text}
""".strip()

def build_image_prompt() -> str:
    schema = json.dumps(base_text_schema(), ensure_ascii=False, indent=2)
    return f"""
Analiza con extrema atención la imagen de una factura paraguaya y devuelve **solo** un JSON válido según:

{schema}

Reglas adicionales:
- **Definición clave**: `subtotal_5` y `subtotal_10` son los **montos gravados (base imponible, sin IVA)**.
- Si el documento **solo muestra el IVA** y no el gravado:
  - Calcula: `subtotal_10 = iva_10 * 11`.
  - Calcula: `subtotal_5  = iva_5  * 21`.
- Si hay columna de IVA por ítem, úsala como fuente de verdad; si no hay, usa el IVA único del resumen; si tampoco hay, asume exento.
- Respeta montos y decimales tal como están impresos. No conviertas moneda.
""".strip()

def build_xml_prompt(xml_content: str) -> str:
    schema = json.dumps(base_text_schema(), ensure_ascii=False, indent=2)
    return f"""
A continuación se provee el contenido de un XML de factura electrónica paraguaya.
Devuelve **solo** un JSON válido con la siguiente estructura, sin texto adicional:

{schema}

Reglas de totales:
- `subtotal_5` y `subtotal_10` son los **gravados sin IVA**.
- Si el XML solo provee IVA discriminado y no el gravado:
  - `subtotal_10 = iva_10 * 11`
  - `subtotal_5  = iva_5  * 21`

XML:
```xml
{xml_content}
""".strip()

def messages_user_only(prompt: str) -> list[dict]:
    return [{"role": "user", "content": prompt}]

def messages_user_with_image(prompt: str, base64_image_jpeg: str) -> list[dict]:
    return [{
    "role": "user",
    "content": [
    {"type": "text", "text": prompt},
    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image_jpeg}"}}
    ],
    }]