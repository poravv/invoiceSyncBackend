from __future__ import annotations

import os
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

import pandas as pd

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
)
from .formatting import write_summary_sheet, apply_ascont_formatting

logger = logging.getLogger(__name__)

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
                detalle = generar_detalle_articulos(inv)
                descripcion_base = getattr(inv, "descripcion_factura", "") or ""
                descripcion = f"{descripcion_base}\n{detalle}" if detalle else descripcion_base

                # Decide enteros vs decimales por moneda
                use_ints = (str(getattr(inv, "moneda", "PYG")).upper() != "USD")

                # Si ya traes gravados calculados desde el procesador/normalizador:
                gra10 = parse_monto(getattr(inv, "gravado_10", inv.subtotal_10), enteros=use_ints)
                gra5  = parse_monto(getattr(inv, "gravado_5", inv.subtotal_5), enteros=use_ints)
                iva10 = parse_monto(inv.iva_10, enteros=use_ints)
                iva5  = parse_monto(inv.iva_5, enteros=use_ints)
                exen  = parse_monto(inv.subtotal_exentas, enteros=use_ints)

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
                    "monto_total": parse_monto(getattr(inv, "monto_total", 0) or (gra10 + gra5 + exen + iva10 + iva5),
                                               enteros=use_ints),
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
                
                # Evits que se dupliquen las facturas (mismo RUC+factura+CDC)
                df_comb.drop_duplicates(subset=["ruc", "factura", "CDC"], keep="last", inplace=True)

                dfp_comb = pd.concat([dfp_old, dfp_new], ignore_index=True)
                dfp_comb.drop_duplicates(subset=["factura", "ruc", "articulo"], keep="last", inplace=True)
            else:
                df_comb = df_new
                dfp_comb = dfp_new

            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
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