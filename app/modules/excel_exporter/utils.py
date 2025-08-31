from __future__ import annotations

import os
import re
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from app.models.models import InvoiceData, ExcelFileInfo
from app.config.settings import settings
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

# ---------------------------
# Parsing y normalización
# ---------------------------

def parse_monto(valor: Any, enteros: bool = True) -> float:
    """Convierte a número. Si enteros=True redondea (para PYG)."""
    try:
        n = float(valor or 0)
        return q0(n) if enteros else float(n)
    except Exception:
        return 0 if enteros else 0.0
    except Exception:
        logger.debug("parse_monto: valor inválido %r", valor)
        return 0 if enteros else 0.0

def q0(x) -> int:
    """Redondea a 0 decimales con HALF_UP (1.5 -> 2). Acepta None/str/float."""
    if x is None:
        x = 0
    return int(Decimal(str(x)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def round_bucket(base, iva) -> tuple[int, int]:
    """
    Redondeo pareado para un bucket:
    - total_int = round(base + iva)
    - base_int  = round(base)
    - iva_int   = total_int - base_int  (garantiza que base+iva = total_exacto)
    """
    base_dec = Decimal(str(base or 0))
    iva_dec  = Decimal(str(iva or 0))
    total_int = q0(base_dec + iva_dec)
    base_int  = q0(base_dec)
    iva_int   = total_int - base_int
    return base_int, iva_int

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
    # Solo aceptamos CDC válidos de 44 dígitos numéricos (SIFEN)
    if not (len(limpio) == 44 and limpio.isdigit()):
        return cdc
    # separa cada 4 dígitos
    return " ".join(limpio[i:i+4] for i in range(0, len(limpio), 4))


def formatear_email_origen(email_origen: Optional[str]) -> str:
    """
    Normaliza "email_origen" a 'Nombre <email@dominio>'.
    No altera si ya viene con ángulos.
    """
    if not email_origen:
        return ""
    s = email_origen.strip().strip('"').strip("'")
    if "<" in s and ">" in s:
        return s
    if "@" in s:
        nombre = s.split("@")[0].replace(".", " ").replace("_", " ").strip()
        nombre = " ".join(nombre.split())  # colapsar espacios
        return f"{nombre.title()} <{s}>"
    return s


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


def reconciliar_contra_total(invoice: InvoiceData):
    """
    Empata el total calculado con el declarado si la diferencia es pequeña.
    Ajusta SOLO bases (exento/g5/g10), nunca el IVA.
    Tolerancia: settings.POSTPROCESS_RECONCILE_TOLERANCE (por defecto ±2..±5).
    """
    try:
        exento = int(round(float(invoice.subtotal_exentas or 0)))
        g5 = int(round(float(invoice.subtotal_5 or 0)))
        i5 = int(round(float(invoice.iva_5 or 0)))
        g10 = int(round(float(invoice.subtotal_10 or 0)))
        i10 = int(round(float(invoice.iva_10 or 0)))

        total_calc = exento + g5 + i5 + g10 + i10
        monto_total = int(round(float(invoice.monto_total or 0)))

        if monto_total and total_calc != monto_total:
            diff = monto_total - total_calc
            tol = int(getattr(settings, "POSTPROCESS_RECONCILE_TOLERANCE", 2))

            # Solo corregimos residuo pequeño (evita “deformar” montos)
            if abs(diff) <= tol:
                buckets = [('g10', g10), ('g5', g5), ('exento', exento)]
                buckets.sort(key=lambda x: x[1], reverse=True)

                for name, val in buckets:
                    if val > 0:
                        if name == 'g10':
                            g10 += diff
                        elif name == 'g5':
                            g5 += diff
                        else:
                            exento += diff
                        break

                invoice.subtotal_exentas = exento
                invoice.subtotal_5 = g5
                invoice.subtotal_10 = g10
                invoice.gravado_5 = g5
                invoice.gravado_10 = g10
            else:
                logger.warning(
                    f"Residuo {diff} fuera de tolerancia (>|{tol}|). No se ajusta automáticamente. "
                    f"CDC={getattr(invoice,'cdc','')}, factura={getattr(invoice,'numero_factura','')}"
                )
    except Exception as e:
        logger.warning(f"⚠️ Error en reconciliación de totales: {e}")


def recalcular_totales_desde_productos(invoice: InvoiceData) -> None:
    """
    Recalcula (solo si faltan) los campos de IVA/gravados a partir de los productos.
    Respeta la definición: subtotal_5/subtotal_10 = GRAVADOS (base imponible, sin IVA).
    No pisa valores ya presentes provenientes del XML/IA.

    - Si ya hay `subtotal_5`/`subtotal_10` y `iva_5`/`iva_10`: setea `gravado_5/10 = subtotal_5/10`.
    - Si faltan IVA pero hay gravados: IVA = gravado * tasa.
    - Si faltan gravados pero hay IVA: gravado = IVA * 20 (5%) o * 10 (10%).
    - Si todo falta y hay productos: intenta inferir gravados desde productos con IVA por ítem.
    """
    try:
        # Normalizar moneda (no influye en cálculos de PYG)
        moneda = (getattr(invoice, 'moneda', 'GS') or 'GS').upper()

        # 1) Si vienen de origen, mapear directo: gravado = subtotal (base)
        if invoice.subtotal_5 not in (None, 0) or invoice.subtotal_10 not in (None, 0):
            # IVA faltante => calcularlo de la base
            if (invoice.iva_5 in (None, 0)) and (invoice.subtotal_5 not in (None, 0)):
                invoice.iva_5 = invoice.subtotal_5 * 0.05
            if (invoice.iva_10 in (None, 0)) and (invoice.subtotal_10 not in (None, 0)):
                invoice.iva_10 = invoice.subtotal_10 * 0.10

            # Gravados para Excel (son la base)
            invoice.gravado_5 = invoice.subtotal_5 or 0
            invoice.gravado_10 = invoice.subtotal_10 or 0
            return

        # 2) Si NO hay gravado pero SÍ hay IVA => obtener base desde IVA
        have_iva = (invoice.iva_5 not in (None, 0)) or (invoice.iva_10 not in (None, 0))
        if not ((invoice.subtotal_5 or 0) or (invoice.subtotal_10 or 0)) and have_iva:
            if invoice.iva_5 not in (None, 0):
                invoice.subtotal_5 = invoice.iva_5 * 20
            if invoice.iva_10 not in (None, 0):
                invoice.subtotal_10 = invoice.iva_10 * 10
            invoice.gravado_5 = invoice.subtotal_5 or 0
            invoice.gravado_10 = invoice.subtotal_10 or 0
            return

        # 3) Si no hay nada y hay productos, intentar inferir:
        if getattr(invoice, 'productos', None):
            base_5 = 0.0
            base_10 = 0.0
            exento = 0.0

            for p in invoice.productos:
                iva_item = int(getattr(p, 'iva', 0) or 0)
                cant = float(getattr(p, 'cantidad', 0) or 0)
                pu = float(getattr(p, 'precio_unitario', 0) or 0)
                total_line = float(getattr(p, 'total', 0) or 0)

                # Preferimos base desde precio_unitario*cantidad si está presente
                base_line = cant * pu if (cant and pu) else None

                if iva_item == 5:
                    if base_line is not None and base_line > 0:
                        base_5 += base_line
                    elif total_line:
                        # Si solo tenemos total_line (podría incluir IVA), aproximar base
                        base_5 += total_line / 1.05
                elif iva_item == 10:
                    if base_line is not None and base_line > 0:
                        base_10 += base_line
                    elif total_line:
                        base_10 += total_line / 1.10
                else:
                    # Exento
                    exento += total_line if total_line else (base_line or 0)

            invoice.subtotal_5 = base_5
            invoice.subtotal_10 = base_10
            invoice.subtotal_exentas = exento
            invoice.iva_5 = invoice.iva_5 or (base_5 * 0.05)
            invoice.iva_10 = invoice.iva_10 or (base_10 * 0.10)
            invoice.gravado_5 = base_5
            invoice.gravado_10 = base_10

    except Exception as e:
        logger.error(f"❌ Error al recalcular totales desde productos: {e}")


def postprocess_invoice(inv):
    """
    Aplica postprocesado solo si hace falta:
    - Recalcular: solo si faltan campos.
    - Reconciliar: solo si el total difiere dentro de la tolerancia.
    """
    # 1) Recalcular SOLO si faltan datos.
    if getattr(settings, "POSTPROCESS_ENABLE_RECALC", True):
        need_recalc = (
            (inv.subtotal_5 in (None, 0) and (inv.iva_5 not in (None, 0))) or
            (inv.subtotal_10 in (None, 0) and (inv.iva_10 not in (None, 0))) or
            # o si no hay nada y hay productos con IVA por ítem
            ((not (inv.subtotal_5 or inv.subtotal_10 or inv.subtotal_exentas)) and bool(getattr(inv, "productos", None)))
        )
        if need_recalc:
            recalcular_totales_desde_productos(inv)

    # 2) Reconciliar SOLO si hay diferencia pequeña
    if getattr(settings, "POSTPROCESS_ENABLE_RECONCILE", True):
        try:
            ex = float(inv.subtotal_exentas or 0)
            g5 = float(inv.subtotal_5 or 0)
            i5 = float(inv.iva_5 or 0)
            g10 = float(inv.subtotal_10 or 0)
            i10 = float(inv.iva_10 or 0)

            total_calc = ex + g5 + i5 + g10 + i10
            monto_total = float(inv.monto_total or 0)
            diff = round(monto_total - total_calc)

            tol = int(getattr(settings, "POSTPROCESS_RECONCILE_TOLERANCE", 2))
            if monto_total and diff != 0 and abs(diff) <= tol:
                reconciliar_contra_total(inv)
        except Exception:
            pass

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
    """
    Agrupa facturas por mes, aplicando post-procesado controlado por settings
    (no fuerza recálculo/ajuste si no es necesario).
    """
    grouped: Dict[str, List[InvoiceData]] = {}
    for inv in invoices:
        if not inv or not isinstance(inv, InvoiceData):
            continue
        # ✅ Solo si hace falta (flags + chequeos internos)
        postprocess_invoice(inv)
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
