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
            # Convertir a formato ASCONT
            ascont_data = []
            productos_data = []
            
            for invoice in invoices:
                # Convertir fecha a string para mejor visualización
                fecha_str = invoice.fecha.strftime("%d/%m/%Y") if invoice.fecha else ""
                
                # Mapear al formato ASCONT exacto
                ascont_record = {
                    "Fecha": fecha_str,
                    "Tipo Documento": self._determinar_tipo_documento(invoice),
                    "Número Documento": invoice.numero_factura or "",
                    "RUC Proveedor": invoice.ruc_emisor or "",
                    "Razón Social Proveedor": invoice.nombre_emisor or "",
                    "Condición Compra": self._normalizar_condicion_compra(invoice.condicion_venta),
                    
                    # Importes en formato ASCONT - convertir a enteros como en el ejemplo
                    "Gravado 10%": int(getattr(invoice, 'subtotal_10', 0) or 0),
                    "IVA 10%": int(self._calcular_iva_10(invoice)),
                    "Gravado 5%": int(getattr(invoice, 'subtotal_5', 0) or 0),
                    "IVA 5%": int(self._calcular_iva_5(invoice)),
                    "Exento": int(getattr(invoice, 'subtotal_exentas', 0) or 0),
                    "Total Factura": int(invoice.monto_total or 0),
                    
                    # Campos adicionales
                    "Timbrado": invoice.timbrado or "",
                    "CDC": self._formatear_cdc(invoice.cdc),
                    "Moneda": invoice.moneda or "PYG",
                    "Email Origen": self._formatear_email_origen(invoice.email_origen),
                    "Procesado En": invoice.procesado_en.strftime("%d/%m/%Y %H:%M:%S") if invoice.procesado_en else ""
                }
                
                # Calcular IVAs si no están presentes
                if ascont_record["IVA 10%"] == 0 and ascont_record["Gravado 10%"] > 0:
                    ascont_record["IVA 10%"] = round(ascont_record["Gravado 10%"] * 0.10, 2)
                
                if ascont_record["IVA 5%"] == 0 and ascont_record["Gravado 5%"] > 0:
                    ascont_record["IVA 5%"] = round(ascont_record["Gravado 5%"] * 0.05, 2)
                
                ascont_data.append(ascont_record)
                
                # Productos para hoja separada
                if invoice.productos:
                    for producto in invoice.productos:
                        if isinstance(producto, dict):
                            articulo = producto.get('articulo', '')
                            cantidad = float(producto.get('cantidad', 0))
                            precio_unitario = float(producto.get('precio_unitario', 0))
                            total = float(producto.get('total', 0))
                        else:
                            articulo = getattr(producto, 'articulo', '')
                            cantidad = float(getattr(producto, 'cantidad', 0))
                            precio_unitario = float(getattr(producto, 'precio_unitario', 0))
                            total = float(getattr(producto, 'total', 0))
                        
                        productos_data.append({
                            "Número Documento": ascont_record["Número Documento"],
                            "RUC Proveedor": ascont_record["RUC Proveedor"],
                            "Fecha": fecha_str,
                            "Artículo": articulo,
                            "Cantidad": cantidad,
                            "Precio Unitario": precio_unitario,
                            "Total": total
                        })
            
            # Cargar datos existentes si el archivo ya existe
            if os.path.exists(excel_path):
                try:
                    existing_df = pd.read_excel(excel_path, sheet_name="Facturas ASCONT")
                    new_df = pd.DataFrame(ascont_data)
                    
                    # Combinar y eliminar duplicados
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                    combined_df.drop_duplicates(
                        subset=["RUC Proveedor", "Número Documento", "Total Factura", "CDC"],
                        keep="last",
                        inplace=True
                    )
                    
                    # Cargar productos existentes
                    try:
                        existing_productos_df = pd.read_excel(excel_path, sheet_name="Productos")
                        productos_df = pd.DataFrame(productos_data)
                        combined_productos_df = pd.concat([existing_productos_df, productos_df], ignore_index=True)
                        combined_productos_df.drop_duplicates(
                            subset=["Número Documento", "RUC Proveedor", "Artículo"],
                            keep="last",
                            inplace=True
                        )
                    except:
                        combined_productos_df = pd.DataFrame(productos_data)
                    
                except Exception as e:
                    logger.warning(f"Error al cargar archivo existente: {str(e)}")
                    combined_df = pd.DataFrame(ascont_data)
                    combined_productos_df = pd.DataFrame(productos_data)
            else:
                combined_df = pd.DataFrame(ascont_data)
                combined_productos_df = pd.DataFrame(productos_data)
            
            # Escribir a Excel con múltiples hojas
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                combined_df.to_excel(writer, sheet_name="Facturas ASCONT", index=False)
                combined_productos_df.to_excel(writer, sheet_name="Productos", index=False)
                
                # Crear hoja de resumen
                self._create_summary_sheet(writer, combined_df, year_month)
            
            # Aplicar formato
            self._apply_ascont_formatting(excel_path)
            
            logger.info(f"Archivo Excel ASCONT generado: {excel_path} con {len(combined_df)} facturas")
            return True
            
        except Exception as e:
            logger.error(f"Error al exportar Excel mensual: {str(e)}", exc_info=True)
            return False
    
    def _create_summary_sheet(self, writer, df: pd.DataFrame, year_month: str):
        """
        Crea una hoja de resumen con totales del mes.
        
        Args:
            writer: ExcelWriter objeto
            df: DataFrame con los datos
            year_month: Mes en formato YYYY-MM
        """
        try:
            # Calcular totales
            total_facturas = len(df)
            total_gravado_10 = df["Gravado 10%"].sum()
            total_iva_10 = df["IVA 10%"].sum()
            total_gravado_5 = df["Gravado 5%"].sum()
            total_iva_5 = df["IVA 5%"].sum()
            total_exento = df["Exento"].sum()
            total_general = df["Total Factura"].sum()
            
            # Crear datos del resumen
            resumen_data = [
                ["RESUMEN MENSUAL", ""],
                ["Período", year_month],
                ["", ""],
                ["Total Facturas", total_facturas],
                ["", ""],
                ["IMPORTES", ""],
                ["Gravado 10%", total_gravado_10],
                ["IVA 10%", total_iva_10],
                ["Gravado 5%", total_gravado_5],
                ["IVA 5%", total_iva_5],
                ["Exento", total_exento],
                ["", ""],
                ["TOTAL GENERAL", total_general]
            ]
            
            resumen_df = pd.DataFrame(resumen_data, columns=["Concepto", "Valor"])
            resumen_df.to_excel(writer, sheet_name="Resumen", index=False)
            
        except Exception as e:
            logger.error(f"Error al crear hoja de resumen: {str(e)}")
    
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
        condicion = getattr(invoice, 'condicion_venta', '').upper()
        
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

# Mantener clase original para compatibilidad hacia atrás
class ExcelExporter(ExcelExporterASCONT):
    """Clase de compatibilidad hacia atrás."""
    pass