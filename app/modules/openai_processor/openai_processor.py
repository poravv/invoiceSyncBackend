import os
import base64
import logging
import tempfile
import json
import re
import io
from typing import Dict, Any, Optional, List, Union
from datetime import datetime
import requests
import openai
import fitz  # PyMuPDF
from PIL import Image

from config.settings import settings
from models.models import InvoiceData, EmpresaData, TimbradoData, FacturaData, ClienteData, TotalesData, ProductoFactura

logger = logging.getLogger(__name__)

class OpenAIProcessor:
    def __init__(self):
        """
        Inicializa el procesador de OpenAI.
        """
        # Configurar la API key de OpenAI
        self.api_key = settings.OPENAI_API_KEY
        # Configurar la API key globalmente para el módulo openai
        openai.api_key = self.api_key
        
        # Verificar que la API key esté configurada
        if not self.api_key:
            logger.warning("No se ha configurado la API key de OpenAI. La extracción de datos no funcionará correctamente.")
    
    def extract_invoice_data(self, pdf_path: str, email_metadata: Dict[str, Any] = None) -> InvoiceData:
        """
        Extrae datos de factura de un PDF utilizando OpenAI.
        
        Args:
            pdf_path: Ruta al archivo PDF.
            email_metadata: Metadatos del correo de donde se extrajo la factura.
            
        Returns:
            InvoiceData: Objeto con los datos extraídos.
        """
        # Crear objeto de datos de factura
        invoice_data = InvoiceData()
        
        # Establecer el origen del email si existe
        if email_metadata and "sender" in email_metadata:
            invoice_data.email_origen = email_metadata.get("sender", "")
        
        # Establecer la ruta del PDF si existe
        invoice_data.pdf_path = pdf_path
        
        try:
            # Extraer contenido del PDF usando directamente OpenAI
            extracted_data = self._process_pdf_with_openai(pdf_path, email_metadata)
            
            if extracted_data:
                # Actualizar el objeto de factura con los datos extraídos
                for key, value in extracted_data.items():
                    if hasattr(invoice_data, key):
                        setattr(invoice_data, key, value)
            
            logger.info(f"Datos extraídos con OpenAI: {invoice_data}")
            return invoice_data
            
        except Exception as e:
            logger.error(f"Error al procesar PDF con OpenAI: {str(e)}")
            return invoice_data
    
    def _process_pdf_with_openai(self, pdf_path: str, email_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Procesa un archivo PDF directamente con la API de OpenAI.
        
        Args:
            pdf_path: Ruta al archivo PDF.
            email_metadata: Metadatos del email.
            
        Returns:
            Dict: Datos extraídos de la factura.
        """
        if not self.api_key:
            logger.error("No se ha configurado la API key de OpenAI")
            return {}
        
        if not os.path.exists(pdf_path):
            logger.error(f"El archivo PDF {pdf_path} no existe")
            return {}
        
        try:
            # Convertir PDF a imagen para mejor procesamiento
            image_data = self._convert_pdf_to_image(pdf_path)

            # Prompt mejorado para el análisis de facturas
            prompt_text = """Analiza cuidadosamente esta factura y extrae TODOS los siguientes campos (es muy importante que devuelvas TODOS los campos, incluso si están vacíos):

            1. fecha: Fecha de emisión (formato YYYY-MM-DD)
            2. ruc_emisor: RUC del emisor (con guiones)
            3. nombre_emisor: Nombre completo de la empresa
            4. numero_factura: Número completo (ej: 001-001-0000001)
            5. monto_total: Importe total (solo números)
            6. iva: Importe total del IVA (solo números)
            7. timbrado: Número de timbrado
            8. cdc: Código de control CDC
            9. ruc_cliente: RUC del cliente (con guiones)
            10. nombre_cliente: Nombre completo del cliente
            11. email_cliente: Email del cliente
            12. condicion_venta: CONTADO o CRÉDITO
            13. moneda: Tipo de moneda (ej: PYG)
            14. subtotal_exentas: Monto exento de IVA (solo números)
            15. subtotal_5: Monto gravado IVA 5% (solo números)
            16. subtotal_10: Monto gravado IVA 10% (solo números)
            17. actividad_economica: Actividad económica del emisor

            Además, quiero que extraigas la información en un formato estructurado:

            empresa: {
                nombre: Nombre de la empresa emisora,
                ruc: RUC de la empresa,
                direccion: Dirección completa,
                telefono: Teléfono de contacto,
                actividad_economica: Actividad económica
            },
            timbrado_data: {
                nro: Número de timbrado,
                fecha_inicio_vigencia: Fecha de inicio vigencia,
                valido_hasta: Fecha fin vigencia
            },
            factura_data: {
                contado_nro: Número de factura,
                fecha: Fecha de emisión,
                caja_nro: Número de caja,
                cdc: Código CDC,
                condicion_venta: CONTADO o CRÉDITO
            },
            productos: [
                {
                    articulo: Descripción del producto/servicio,
                    cantidad: Cantidad (número),
                    precio_unitario: Precio unitario (número),
                    total: Importe total (número)
                }
            ],
            totales: {
                cantidad_articulos: Cantidad total de ítems,
                subtotal: Importe antes de impuestos,
                total_a_pagar: Importe total a pagar,
                iva_0%: Monto exento de IVA,
                iva_5%: Monto gravado al 5%,
                iva_10%: Monto gravado al 10%,
                total_iva: Suma total del IVA
            },
            cliente: {
                nombre: Nombre del cliente,
                ruc: RUC del cliente,
                email: Email del cliente
            }

            REGLAS IMPORTANTES:
            - TODOS los campos son obligatorios. Si no encuentras el valor, devuelve null para texto o 0 para números
            - Para montos, devuelve SOLO NÚMEROS sin símbolos ni separadores de miles
            - Las fechas deben estar en formato YYYY-MM-DD
            - Si el campo está vacío o no lo encuentras, devuelve null o 0 según corresponda
            - Revisa TODO el documento, los campos pueden estar en cualquier lugar
            - NO omitas ningún campo en la respuesta

            FORMATO DE RESPUESTA:
            Debes responder SOLO con un objeto JSON que contenga TODOS los campos listados arriba, sin explicaciones adicionales."""

            # Hacer la petición a GPT-4 Vision
            try:
                response = openai.ChatCompletion.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": prompt_text
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_data}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=1000,
                    temperature=0.3
                )

                # Imprimir la respuesta completa para depuración
                print("\n\n=== RESPUESTA COMPLETA DE OPENAI ===")
                print(json.dumps(response, indent=2))
                print("=== FIN DE RESPUESTA DE OPENAI ===\n\n")

                result = response['choices'][0]['message']['content']
                try:
                    # Extraer el JSON si está dentro de backticks
                    json_match = None
                    if "```json" in result:
                        json_match = re.search(r'```json\n(.*?)\n```', result, re.DOTALL)
                    elif "```" in result:
                        json_match = re.search(r'```\n(.*?)\n```', result, re.DOTALL)
                    
                    if json_match:
                        result = json_match.group(1)
                    
                    # Imprimir el JSON extraído para depuración
                    print("\n=== JSON EXTRAÍDO ===")
                    print(result)
                    print("=== FIN JSON EXTRAÍDO ===\n")
                    
                    # Parsear el JSON y procesar los datos
                    result_json = json.loads(result)
                    
                    # Procesar y validar los datos extraídos
                    processed_data = {}
                    
                    # Procesar fecha
                    fecha_str = result_json.get("fecha")
                    if fecha_str:
                        fecha = self._parse_date(fecha_str)
                        if fecha:
                            processed_data["fecha"] = fecha
                    
                    # Procesar campos de texto
                    text_fields = [
                        "nombre_emisor", "numero_factura",
                        "timbrado", "cdc", "nombre_cliente",
                        "email_cliente", "condicion_venta", "actividad_economica"
                    ]
                    for field in text_fields:
                        value = result_json.get(field)
                        if value and isinstance(value, str):
                            processed_data[field] = value.strip()
                    
                    # Procesar RUCs (mantener con guiones si existen)
                    ruc_fields = ["ruc_emisor", "ruc_cliente"]
                    for field in ruc_fields:
                        value = result_json.get(field)
                        if value and isinstance(value, str):
                            # Agregar guiones al RUC si no los tiene
                            if "-" not in value and len(value) > 1:
                                # Para RUCs de empresas (8 dígitos + DV)
                                if len(value) >= 8:
                                    processed_data[field] = f"{value[:-1]}-{value[-1]}"
                                else:
                                    # Para RUCs de personas (6-7 dígitos + DV)
                                    processed_data[field] = f"{value[:-1]}-{value[-1]}"
                            else:
                                processed_data[field] = value.strip()
                    
                    # Procesar campos numéricos
                    numeric_fields = [
                        "monto_total", "iva", "subtotal_exentas",
                        "subtotal_5", "subtotal_10"
                    ]
                    for field in numeric_fields:
                        value = self._convert_to_number(result_json.get(field))
                        if value is not None:
                            processed_data[field] = value
                        else:
                            processed_data[field] = 0.0
                    
                    # Establecer moneda por defecto
                    processed_data["moneda"] = result_json.get("moneda", "PYG")
                    
                    # Procesar datos estructurados
                    # Empresa
                    if "empresa" in result_json:
                        empresa_data = result_json["empresa"]
                        # Asegurar que el RUC tenga guiones
                        if "ruc" in empresa_data and "-" not in empresa_data["ruc"] and len(empresa_data["ruc"]) > 1:
                            if len(empresa_data["ruc"]) >= 8:  # RUC de empresa
                                empresa_data["ruc"] = f"{empresa_data['ruc'][:-1]}-{empresa_data['ruc'][-1]}"
                            else:  # RUC de persona
                                empresa_data["ruc"] = f"{empresa_data['ruc'][:-1]}-{empresa_data['ruc'][-1]}"
                        processed_data["empresa"] = empresa_data
                    
                    # Cliente
                    if "cliente" in result_json:
                        cliente_data = result_json["cliente"]
                        # Asegurar que el RUC tenga guiones
                        if "ruc" in cliente_data and "-" not in cliente_data["ruc"] and len(cliente_data["ruc"]) > 1:
                            if len(cliente_data["ruc"]) >= 8:  # RUC de empresa
                                cliente_data["ruc"] = f"{cliente_data['ruc'][:-1]}-{cliente_data['ruc'][-1]}"
                            else:  # RUC de persona
                                cliente_data["ruc"] = f"{cliente_data['ruc'][:-1]}-{cliente_data['ruc'][-1]}"
                        processed_data["cliente"] = cliente_data
                    
                    # Otros datos estructurados
                    other_structured_fields = ["timbrado_data", "factura_data", "totales", "productos"]
                    for field in other_structured_fields:
                        if field in result_json:
                            processed_data[field] = result_json[field]
                    
                    # Convertir campos numéricos en totales
                    if "totales" in processed_data:
                        numeric_total_fields = ["subtotal", "total_a_pagar", "iva_0%", "iva_5%", "iva_10%", "total_iva"]
                        for field in numeric_total_fields:
                            field_key = field
                            if field in processed_data["totales"]:
                                value = self._convert_to_number(processed_data["totales"][field])
                                if value is not None:
                                    processed_data["totales"][field] = value
                            
                    # Convertir campos numéricos en productos
                    if "productos" in processed_data and isinstance(processed_data["productos"], list):
                        for producto in processed_data["productos"]:
                            numeric_product_fields = ["cantidad", "precio_unitario", "total"]
                            for field in numeric_product_fields:
                                if field in producto:
                                    value = self._convert_to_number(producto[field])
                                    if value is not None:
                                        producto[field] = value
                    
                    logger.debug(f"Datos extraídos y procesados: {processed_data}")
                    
                    # Registrar datos procesados en formato legible
                    logger.info(f"Datos procesados: {json.dumps(processed_data, indent=2, default=str)}")
                    
                    return processed_data
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Error al parsear JSON de la respuesta de OpenAI: {str(e)}")
                    return {}
                    
            except Exception as e:
                logger.error(f"Error al hacer la petición a OpenAI: {str(e)}")
                return {}
                
        except Exception as e:
            logger.error(f"Error al procesar el PDF: {str(e)}")
            return {}
    
    def _convert_to_number(self, value: Any) -> Optional[float]:
        """
        Convierte un valor a número, eliminando caracteres no numéricos.
        
        Args:
            value: El valor a convertir
            
        Returns:
            float: El valor convertido a número o None si no se puede convertir
        """
        if value is None:
            return None
            
        if isinstance(value, (int, float)):
            return float(value)
            
        try:
            # Eliminar caracteres no numéricos excepto el punto decimal y la coma
            cleaned = str(value).replace(',', '.')
            cleaned = re.sub(r'[^\d.]', '', cleaned)
            if cleaned:
                return float(cleaned)
        except:
            pass
            
        return None

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        Convierte una cadena de fecha en un objeto datetime.
        
        Args:
            date_str: Cadena de fecha en varios formatos posibles
            
        Returns:
            datetime: Objeto datetime o None si no se puede convertir
        """
        if not date_str:
            return None

        # Limpiar la cadena de fecha
        date_str = date_str.strip()
        
        # Lista de formatos de fecha comunes en facturas paraguayas
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%Y/%m/%d",
            "%d/%m/%y",
            "%Y%m%d",
            "%d-%m-%y"
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
                
        return None

    def _convert_pdf_to_image(self, pdf_path: str) -> str:
        """
        Convierte la primera página de un PDF a una imagen codificada en base64.
        
        Args:
            pdf_path: Ruta al archivo PDF
            
        Returns:
            str: Representación base64 de la imagen
        """
        try:
            # Abrir el PDF
            doc = fitz.open(pdf_path)
            
            # Obtener la primera página
            page = doc[0]
            
            # Renderizar página a un pixmap (establecer una resolución decente, 300 DPI)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            
            # Convertir a imagen PIL
            img_data = pix.tobytes("jpeg")
            
            # Codificar a base64
            return base64.b64encode(img_data).decode('utf-8')
            
        except Exception as e:
            logger.error(f"Error al convertir PDF a imagen: {str(e)}")
            
            # Fallback: intentar leer el PDF directamente como bytes
            with open(pdf_path, "rb") as f:
                return base64.b64encode(f.read()).decode('utf-8')
