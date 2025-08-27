from __future__ import annotations
import json
import re
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

def extract_and_normalize_json(text: str) -> Dict[str, Any]:
    content = _extract_json_block(text)
    data = json.loads(content)
    data = normalize_fields(data)
    data = autocorrect_iva_consistency(data)
    data = coerce_none_strings(data)        # ← NUEVO
    data = fix_product_iva_percent(data)    # ← NUEVO
    data = backfill_bases_from_iva(data)  # ← NUEVO: completa subtotal_* desde IVA si faltan
    data = backfill_total_if_missing(data)  # ← opcional: completa monto_total si falta
    return data

def _extract_json_block(text: str) -> str:
    # 1) bloque ```json
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1)
    # 2) primer objeto { ... }
    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        return m.group(1)
    # 3) limpieza agresiva
    cleaned = re.sub(r"```json\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"```.*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^.*?(\{)", r"\1", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(\}).*$", r"\1", cleaned, flags=re.DOTALL)
    return cleaned.strip()

def normalize_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Alinea tipos y formatos esperados: numéricos, strings, CDC, moneda, tipo_cambio, condicion_venta, etc.
    """
    # numéricos que a veces vienen como lista o string
    for field in ["subtotal_exentas", "subtotal_5", "iva_5", "subtotal_10", "iva_10", "monto_total"]:
        if field in data:
            data[field] = _to_float_safe(data[field])

    # strings que llegan como lista
    for field in ["numero_factura", "ruc_emisor", "nombre_emisor", "fecha", "timbrado"]:
        if field in data and isinstance(data[field], list):
            data[field] = data[field][0] if data[field] else ""

    # CDC sin espacios
    if "cdc" in data and isinstance(data["cdc"], str):
        data["cdc"] = re.sub(r"\s+", "", data["cdc"])

    # condicion_venta
    if "condicion_venta" in data:
        cv = data["condicion_venta"]
        if cv is None:
            data["condicion_venta"] = "CONTADO"
        elif isinstance(cv, list):
            data["condicion_venta"] = cv[0] if cv else "CONTADO"

    # moneda
    if "moneda" in data:
        moneda = str(data["moneda"]).strip().upper()
        if moneda in ["USD", "DOLAR", "DOLLAR", "$"]:
            data["moneda"] = "USD"
        elif moneda in ["PYG", "GUARANI", "GS", "G$"]:
            data["moneda"] = "PYG"
        else:
            data["moneda"] = "PYG"

    # tipo_cambio
    if "tipo_cambio" in data:
        try:
            data["tipo_cambio"] = float(str(data["tipo_cambio"]).replace(",", "."))
        except Exception:
            data["tipo_cambio"] = None

    return data

def _to_float_safe(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        value = value[0] if value else 0
    try:
        s = re.sub(r"[^\d.,-]", "", str(value))
        s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 and s.count(".") > 1 else s.replace(",", ".")
        return float(s)
    except Exception:
        return 0.0

def autocorrect_iva_consistency(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Si todos los productos vienen con iva=0 pero el resumen muestra un único IVA no cero,
    ajusta los productos para que coincidan (5% o 10%).
    """
    try:
        productos: List[Dict[str, Any]] = data.get("productos", []) or []
        if not productos:
            return data
        iva_5 = float(data.get("iva_5", 0) or 0)
        iva_10 = float(data.get("iva_10", 0) or 0)
        todos_sin_iva = all(str(p.get("iva", 0)).strip() in ["0", ""] for p in productos)

        if todos_sin_iva:
            if iva_5 > 0 and iva_10 == 0:
                for p in productos:
                    p["iva"] = 5
            elif iva_10 > 0 and iva_5 == 0:
                for p in productos:
                    p["iva"] = 10
            data["productos"] = productos
        return data
    except Exception as e:
        logger.warning("autocorrect_iva_consistency error: %s", e)
        return data
    
def backfill_bases_from_iva(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Si subtotal_10/subtotal_5 están ausentes o en cero, y hay IVA correspondiente > 0,
    calcula gravados con reglas paraguayas:
      - gravado 10%: subtotal_10 = iva_10 * 11
      - gravado  5%: subtotal_5  = iva_5  * 21
    """
    try:
        iva10 = float(data.get("iva_10") or 0)
        iva5  = float(data.get("iva_5") or 0)

        sub10 = float(data.get("subtotal_10") or 0)
        sub5  = float(data.get("subtotal_5") or 0)

        if sub10 == 0 and iva10 > 0:
            data["subtotal_10"] = round(iva10 * 11, 2)
        if sub5 == 0 and iva5 > 0:
            data["subtotal_5"] = round(iva5 * 21, 2)

        return data
    except Exception as e:
        logger.warning("backfill_bases_from_iva error: %s", e)
        return data

def backfill_total_if_missing(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Si falta monto_total, estimar como suma de gravados + IVA + exentas.
    (No reemplaza el total impreso si ya viene informado).
    """
    try:
        total = _to_float_safe(data.get("monto_total"))
        if total > 0:
            return data
        ex = _to_float_safe(data.get("subtotal_exentas"))
        s5 = _to_float_safe(data.get("subtotal_5"))
        i5 = _to_float_safe(data.get("iva_5"))
        s10 = _to_float_safe(data.get("subtotal_10"))
        i10 = _to_float_safe(data.get("iva_10"))
        data["monto_total"] = round(ex + s5 + i5 + s10 + i10, 2)
        return data
    except Exception:
        return data
    
def coerce_none_strings(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convierte "None"/"null"/"" en None para campos conocidos.
    """
    def _noney(v):
        if isinstance(v, str) and v.strip().lower() in {"none", "null", ""}:
            return None
        return v

    # Campos simples
    for k in ["tipo_cambio", "actividad_economica", "timbrado", "cdc",
              "ruc_cliente", "nombre_cliente", "email_cliente",
              "numero_factura", "fecha", "ruc_emisor", "nombre_emisor", "moneda"]:
        if k in data:
            data[k] = _noney(data[k])

    # timbrado_data / factura_data
    if isinstance(data.get("timbrado_data"), dict):
        for k in ["nro", "fecha_inicio_vigencia", "valido_hasta"]:
            if k in data["timbrado_data"]:
                data["timbrado_data"][k] = _noney(data["timbrado_data"][k])
    if isinstance(data.get("factura_data"), dict):
        for k in ["contado_nro", "fecha", "caja_nro", "cdc", "condicion_venta"]:
            if k in data["factura_data"]:
                data["factura_data"][k] = _noney(data["factura_data"][k])

    # empresa / cliente
    if isinstance(data.get("empresa"), dict):
        for k in ["nombre", "ruc", "direccion", "telefono", "actividad_economica"]:
            if k in data["empresa"]:
                data["empresa"][k] = _noney(data["empresa"][k])
    if isinstance(data.get("cliente"), dict):
        for k in ["nombre", "ruc", "email"]:
            if k in data["cliente"]:
                data["cliente"][k] = _noney(data["cliente"][k])

    return data

def fix_product_iva_percent(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Si 'productos[i].iva' no es 0/5/10 sino un monto (por ejemplo 3227),
    lo corrige a porcentaje usando el resumen (si hay iva_5 o iva_10 > 0).
    """
    try:
        prods = data.get("productos") or []
        if not isinstance(prods, list) or not prods:
            return data

        iva_5_sum  = float(data.get("iva_5") or 0)
        iva_10_sum = float(data.get("iva_10") or 0)

        for p in prods:
            iva_val = p.get("iva", 0)
            # ya está bien
            if str(iva_val) in {"0", "5", "10"} or iva_val in (0, 5, 10):
                continue
            # si es numérico grande, probablemente es monto
            try:
                iva_num = float(iva_val)
            except Exception:
                continue

            # decide % por resumen
            if iva_10_sum > 0 and iva_5_sum == 0:
                p["iva"] = 10
            elif iva_5_sum > 0 and iva_10_sum == 0:
                p["iva"] = 5
            else:
                # si ambos 0, déjalo en 0; si ambos >0, no podemos decidir por ítem
                p["iva"] = 0

        data["productos"] = prods
        return data
    except Exception:
        return data