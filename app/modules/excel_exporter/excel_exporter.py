import os
import logging
import pandas as pd
from typing import List, Dict, Any, Optional
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

from app.models.models import InvoiceData, ExcelFileInfo
from app.config.settings import settings

logger = logging.getLogger(__name__)

class ExcelExporterASCONT:
    def __init__(self, output_dir: str = None):
        """
        Inicializa el exportador a Excel para formato ASCONT.
        
        Args:
            output_dir: Directorio base donde se guardarán los archivos Excel mensuales.
        """
        self.output_dir = output_dir or settings.EXCEL_OUTPUT_DIR or "/app/data/excels"
        
        # Crear directorio si no existe
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info(f"ExcelExporterASCONT apuntando a: {self.output_dir}")
    
    def get_monthly_excel_path(self, year_month: str) -> str:
        """
        Obtiene la ruta del archivo Excel para un mes específico.
        
        Args:
            year_month: Mes en formato YYYY-MM
            
        Returns:
            str: Ruta completa del archivo Excel
        """
        filename = f"facturas_ascont_{year_month}.xlsx"
        return os.path.join(self.output_dir, filename)
    
    def export_invoices(self, invoices: List[InvoiceData]) -> str:
        """
        Exporta facturas a archivos Excel separados por mes en formato ASCONT.
        
        Args:
            invoices: Lista de objetos InvoiceData para exportar.
            
        Returns:
            str: Ruta del último archivo Excel generado.
        """
        if not invoices:
            logger.warning("No hay facturas para exportar")
            return ""
        
        try:
            # Agrupar facturas por mes
            invoices_by_month = self._group_invoices_by_month(invoices)
            
            last_excel_path = ""
            
            for year_month, month_invoices in invoices_by_month.items():
                logger.info(f"Procesando {len(month_invoices)} facturas para {year_month}")
                
                # Obtener ruta del archivo Excel para este mes
                excel_path = self.get_monthly_excel_path(year_month)
                
                # Exportar facturas de este mes
                success = self._export_monthly_excel(month_invoices, excel_path, year_month)
                
                if success:
                    last_excel_path = excel_path
                    logger.info(f"Archivo Excel generado: {excel_path}")
                else:
                    logger.error(f"Error al generar Excel para {year_month}")
            
            return last_excel_path
            
        except Exception as e:
            logger.error(f"Error al exportar a Excel: {str(e)}", exc_info=True)
            return ""
    
    def _group_invoices_by_month(self, invoices: List[InvoiceData]) -> Dict[str, List[InvoiceData]]:
        """
        Agrupa las facturas por mes.
        
        Args:
            invoices: Lista de facturas
            
        Returns:
            Dict: Diccionario con facturas agrupadas por mes (YYYY-MM)
        """
        grouped = {}
        
        for invoice in invoices:
            
            if not invoice or not isinstance(invoice, InvoiceData):
                continue
            
            self._recalcular_totales_desde_productos(invoice)
            # Obtener mes de la factura
            if hasattr(invoice, 'mes_proceso') and invoice.mes_proceso:
                month_key = invoice.mes_proceso
            elif invoice.fecha:
                month_key = invoice.fecha.strftime("%Y-%m")
            else:
                # Usar mes actual como fallback
                month_key = datetime.now().strftime("%Y-%m")
            
            if month_key not in grouped:
                grouped[month_key] = []
            
            grouped[month_key].append(invoice)
        
        return grouped
    
    def _parse_monto(self, valor):
        try:
            return int(float(valor or 0))
        except Exception as e:
            logger.warning(f"⚠️ Error al convertir monto '{valor}': {e}")
            return 0
    
    def _export_monthly_excel(self, invoices: List[InvoiceData], excel_path: str, year_month: str) -> bool:
        """
        Exporta facturas de un mes específico a Excel en formato ASCONT.

        Args:
            invoices: Lista de facturas del mes
            excel_path: Ruta del archivo Excel
            year_month: Mes en formato YYYY-MM

        Returns:
            bool: True si se exportó correctamente
        """
        try:
            if not invoices:
                logger.warning("⚠️ No hay facturas para exportar. Se omite la generación del Excel.")
                return False

            ascont_data = []
            productos_data = []

            for invoice in invoices:
                fecha_str = invoice.fecha.strftime("%d/%m/%Y") if invoice.fecha else ""
                detalle_articulos = self._generar_detalle_articulos(invoice)
                descripcion_base = getattr(invoice, 'descripcion_factura', '') or ""
                descripcion_final = f"{descripcion_base}\n{detalle_articulos}" if detalle_articulos else descripcion_base

                gra10 = self._parse_monto(invoice.subtotal_10)
                iva10 = self._parse_monto(invoice.iva_10)
                gra5 = self._parse_monto(invoice.subtotal_5)
                iva5 = self._parse_monto(invoice.iva_5)
                exento = self._parse_monto(invoice.subtotal_exentas)

                logger.info(f"🧾 Factura procesada: {invoice.numero_factura}")
                logger.info(f"💵 Subtotal 10%: {gra10}, IVA 10%: {iva10}, Subtotal 5%: {gra5}, IVA 5%: {iva5}, Exento: {exento}")
                logger.info(f"📊 VERIFICANDO TOTALES:")
                logger.info(f"🟩 subtotal_10: {invoice.subtotal_10} (type: {type(invoice.subtotal_10)})")
                logger.info(f"🟨 iva_10: {invoice.iva_10}")
                logger.info(f"🟧 subtotal_5: {invoice.subtotal_5}")
                logger.info(f"🟦 iva_5: {invoice.iva_5}")
                logger.info(f"⬜ exentas: {invoice.subtotal_exentas}")
                logger.debug(f"➡️ Factura: {invoice.numero_factura}")
                logger.debug(f"  ↪ subtotal_10 (original): {invoice.subtotal_10}")
                logger.debug(f"  ↪ iva_10 (original): {invoice.iva_10}")
                logger.debug(f"  ↪ subtotal_5 (original): {invoice.subtotal_5}")
                logger.debug(f"  ↪ iva_5 (original): {invoice.iva_5}")
                logger.debug(f"  ↪ subtotal_exentas (original): {invoice.subtotal_exentas}")

                ascont_record = {
                    "fecha": fecha_str,
                    "factura": invoice.numero_factura or "",
                    "ruc": invoice.ruc_emisor or "",
                    "razon": invoice.nombre_emisor or "",
                    "tipo": self._determinar_tipo_documento_real(invoice),
                    "gra10": self._parse_monto(invoice.subtotal_10),
                    "iva10": self._parse_monto(invoice.iva_10),
                    "gra5": self._parse_monto(invoice.subtotal_5),
                    "iva5": self._parse_monto(invoice.iva_5),
                    "exentos": self._parse_monto(invoice.subtotal_exentas),
                    "num_tim": invoice.timbrado or "",
                    "descripcion": descripcion_final,
                    "moneda": getattr(invoice, 'moneda', 'GS') or 'GS',
                    "tipo_cambio": float(getattr(invoice, 'tipo_cambio', 1.0) or 1.0),
                    "ruc_cliente": invoice.ruc_cliente or "",
                    "razon_cliente": invoice.nombre_cliente or "",
                    "CDC": self._formatear_cdc(invoice.cdc),
                    "email_origen": self._formatear_email_origen(invoice.email_origen),
                    "procesado_en": invoice.procesado_en.strftime("%d/%m/%Y %H:%M:%S") if invoice.procesado_en else "",
                    "monto_total": self._parse_monto(invoice.monto_total) or (gra10 + gra5 + exento + iva10 + iva5)
                }

                ascont_data.append(ascont_record)

                for producto in invoice.productos or []:
                    articulo = producto.get('articulo') if isinstance(producto, dict) else getattr(producto, 'articulo', '')
                    cantidad = float(producto.get('cantidad', 0) if isinstance(producto, dict) else getattr(producto, 'cantidad', 0))
                    precio_unitario = float(producto.get('precio_unitario', 0) if isinstance(producto, dict) else getattr(producto, 'precio_unitario', 0))
                    total = float(producto.get('total', 0) if isinstance(producto, dict) else getattr(producto, 'total', 0))

                    productos_data.append({
                        "factura": ascont_record["factura"],
                        "ruc": ascont_record["ruc"],
                        "fecha": fecha_str,
                        "articulo": articulo,
                        "cantidad": cantidad,
                        "precio_unitario": precio_unitario,
                        "total": total
                    })

            if os.path.exists(excel_path):
                try:
                    existing_df = pd.read_excel(excel_path, sheet_name="Facturas ASCONT")
                    new_df = pd.DataFrame(ascont_data)
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                    combined_df.drop_duplicates(subset=["ruc", "factura", "CDC"], keep="last", inplace=True)
                    try:
                        existing_productos_df = pd.read_excel(excel_path, sheet_name="Productos")
                        new_productos_df = pd.DataFrame(productos_data)
                        combined_productos_df = pd.concat([existing_productos_df, new_productos_df], ignore_index=True)
                        combined_productos_df.drop_duplicates(subset=["factura", "ruc", "articulo"], keep="last", inplace=True)
                    except:
                        combined_productos_df = pd.DataFrame(productos_data)
                except Exception as e:
                    logger.warning(f"Error al cargar archivo existente: {str(e)}")
                    combined_df = pd.DataFrame(ascont_data)
                    combined_productos_df = pd.DataFrame(productos_data)
            else:
                combined_df = pd.DataFrame(ascont_data)
                combined_productos_df = pd.DataFrame(productos_data)

            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                combined_df.to_excel(writer, sheet_name="Facturas ASCONT", index=False)
                combined_productos_df.to_excel(writer, sheet_name="Productos", index=False)
                self._create_summary_sheet(writer, combined_df, year_month)

            self._apply_ascont_formatting(excel_path)
            logger.info(f"✅ Archivo Excel generado con {len(combined_df)} facturas: {excel_path}")
            return True

        except Exception as e:
            logger.error(f"❌ Error al exportar Excel mensual: {str(e)}", exc_info=True)
            return False
    
    def _create_summary_sheet(self, writer, df: pd.DataFrame, year_month: str):
        """
        Crea una hoja de resumen con totales del mes.

        Args:
            writer: Objeto ExcelWriter
            df: DataFrame con los datos
            year_month: Mes en formato YYYY-MM
        """
        try:
            # Mapear columnas internas a nombres amigables
            columns_map = {
                "gra10": "Gravado 10%",
                "iva10": "IVA 10%",
                "gra5": "Gravado 5%",
                "iva5": "IVA 5%",
                "exentos": "Exento",
                "monto_total": "Total Factura"
            }

            # Inicializar totales
            total_facturas = len(df)
            totals = {}

            for key, label in columns_map.items():
                if key in df.columns:
                    totals[label] = df[key].sum()
                else:
                    logger.warning(f"⚠️ Columna faltante para resumen: '{key}', se asume 0")
                    totals[label] = 0.0

            # Crear resumen como lista
            resumen_data = [
                ["RESUMEN MENSUAL", ""],
                ["Período", year_month],
                ["", ""],
                ["Total Facturas", total_facturas],
                ["", ""],
                ["IMPORTES", ""],
                ["Gravado 10%", totals["Gravado 10%"]],
                ["IVA 10%", totals["IVA 10%"]],
                ["Gravado 5%", totals["Gravado 5%"]],
                ["IVA 5%", totals["IVA 5%"]],
                ["Exento", totals["Exento"]],
                ["", ""],
                ["TOTAL GENERAL", totals["Total Factura"]]
            ]

            resumen_df = pd.DataFrame(resumen_data, columns=["Concepto", "Valor"])
            resumen_df.to_excel(writer, sheet_name="Resumen", index=False)

            logger.info(f"✅ Hoja de resumen creada exitosamente para {year_month}")

        except Exception as e:
            logger.error(f"❌ Error al crear hoja de resumen: {str(e)}")
    
    def _apply_ascont_formatting(self, excel_path: str):
        """
        Aplica formato específico para ASCONT.
        
        Args:
            excel_path: Ruta del archivo Excel
        """
        try:
            wb = openpyxl.load_workbook(excel_path)
            
            # Definir estilos
            header_font = Font(bold=True, size=11, color="FFFFFF")
            header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            
            thin_border = Border(
                left=Side(style="thin"),
                right=Side(style="thin"),
                top=Side(style="thin"),
                bottom=Side(style="thin")
            )
            
            # Formatear hoja de Facturas ASCONT
            if "Facturas ASCONT" in wb.sheetnames:
                ws = wb["Facturas ASCONT"]
                
                # Aplicar formato a encabezados
                for cell in ws[1]:
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = header_alignment
                    cell.border = thin_border
                
                # Aplicar formato a datos
                for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                    for cell in row:
                        cell.border = thin_border
                        cell.alignment = Alignment(vertical="center")
                        
                        # Formato para columnas numéricas
                        header_value = ws.cell(1, cell.column).value
                        if header_value and any(word in header_value for word in ["Gravado", "IVA", "Total", "Exento"]):
                            cell.number_format = "#,##0.00"
                
                # Auto-ajustar anchos
                for column in ws.columns:
                    max_length = max(len(str(cell.value)) for cell in column if cell.value)
                    if max_length > 0:
                        column_letter = column[0].column_letter
                        adjusted_width = min(max_length + 2, 50)
                        ws.column_dimensions[column_letter].width = adjusted_width
                
                ws.freeze_panes = "A2"
            
            # Formatear hoja de Resumen
            if "Resumen" in wb.sheetnames:
                ws = wb["Resumen"]
                
                # Formato especial para la hoja de resumen
                for row in ws.iter_rows():
                    for cell in row:
                        if cell.row == 1 or "RESUMEN" in str(cell.value) or "TOTAL GENERAL" in str(cell.value):
                            cell.font = Font(bold=True, size=12)
                        elif "IMPORTES" in str(cell.value):
                            cell.font = Font(bold=True, size=11)
                        
                        if isinstance(cell.value, (int, float)) and cell.value != 0:
                            cell.number_format = "#,##0.00"
            
            wb.save(excel_path)
            logger.info("Formato ASCONT aplicado al archivo Excel")
            
        except Exception as e:
            logger.error(f"Error al aplicar formato ASCONT: {str(e)}")
    
    def get_available_excel_files(self) -> List[ExcelFileInfo]:
        """
        Obtiene la lista de archivos Excel disponibles.
        
        Returns:
            List[ExcelFileInfo]: Lista de archivos Excel disponibles
        """
        excel_files = []
        
        try:
            if not os.path.exists(self.output_dir):
                return excel_files
            
            for filename in os.listdir(self.output_dir):
                if filename.endswith('.xlsx') and filename.startswith('facturas_ascont_'):
                    file_path = os.path.join(self.output_dir, filename)
                    
                    # Extraer mes del nombre del archivo
                    year_month = filename.replace('facturas_ascont_', '').replace('.xlsx', '')
                    
                    # Crear nombre para mostrar
                    display_name = self._format_display_name(year_month)
                    
                    # Obtener información del archivo
                    file_stats = os.stat(file_path)
                    
                    # Contar facturas en el archivo
                    invoice_count = 0
                    try:
                        df = pd.read_excel(file_path, sheet_name="Facturas ASCONT")
                        invoice_count = len(df)
                    except:
                        invoice_count = 0
                    
                    excel_files.append(ExcelFileInfo(
                        filename=filename,
                        year_month=year_month,
                        display_name=display_name,
                        path=file_path,
                        size=file_stats.st_size,
                        last_modified=datetime.fromtimestamp(file_stats.st_mtime),
                        invoice_count=invoice_count
                    ))
            
            # Ordenar por mes (más reciente primero)
            excel_files.sort(key=lambda x: x.year_month, reverse=True)
            
        except Exception as e:
            logger.error(f"Error al obtener archivos Excel: {str(e)}")
        
        return excel_files
    
    def _format_display_name(self, year_month: str) -> str:
        """
        Convierte YYYY-MM a formato amigable como 'Enero 2025'.
        
        Args:
            year_month: Mes en formato YYYY-MM
            
        Returns:
            str: Nombre formateado para mostrar
        """
        try:
            year, month = year_month.split('-')
            month_names = [
                'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
            ]
            month_index = int(month) - 1
            return f"{month_names[month_index]} {year}"
        except:
            return year_month
    
    def get_excel_by_month(self, year_month: str) -> Optional[str]:
        """
        Obtiene la ruta del archivo Excel para un mes específico.
        
        Args:
            year_month: Mes en formato YYYY-MM
            
        Returns:
            Optional[str]: Ruta del archivo si existe, None si no existe
        """
        excel_path = self.get_monthly_excel_path(year_month)
        
        if os.path.exists(excel_path):
            return excel_path
        
        return None
    
    def _determinar_tipo_documento(self, invoice) -> str:
        """
        Determina el tipo de documento según el formato ASCONT.
        
        Args:
            invoice: Datos de la factura
            
        Returns:
            str: Tipo de documento ('FC', 'CR', etc.)
        """
        condicion = getattr(invoice, 'condicion_venta', '') or ''
        condicion = condicion.upper() if condicion else ''
        
        if 'CONTADO' in condicion:
            return 'FC'  # Factura Contado
        elif 'CREDITO' in condicion or 'CRÉDITO' in condicion:
            return 'CR'  # Crédito
        else:
            return 'FC'  # Default a Factura Contado
    
    def _normalizar_condicion_compra(self, condicion_venta: str) -> str:
        """
        Normaliza la condición de compra según el formato ASCONT.
        
        Args:
            condicion_venta: Condición original de la factura
            
        Returns:
            str: Condición normalizada ('CONTADO' o 'CREDITO')
        """
        if not condicion_venta:
            return 'CONTADO'
        
        condicion_upper = condicion_venta.upper()
        
        if 'CONTADO' in condicion_upper:
            return 'CONTADO'
        elif 'CREDITO' in condicion_upper or 'CRÉDITO' in condicion_upper:
            return 'CREDITO'
        else:
            return 'CONTADO'  # Default
    
    def _calcular_iva_10(self, invoice) -> int:
        """
        Calcula el IVA del 10% si no está presente en la factura.
        
        Args:
            invoice: Datos de la factura
            
        Returns:
            int: Valor del IVA del 10%
        """
        # Buscar IVA explícito primero
        iva_10_explicito = getattr(invoice, 'iva_10', None)
        if iva_10_explicito is not None and iva_10_explicito > 0:
            return int(iva_10_explicito)
        
        # Si no hay IVA explícito, calcularlo del subtotal_10
        subtotal_10 = getattr(invoice, 'subtotal_10', 0) or 0
        if subtotal_10 > 0:
            return int(round(subtotal_10 * 0.10))
        
        return 0
    
    def _calcular_iva_5(self, invoice) -> int:
        """
        Calcula el IVA del 5% si no está presente en la factura.
        
        Args:
            invoice: Datos de la factura
            
        Returns:
            int: Valor del IVA del 5%
        """
        # Buscar IVA explícito primero
        iva_5_explicito = getattr(invoice, 'iva_5', None)
        if iva_5_explicito is not None and iva_5_explicito > 0:
            return int(iva_5_explicito)
        
        # Si no hay IVA explícito, calcularlo del subtotal_5
        subtotal_5 = getattr(invoice, 'subtotal_5', 0) or 0
        if subtotal_5 > 0:
            return int(round(subtotal_5 * 0.05))
        
        return 0
    
    def _formatear_cdc(self, cdc: str) -> str:
        """
        Formatea el CDC según el formato ASCONT.
        
        Args:
            cdc: Código CDC original
            
        Returns:
            str: CDC formateado
        """
        if not cdc:
            return ""
        
        # Limpiar el CDC de espacios y guiones
        cdc_limpio = cdc.replace(" ", "").replace("-", "")
        
        # Si ya tiene el formato con espacios, mantenerlo
        if " " in cdc:
            return cdc
        
        # Si no, formatear con espacios cada 4 dígitos
        if len(cdc_limpio) >= 44:  # CDC completo
            # Formatear: XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX
            formatted = ""
            for i in range(0, len(cdc_limpio), 4):
                if i > 0:
                    formatted += " "
                formatted += cdc_limpio[i:i+4]
            return formatted
        
        return cdc
    
    def _formatear_email_origen(self, email_origen: str) -> str:
        """
        Formatea el email de origen según el formato ASCONT.
        
        Args:
            email_origen: Email original
            
        Returns:
            str: Email formateado
        """
        if not email_origen:
            return ""
        
        # Si ya contiene formato "Nombre <email>", mantenerlo
        if "<" in email_origen and ">" in email_origen:
            return email_origen
        
        # Si es solo un email, agregarlo con formato básico
        if "@" in email_origen:
            # Extraer nombre del email (parte antes del @)
            nombre_usuario = email_origen.split("@")[0]
            nombre_formateado = nombre_usuario.replace(".", " ").title()
            return f"{nombre_formateado} <{email_origen}>"
        
        return email_origen

    def _recalcular_totales_desde_productos(self, invoice: InvoiceData):
        """
        Recalcula subtotales e IVA a partir de los productos **solo si los campos vienen vacíos o en 0**.
        """
        if not invoice.productos:
            return

        try:
            # Recalcular subtotales siempre (es seguro)
            invoice.subtotal_exentas = sum(
                p.total for p in invoice.productos if int(p.iva or 0) == 0
            )
            invoice.subtotal_5 = sum(
                p.total for p in invoice.productos if int(p.iva or 0) == 5
            )
            invoice.subtotal_10 = sum(
                p.total for p in invoice.productos if int(p.iva or 0) == 10
            )

            # Solo recalcular IVA si vino vacío o en 0
            if invoice.iva_5 in (None, 0):
                invoice.iva_5 = round(invoice.subtotal_5 * 5 / 105)

            if invoice.iva_10 in (None, 0):
                invoice.iva_10 = round(invoice.subtotal_10 * 10 / 110)

        except Exception as e:
            logger.error(f"❌ Error al recalcular totales desde productos: {e}")


    def _determinar_tipo_documento_real(self, invoice) -> str:
        """
        Determina el tipo de documento según el formato real requerido.
        CO para contado, CR para crédito.
        """
        condicion = ""
        if hasattr(invoice, 'condicion_venta') and invoice.condicion_venta:
            condicion = invoice.condicion_venta.upper()
        elif hasattr(invoice, 'condicion_compra') and invoice.condicion_compra:
            condicion = invoice.condicion_compra.upper()
        
        if "CREDITO" in condicion or "CREDIT" in condicion:
            return "CR"
        else:
            return "CO"  # CO para contado (en lugar de FC)
    
    def _generar_detalle_articulos(self, invoice) -> str:
        """
        Genera la cadena de artículos concatenados por comas.
        """
        try:
            # Primero intentar desde el campo detalle_articulos si existe
            if hasattr(invoice, 'detalle_articulos') and invoice.detalle_articulos:
                return invoice.detalle_articulos
            
            # Si no, generar desde productos
            if not hasattr(invoice, 'productos') or not invoice.productos:
                return ""
            
            articulos = []
            for producto in invoice.productos:
                if isinstance(producto, dict):
                    articulo = producto.get('articulo', '')
                    if articulo:
                        articulos.append(str(articulo))
                elif hasattr(producto, 'articulo') and producto.articulo:
                    articulos.append(str(producto.articulo))
            
            return ", ".join(articulos) if articulos else ""
            
        except Exception as e:
            logger.warning(f"Error generando detalle de artículos: {e}")
            return ""
        
    

# Mantener clase original para compatibilidad hacia atrás
class ExcelExporter(ExcelExporterASCONT):
    """Clase de compatibilidad hacia atrás."""
    pass