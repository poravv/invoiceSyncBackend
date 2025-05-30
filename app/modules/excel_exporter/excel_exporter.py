import os
import logging
import pandas as pd
from typing import List, Dict, Any, Optional
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

from models.models import InvoiceData
from config.settings import settings

logger = logging.getLogger(__name__)

class ExcelExporter:
    def __init__(self, output_path: str = None):
        """
        Inicializa el exportador a Excel.
        
        Args:
            output_path: Ruta del archivo Excel de salida. Si no se proporciona,
                       se utiliza el valor de configuración.
        """
        self.output_path = output_path or settings.EXCEL_OUTPUT_PATH
        
        # Crear directorio si no existe
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
    
    def export_invoices(self, invoices: List[InvoiceData]) -> str:
        """
        Exporta facturas a un archivo Excel con todos los campos necesarios.
        
        Args:
            invoices: Lista de objetos InvoiceData para exportar.
            
        Returns:
            str: Ruta del archivo Excel generado.
        """
        if not invoices:
            logger.warning("No hay facturas para exportar")
            return ""
        
        try:
            # Convertir a lista de diccionarios para pandas
            data = []
            for invoice in invoices:
                # Convertir fecha a str para mejor visualización
                fecha_str = invoice.fecha.strftime("%d/%m/%Y") if invoice.fecha else ""
                procesado_str = invoice.procesado_en.strftime("%d/%m/%Y %H:%M:%S") if invoice.procesado_en else ""
                
                data.append({
                    "Fecha": fecha_str,
                    "RUC Emisor": invoice.ruc_emisor or "",
                    "Nombre Emisor": invoice.nombre_emisor or "",
                    "Nro. Factura": invoice.numero_factura or "",
                    "Condición Venta": invoice.condicion_venta or "",
                    "Moneda": invoice.moneda or "PYG",
                    "Monto Total": float(invoice.monto_total) if invoice.monto_total else 0.0,
                    "IVA": float(invoice.iva) if invoice.iva else 0.0,
                    "Subtotal Exentas": float(invoice.subtotal_exentas) if invoice.subtotal_exentas else 0.0,
                    "Subtotal 5%": float(invoice.subtotal_5) if invoice.subtotal_5 else 0.0,
                    "Subtotal 10%": float(invoice.subtotal_10) if invoice.subtotal_10 else 0.0,
                    "RUC Cliente": invoice.ruc_cliente or "",
                    "Nombre Cliente": invoice.nombre_cliente or "",
                    "Email Cliente": invoice.email_cliente or "",
                    "Timbrado": invoice.timbrado or "",
                    "CDC": invoice.cdc or "",
                    "Actividad Económica": invoice.actividad_economica or "",
                    "PDF": invoice.pdf_path or "",
                    "Origen (correo)": invoice.email_origen or "",
                    "Procesado en": procesado_str
                })
            
            # Columnas numéricas para formateo especial
            numeric_cols = ["Monto Total", "IVA", "Subtotal Exentas", "Subtotal 5%", "Subtotal 10%"]
            
            # Crear o cargar el archivo Excel existente
            if os.path.exists(self.output_path):
                try:
                    existing_df = pd.read_excel(self.output_path)
                    new_df = pd.DataFrame(data)
                    
                    # Convertir columnas numéricas a float para evitar problemas
                    for col in numeric_cols:
                        existing_df[col] = existing_df[col].astype(float)
                        new_df[col] = new_df[col].astype(float)
                    
                    # Concatenar y eliminar duplicados
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                    combined_df.drop_duplicates(
                        subset=["RUC Emisor", "Nro. Factura", "Monto Total", "CDC"],
                        keep="last",
                        inplace=True
                    )
                    
                    # Guardar el DataFrame combinado
                    combined_df.to_excel(self.output_path, index=False)
                    
                except Exception as e:
                    logger.error(f"Error al cargar archivo Excel existente: {str(e)}")
                    # Si hay error al cargar, creamos uno nuevo
                    pd.DataFrame(data).to_excel(self.output_path, index=False)
            else:
                # Crear nuevo archivo
                pd.DataFrame(data).to_excel(self.output_path, index=False)
            
            # Aplicar formato
            self._apply_excel_formatting(numeric_cols)
            
            logger.info(f"Archivo Excel generado: {self.output_path} con {len(data)} facturas")
            return self.output_path
            
        except Exception as e:
            logger.error(f"Error al exportar a Excel: {str(e)}", exc_info=True)
            return ""
    
    def _apply_excel_formatting(self, numeric_cols: List[str]):
        """
        Aplica formato al archivo Excel para mejorar su visualización.
        
        Args:
            numeric_cols: Lista de columnas numéricas para formateo especial.
        """
        try:
            wb = openpyxl.load_workbook(self.output_path)
            ws = wb.active
            
            # Definir estilos
            header_font = Font(bold=True, size=12, color="FFFFFF")
            header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
            header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            
            thin_border = Border(
                left=Side(style="thin"),
                right=Side(style="thin"),
                top=Side(style="thin"),
                bottom=Side(style="thin")
            )
            
            # Aplicar formato a encabezados
            for cell in ws[1]:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
                cell.border = thin_border
            
            # Aplicar formato a celdas
            for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                for cell in row:
                    cell.border = thin_border
                    cell.alignment = Alignment(vertical="center")
                    
                    # Formato para columnas numéricas
                    if ws.cell(1, cell.column).value in numeric_cols:
                        cell.number_format = "#,##0.00"
            
            # Auto-ajustar anchos de columna
            for column in ws.columns:
                max_length = max(
                    len(str(cell.value)) for cell in column
                ) if any(cell.value for cell in column) else 0
                
                if max_length > 0:
                    column_letter = column[0].column_letter
                    adjusted_width = min(max_length + 2, 50)
                    ws.column_dimensions[column_letter].width = adjusted_width
            
            # Congelar paneles (fila de encabezados)
            ws.freeze_panes = "A2"
            
            # Guardar cambios
            wb.save(self.output_path)
            logger.info("Formato aplicado al archivo Excel")
            
        except Exception as e:
            logger.error(f"Error al aplicar formato Excel: {str(e)}", exc_info=True)
    
    def append_invoices(self, invoices: List[InvoiceData]) -> bool:
        """
        Añade facturas al archivo Excel existente.
        
        Args:
            invoices: Lista de objetos InvoiceData para añadir.
            
        Returns:
            bool: True si se añadieron correctamente, False en caso contrario.
        """
        return bool(self.export_invoices(invoices))