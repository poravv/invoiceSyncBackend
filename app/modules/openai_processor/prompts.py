# app/modules/openai_processor/prompts.py

from __future__ import annotations
import json
from typing import Dict, Any

def base_text_schema() -> Dict[str, Any]:
    """
    Esquema optimizado - solo campos que se usan en el export.
    Eliminados campos innecesarios para ahorrar tokens.
    """
    return {
        # CAMPOS PRINCIPALES (siempre requeridos)
        "fecha": "YYYY-MM-DD",
        "numero_factura": "XXX-XXX-XXXXXXX",
        "ruc_emisor": "XXXXXXXX-X",
        "nombre_emisor": "Razón social completa",
        "condicion_venta": "CONTADO o CREDITO",
        "tipo_documento": "CO o CR",
        
        # MONEDA Y CAMBIO
        "tipo_cambio": "None o valor si está en moneda extranjera",
        "moneda": "GS PYG o USD",
        
        # TOTALES PRINCIPALES (siempre requeridos)
        "subtotal_exentas": 0,
        "subtotal_5": 0,      # = gravado 5% (base imponible, sin IVA)
        "iva_5": 0,
        "subtotal_10": 0,     # = gravado 10% (base imponible, sin IVA)
        "iva_10": 0,
        "monto_total": 0,
        
        # IDENTIFICADORES
        "timbrado": "None o NumTim",
        "cdc": "None o CDC (DE Id)",
        
        # CLIENTE
        "ruc_cliente": "None o ruc cliente",
        "nombre_cliente": "None o nombre cliente",
        "email_cliente": "None o email cliente",
        
        # PRODUCTOS (solo campos esenciales)
        "productos": [
            {
                "articulo": "descripción del producto/servicio",
                "cantidad": 1,
                "precio_unitario": 0,
                "total": 0,
                "iva": 0   # 0, 5 o 10
            }
        ]
    }

# CAMPOS ELIMINADOS (no se usan en export):
# - actividad_economica
# - empresa (nombre, ruc, direccion, telefono, actividad_economica)
# - timbrado_data (nro, fecha_inicio_vigencia, valido_hasta)
# - factura_data (contado_nro, fecha, caja_nro, cdc, condicion_venta)
# - totales (cantidad_articulos, subtotal, total_a_pagar, iva_0%, iva_5%, iva_10%, total_iva)
# - cliente (nombre, ruc, email) - duplicado

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
  - Calcula: `subtotal_10 = iva_10 * 10` (en Paraguay, IVA 10% = gravado/10).
  - Calcula: `subtotal_5  = iva_5  * 20` (en Paraguay, IVA 5%  = gravado/20).
- Si hay columna de IVA por ítem, úsala como fuente de verdad; si no hay, aplica el IVA único del resumen; si tampoco hay, asume exento.
- Nunca infieras IVA por nombre del producto.
- Moneda: si la factura está en USD, usa "USD" y agrega "tipo_cambio" si está impreso. No conviertas a PYG.
- El campo `moneda` debe ser:
  - "GS" si la factura está en Guaraníes (PYG).
  - "USD" si la factura está en Dólares.
  - Otras monedas, exactamente como aparecen en la factura.
- Si la moneda es extranjera, agregar el valor de `tipo_cambio` si figura impreso.
- Nunca convertir montos de una moneda a otra.

Texto:
{pdf_text}
""".strip()

def build_image_prompt() -> str:
    schema = json.dumps(base_text_schema(), ensure_ascii=False, indent=2)
    return f"""
Analiza con extrema atención la imagen de una factura paraguaya y devuelve **solo** un JSON válido según:

{schema}

📌 Reglas importantes:
- **Definición clave**: `subtotal_5` y `subtotal_10` son los **montos gravados (base imponible, sin IVA)**.
- Si el documento **solo muestra el IVA** y no el gravado:
  - Calcula: `subtotal_10 = iva_10 * 10`.
  - Calcula: `subtotal_5  = iva_5  * 20`.
- Respeta montos y decimales tal como están impresos. No conviertas moneda.
- El campo `condicion_venta` debe ser exactamente `"CONTADO"` o `"CREDITO"`.
- El campo `tipo_documento` debe ser `"CO"` si es CONTADO, o `"CR"` si es CREDITO.
- **Moneda**: "GS" para Guaraníes, "USD" para Dólares (mantener decimales para USD).
""".strip()

def build_xml_prompt(xml_content: str) -> str:
    # Esquema simplificado para XML - solo campos esenciales
    simplified_schema = {
        "fecha": "YYYY-MM-DD",
        "numero_factura": "XXX-XXX-XXXXXXX",
        "ruc_emisor": "XXXXXXXX-X",
        "nombre_emisor": "Razón social completa",
        "condicion_venta": "CONTADO o CREDITO",
        "tipo_documento": "CO o CR",
        "tipo_cambio": "None o valor si está en moneda extranjera",
        "subtotal_exentas": 0,
        "subtotal_5": 0,      # gravado 5% sin IVA
        "iva_5": 0,
        "subtotal_10": 0,     # gravado 10% sin IVA
        "iva_10": 0,
        "monto_total": 0,
        "timbrado": "None o NumTim",
        "cdc": "None o CDC (DE Id)",
        "ruc_cliente": "None o ruc cliente",
        "nombre_cliente": "None o nombre cliente",
        "email_cliente": "None o email cliente",
        "moneda": "GS PYG o USD",
        "actividad_economica": None,
        "productos": [
            {
                "articulo": "descripción del producto/servicio",
                "cantidad": 1,
                "precio_unitario": 0,
                "total": 0,
                "iva": 0
            }
        ]
    }
    
    schema = json.dumps(simplified_schema, ensure_ascii=False, indent=2)
    
    return f"""
Analiza este XML de factura electrónica paraguaya y devuelve **solo** un JSON válido:

{schema}

📌 Reglas importantes:
- `subtotal_5` y `subtotal_10` son los **montos gravados sin IVA**
- Si solo hay IVA: `subtotal_10 = iva_10 * 10`, `subtotal_5 = iva_5 * 20`
- Moneda: "GS" para Guaraníes, "USD" para Dólares
- No convertir monedas, usar valores exactos del XML

XML:
```xml
{xml_content}
```
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