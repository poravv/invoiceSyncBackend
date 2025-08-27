from __future__ import annotations

import os
import re
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from app.models.models import InvoiceData, ExcelFileInfo
from app.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------
# Parsing y normalización
# ---------------------------

def parse_monto(valor: Any, enteros: bool = True) -> float:
    """Convierte a número. Si enteros=True redondea (para PYG)."""
    try:
        n = float(valor or 0)
        return int(n) if enteros else n
    except Exception:
        logger.debug("parse_monto: valor inválido %r", valor)
        return 0 if enteros else 0.0


def normalizar_condicion_compra(condicion_venta: Optional[str]) -> str:
    if not condicion_venta:
        return "CONTADO"
    cu = condicion_venta.upper()
    if "CREDITO" in cu or "CRÉDITO" in cu or "CREDIT" in cu:
        return "CREDITO"
    return "CONTADO"


def determinar_tipo_documento_real(condicion_venta: Optional[str], condicion_compra: Optional[str]) -> str:
    """
    Tipo requerido por tu ASCONT:
    - "CO" contado
    - "CR" crédito
    """
    base = (condicion_venta or condicion_compra or "").upper()
    return "CR" if ("CREDITO" in base or "CRÉDITO" in base or "CREDIT" in base) else "CO"


def formatear_cdc(cdc: Optional[str]) -> str:
    if not cdc:
        return ""
    limpio = cdc.replace(" ", "").replace("-", "")
    if len(limpio) < 8:
        return cdc
    # separa cada 4 dígitos
    return " ".join(limpio[i:i+4] for i in range(0, len(limpio), 4))


def formatear_email_origen(email_origen: Optional[str]) -> str:
    if not email_origen:
        return ""
    if "<" in email_origen and ">" in email_origen:
        return email_origen
    if "@" in email_origen:
        nombre = email_origen.split("@")[0].replace(".", " ").title()
        return f"{nombre} <{email_origen}>"
    return email_origen


def generar_detalle_articulos(invoice: InvoiceData) -> str:
    try:
        if getattr(invoice, "detalle_articulos", None):
            return invoice.detalle_articulos
        if not getattr(invoice, "productos", None):
            return ""
        articulos: List[str] = []
        for p in invoice.productos:
            if isinstance(p, dict):
                a = p.get("articulo") or ""
            else:
                a = getattr(p, "articulo", "") or ""
            if a:
                articulos.append(str(a))
        return ", ".join(articulos)
    except Exception as e:
        logger.debug("generar_detalle_articulos error: %s", e)
        return ""


def recalcular_totales_desde_productos(invoice: InvoiceData) -> None:
    """
    Recalcula subtotales / IVA y gravados desde productos **solo si faltan**.
    Define:
      subtotal_X = total IVA-incluido (por tu planilla)
      iva_X      = subtotal_X * tasa/(100+tasa)   (5/105, 10/110)
      gravado_X  = subtotal_X - iva_X
    """
    if not getattr(invoice, "productos", None):
        return

    try:
        is_usd = str(getattr(invoice, "moneda", "")).upper() == "USD"
        rnd = (lambda x: x) if is_usd else (lambda x: round(x))

        # suma por porcentaje IVA del item
        def sum_where(v: int) -> float:
            total = 0.0
            for p in invoice.productos:
                iva_item = (p.get("iva") if isinstance(p, dict) else getattr(p, "iva", 0)) or 0
                total_item = (p.get("total") if isinstance(p, dict) else getattr(p, "total", 0)) or 0
                if int(iva_item) == v:
                    total += float(total_item)
            return total

        s_ex = sum_where(0)
        s5   = sum_where(5)
        s10  = sum_where(10)

        if not invoice.subtotal_exentas:
            invoice.subtotal_exentas = rnd(s_ex)
        if not invoice.subtotal_5:
            invoice.subtotal_5 = rnd(s5)
        if not invoice.subtotal_10:
            invoice.subtotal_10 = rnd(s10)

        if not invoice.iva_5:
            invoice.iva_5 = rnd(invoice.subtotal_5 * 5 / 105)
        if not invoice.iva_10:
            invoice.iva_10 = rnd(invoice.subtotal_10 * 10 / 110)

        if not getattr(invoice, "gravado_5", None):
            invoice.gravado_5 = rnd(invoice.subtotal_5 - invoice.iva_5)
        if not getattr(invoice, "gravado_10", None):
            invoice.gravado_10 = rnd(invoice.subtotal_10 - invoice.iva_10)

    except Exception as e:
        logger.warning("recalcular_totales_desde_productos: %s", e)


# ---------------------------
# Agrupado y paths
# ---------------------------

def month_key_for_invoice(invoice: InvoiceData) -> str:
    if getattr(invoice, "mes_proceso", None):
        return invoice.mes_proceso
    if getattr(invoice, "fecha", None):
        return invoice.fecha.strftime("%Y-%m")
    return datetime.now().strftime("%Y-%m")


def group_invoices_by_month(invoices: Iterable[InvoiceData]) -> Dict[str, List[InvoiceData]]:
    grouped: Dict[str, List[InvoiceData]] = {}
    for inv in invoices:
        if not inv or not isinstance(inv, InvoiceData):
            continue
        recalcular_totales_desde_productos(inv)
        k = month_key_for_invoice(inv)
        grouped.setdefault(k, []).append(inv)
    return grouped


def monthly_excel_path(year_month: str, base_dir: Optional[str] = None) -> str:
    outdir = base_dir or settings.EXCEL_OUTPUT_DIR or "/app/data/excels"
    os.makedirs(outdir, exist_ok=True)
    return os.path.join(outdir, f"facturas_ascont_{year_month}.xlsx")


# ---------------------------
# Archivos disponibles
# ---------------------------

def format_display_name(year_month: str) -> str:
    try:
        year, month = year_month.split("-")
        nombres = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                   "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
        return f"{nombres[int(month)-1]} {year}"
    except Exception:
        return year_month


def list_excel_files(output_dir: str) -> List[ExcelFileInfo]:
    files: List[ExcelFileInfo] = []
    if not os.path.exists(output_dir):
        return files

    for fn in os.listdir(output_dir):
        if not (fn.endswith(".xlsx") and fn.startswith("facturas_ascont_")):
            continue
        path = os.path.join(output_dir, fn)
        year_month = fn.replace("facturas_ascont_", "").replace(".xlsx", "")
        try:
            stats = os.stat(path)
            try:
                df = pd.read_excel(path, sheet_name="Facturas ASCONT")
                count = len(df)
            except Exception:
                count = 0
            files.append(ExcelFileInfo(
                filename=fn,
                year_month=year_month,
                display_name=format_display_name(year_month),
                path=path,
                size=stats.st_size,
                last_modified=datetime.fromtimestamp(stats.st_mtime),
                invoice_count=count
            ))
        except Exception as e:
            logger.debug("list_excel_files: %s", e)

    files.sort(key=lambda x: x.year_month, reverse=True)
    return files