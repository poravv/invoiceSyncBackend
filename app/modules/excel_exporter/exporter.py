from __future__ import annotations

import os
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

import pandas as pd
from decimal import Decimal, ROUND_HALF_UP
import threading

from app.models.models import InvoiceData, ExcelFileInfo
from app.config.settings import settings
from .utils import (
    group_invoices_by_month,
    monthly_excel_path,
    parse_monto,
    determinar_tipo_documento_real,
    formatear_cdc,
    formatear_email_origen,
    generar_detalle_articulos,
    list_excel_files,
    round_bucket,
    q0,
)
from .formatting import write_summary_sheet, apply_ascont_formatting

logger = logging.getLogger(__name__)
_EXPORT_LOCK = threading.Lock()

class ExcelExporterASCONT:
    """
    Exporta a Excel en dos hojas:
      - "Facturas ASCONT": registros planos por factura
      - "Productos": detalle por ítem

    Mantiene compatibilidad con tu interfaz pública.
    """

    def __init__(self, output_dir: Optional[str] = None) -> None:
        self.output_dir = output_dir or settings.EXCEL_OUTPUT_DIR or "/app/data/excels"
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info("ExcelExporterASCONT apuntando a: %s", self.output_dir)

    # ---------- API pública ----------

    def get_monthly_excel_path(self, year_month: str) -> str:
        return monthly_excel_path(year_month, self.output_dir)

    def export_invoices(self, invoices: List[InvoiceData]) -> str:
        if not invoices:
            logger.warning("No hay facturas para exportar")
            return ""

        try:
            with _EXPORT_LOCK:
                by_month = group_invoices_by_month(invoices)
                last_path = ""
                for ym, invs in by_month.items():
                    logger.info("Procesando %d facturas para %s", len(invs), ym)
                    path = self.get_monthly_excel_path(ym)
                    if self._export_month(invs, path, ym):
                        last_path = path
                        logger.info("Archivo Excel generado: %s", path)
                return last_path
        except Exception as e:
            logger.error("export_invoices error: %s", e, exc_info=True)
            return ""

    def get_available_excel_files(self) -> List[ExcelFileInfo]:
        return list_excel_files(self.output_dir)

    def get_excel_by_month(self, year_month: str) -> Optional[str]:
        path = self.get_monthly_excel_path(year_month)
        return path if os.path.exists(path) else None

    # ---------- Internos ----------

    def _export_month(self, invoices: List[InvoiceData], excel_path: str, year_month: str) -> bool:
        try:
            ascont_rows: List[Dict[str, Any]] = []
            productos_rows: List[Dict[str, Any]] = []

            for inv in invoices:
                fecha_str = inv.fecha.strftime("%d/%m/%Y") if inv.fecha else ""
                # detalle = generar_detalle_articulos(inv)
                # descripcion_base = getattr(inv, "descripcion_factura", "") or ""
                # descripcion = f"{descripcion_base}\n{detalle}" if detalle else descripcion_base

                # Construir lista de artículos, sin duplicados y limpios
                articulos_list = []
                for p in (inv.productos or []):
                    a = (p.get("articulo") if isinstance(p, dict) else getattr(p, "articulo", "")) or ""
                    a = str(a).strip()
                    if not a:
                        continue
                    if a not in articulos_list:
                        articulos_list.append(a)

                descripcion_base = getattr(inv, "descripcion_factura", "") or ""

                # Evitar duplicar: no agregar artículos ya presentes en la base
                if articulos_list:
                    base_lower = descripcion_base.lower()
                    articulos_faltantes = [a for a in articulos_list if a.lower() not in base_lower]
                else:
                    articulos_faltantes = []

                if descripcion_base and articulos_faltantes:
                    descripcion = f"{descripcion_base} - {', '.join(articulos_faltantes)}"
                elif descripcion_base:
                    descripcion = descripcion_base
                else:
                    descripcion = ", ".join(articulos_list)

                # Decide GS (enteros) vs otras (2 decimales)
                is_gs = (str(getattr(inv, "moneda", "GS")).upper() in {"GS", "PYG"})

                # Helpers de redondeo determinista
                def q2(x):
                    d = Decimal(str(x or 0))
                    return float(d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

                # Tomar valores base como float
                g10f = float(getattr(inv, "gravado_10", inv.subtotal_10) or 0)
                g5f  = float(getattr(inv, "gravado_5", inv.subtotal_5) or 0)
                i10f = float(getattr(inv, "iva_10", 0) or 0)
                i5f  = float(getattr(inv, "iva_5", 0) or 0)
                exf  = float(getattr(inv, "subtotal_exentas", 0) or 0)
                totalf = float(getattr(inv, "monto_total", 0) or (g10f+i10f+g5f+i5f+exf))

                if is_gs:
                    # Enteros asegurando base+iva=total por bucket
                    g10i, i10i = round_bucket(g10f, i10f)
                    g5i,  i5i  = round_bucket(g5f, i5f)
                    exi = q0(exf)
                    total_i = q0(totalf)
                    sum_i = g10i + i10i + g5i + i5i + exi
                    diff = total_i - sum_i
                    if diff != 0:
                        # ajustar al mayor bucket
                        buckets = [("g10", g10i), ("g5", g5i), ("ex", exi)]
                        buckets.sort(key=lambda x: x[1], reverse=True)
                        if buckets[0][0] == "g10": g10i += diff
                        elif buckets[0][0] == "g5": g5i += diff
                        else: exi += diff
                    gra10 = g10i; iva10 = i10i; gra5 = g5i; iva5 = i5i; exen = exi
                    monto_total_cell = g10i + i10i + g5i + i5i + exi
                else:
                    # 2 decimales con HALF_UP y cierre de suma
                    g10 = q2(g10f); i10 = q2(i10f); g5 = q2(g5f); i5 = q2(i5f); exn = q2(exf)
                    total_2 = q2(totalf)
                    sum_2 = q2(g10 + i10 + g5 + i5 + exn)
                    diff = round(Decimal(str(total_2)) - Decimal(str(sum_2)), 2)
                    if diff != 0:
                        # ajustar al mayor bucket (por valor absoluto)
                        buckets = [("g10", g10), ("g5", g5), ("ex", exn)]
                        buckets.sort(key=lambda x: abs(x[1]), reverse=True)
                        if buckets[0][0] == "g10": g10 = q2(g10 + float(diff))
                        elif buckets[0][0] == "g5": g5 = q2(g5 + float(diff))
                        else: exn = q2(exn + float(diff))
                    gra10 = g10; iva10 = i10; gra5 = g5; iva5 = i5; exen = exn
                    monto_total_cell = q2(g10 + i10 + g5 + i5 + exn)

                ascont_rows.append({
                    "fecha": fecha_str,
                    "factura": inv.numero_factura or "",
                    "ruc": inv.ruc_emisor or "",
                    "razon": inv.nombre_emisor or "",
                    "tipo": determinar_tipo_documento_real(getattr(inv, "condicion_venta", None),
                                                           getattr(inv, "condicion_compra", None)),
                    "gra10": gra10,
                    "iva10": iva10,
                    "gra5": gra5,
                    "iva5": iva5,
                    "exentos": exen,
                    "num_tim": (getattr(inv, "timbrado", "") 
                        or (getattr(inv, "timbrado_data", None).nro if getattr(inv, "timbrado_data", None) else "")
                        or ""),
                    "descripcion": descripcion,
                    "moneda": (getattr(inv, "moneda", "GS") or "GS"),
                    "tipo_cambio": float(inv.tipo_cambio) if str(getattr(inv, "moneda","")).upper() in {"USD","DOLLAR","DÓLAR"} and getattr(inv, "tipo_cambio", None) else 0,
                    "ruc_cliente": inv.ruc_cliente or "",
                    "razon_cliente": inv.nombre_cliente or "",
                    "CDC": formatear_cdc(
                            getattr(inv, "cdc", "")
                            or (getattr(inv, "factura_data", None).cdc if getattr(inv, "factura_data", None) else "")
                    ),
                    "email_origen": inv.email_origen, #formatear_email_origen(getattr(inv, "email_origen", "")),
                    "procesado_en": inv.procesado_en.strftime("%d/%m/%Y %H:%M:%S") if getattr(inv, "procesado_en", None) else "",
                    "monto_total": monto_total_cell,
                })

                # productos
                for p in (inv.productos or []):
                    if isinstance(p, dict):
                        articulo = p.get("articulo", "")
                        cantidad = float(p.get("cantidad", 0) or 0)
                        precio_u = float(p.get("precio_unitario", 0) or 0)
                        total    = float(p.get("total", 0) or 0)
                    else:
                        articulo = getattr(p, "articulo", "") or ""
                        cantidad = float(getattr(p, "cantidad", 0) or 0)
                        precio_u = float(getattr(p, "precio_unitario", 0) or 0)
                        total    = float(getattr(p, "total", 0) or 0)

                    productos_rows.append({
                        "factura": ascont_rows[-1]["factura"],
                        "ruc": ascont_rows[-1]["ruc"],
                        "fecha": fecha_str,
                        "articulo": articulo,
                        "cantidad": cantidad,
                        "precio_unitario": precio_u,
                        "total": total,
                        "moneda": (getattr(inv, "moneda", "GS") or "GS"),
                    })

            # merge con existente (si lo hay)
            df_new = pd.DataFrame(ascont_rows)
            dfp_new = pd.DataFrame(productos_rows)

            if os.path.exists(excel_path):
                try:
                    df_old = pd.read_excel(excel_path, sheet_name="Facturas ASCONT")
                except Exception:
                    df_old = pd.DataFrame(columns=df_new.columns)

                try:
                    dfp_old = pd.read_excel(excel_path, sheet_name="Productos")
                except Exception:
                    dfp_old = pd.DataFrame(columns=dfp_new.columns)

                df_comb = pd.concat([df_old, df_new], ignore_index=True)

                # Deduplicación: si hay varias filas con mismo (ruc, factura),
                # preferir la última con CDC válido de 44 dígitos; si ninguna válida, la última.
                def _valid_cdc(val) -> bool:
                    try:
                        s = str(val or "").replace(" ", "").replace("-", "")
                        return len(s) == 44 and s.isdigit()
                    except Exception:
                        return False

                if not df_comb.empty and all(c in df_comb.columns for c in ["ruc", "factura"]):
                    df_comb["__valid_cdc"] = df_comb["CDC"].apply(_valid_cdc) if "CDC" in df_comb.columns else False
                    keep_indices = []
                    for (_, _), grp in df_comb.groupby(["ruc", "factura"], dropna=False):
                        gvalid = grp[grp["__valid_cdc"]] if "__valid_cdc" in grp.columns else pd.DataFrame()
                        if not gvalid.empty:
                            keep_indices.append(gvalid.index.max())  # última válida
                        else:
                            keep_indices.append(grp.index.max())      # última del grupo
                    df_comb = df_comb.loc[sorted(set(keep_indices))].copy()
                    if "__valid_cdc" in df_comb.columns:
                        df_comb.drop(columns=["__valid_cdc"], inplace=True, errors="ignore")

                dfp_comb = pd.concat([dfp_old, dfp_new], ignore_index=True)
                dfp_comb.drop_duplicates(subset=["factura", "ruc", "articulo"], keep="last", inplace=True)
            else:
                df_comb = df_new
                dfp_comb = dfp_new

            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                # Escribimos NUMÉRICOS para permitir fórmulas en Excel.
                df_comb.to_excel(writer, sheet_name="Facturas ASCONT", index=False)
                dfp_comb.to_excel(writer, sheet_name="Productos", index=False)
                write_summary_sheet(writer, df_comb, year_month)

            apply_ascont_formatting(excel_path)
            logger.info("✅ Archivo Excel generado con %d facturas: %s", len(df_comb), excel_path)
            return True

        except Exception as e:
            logger.error("export month error: %s", e, exc_info=True)
            return False


# Compatibilidad hacia atrás
class ExcelExporter(ExcelExporterASCONT):
    pass
