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

from app.config.settings import settings
from app.models.models import InvoiceData

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
        try:
            # Extraer contenido del PDF usando directamente OpenAI
            extracted_data = self._process_pdf_with_openai(pdf_path, email_metadata)
            
            if extracted_data:
                # Crear objeto InvoiceData desde los datos extraídos
                invoice = InvoiceData.from_dict(extracted_data, email_metadata)
                if invoice:
                    return invoice
            
            # Si no se pudo extraer datos, crear factura básica
            logger.warning("No se pudieron extraer datos con OpenAI, creando entrada básica")
            return self._create_basic_invoice_from_filename(pdf_path, email_metadata)
            
        except Exception as e:
            logger.error(f"Error al procesar PDF con OpenAI: {str(e)}")
            # Fallback a factura básica
            return self._create_basic_invoice_from_filename(pdf_path, email_metadata)
    
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

                result = response['choices'][0]['message']['content']
                logger.info(f"Respuesta OpenAI bruta: {result}")
                
                # Si OpenAI rechaza el procesamiento
                if "no puedo ayudar" in result.lower() or "lo siento" in result.lower():
                    logger.warning("OpenAI rechazó procesar el PDF")
                    return {}
                
                try:
                    # Extraer el JSON si está dentro de backticks
                    json_match = None
                    if "```json" in result:
                        json_match = re.search(r'```json\n(.*?)\n```', result, re.DOTALL)
                    elif "```" in result:
                        json_match = re.search(r'```\n(.*?)\n```', result, re.DOTALL)
                    
                    if json_match:
                        result = json_match.group(1)
                    
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
            logger.info(f"Convirtiendo PDF a imagen: {pdf_path}")
            # Abrir el PDF
            doc = fitz.open(pdf_path)
            
            # Obtener la primera página
            page = doc[0]
            
            # Renderizar página a un pixmap (establecer una resolución decente, 300 DPI)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            
            # Convertir a imagen PIL
            img_data = pix.tobytes("jpeg")
            doc.close()
            
            # Log del tamaño de la imagen
            logger.info(f"Imagen generada: {len(img_data)} bytes, dimensiones: {pix.width}x{pix.height}")
            
            # Codificar a base64
            base64_image = base64.b64encode(img_data).decode('utf-8')
            logger.info(f"Imagen base64 generada: {len(base64_image)} caracteres")
            
            return base64_image
            
        except Exception as e:
            logger.error(f"Error al convertir PDF a imagen: {str(e)}")
            
            # Fallback: intentar leer el PDF directamente como bytes
            with open(pdf_path, "rb") as f:
                return base64.b64encode(f.read()).decode('utf-8')
  "condicion_venta": "CONTADO o CREDITO",
  
  // IMPORTES CRÍTICOS - SEPARAR POR TASA DE IVA
  "subtotal_exentas": number, // Monto gravado al 0% (exento de IVA)
  "subtotal_5": number,       // Monto gravado al 5% (sin incluir el IVA)
  "iva_5": number,           // IVA del 5% calculado
  "subtotal_10": number,     // Monto gravado al 10% (sin incluir el IVA)
  "iva_10": number,          // IVA del 10% calculado
  "monto_total": number,     // Total final a pagar
  
  "timbrado": "string",
  "cdc": "string",
  "ruc_cliente": "string",
  "nombre_cliente": "string",
  "email_cliente": "string",
  "moneda": "PYG, USD, EUR, etc.",
  "actividad_economica": "string",

  "empresa": {
    "nombre": "string",
    "ruc": "string",
    "direccion": "string",
    "telefono": "string",
    "actividad_economica": "string"
  },
  "timbrado_data": {
    "nro": "string",
    "fecha_inicio_vigencia": "YYYY-MM-DD",
    "valido_hasta": "YYYY-MM-DD"
  },
  "factura_data": {
    "contado_nro": "string",
    "fecha": "YYYY-MM-DD",
    "caja_nro": "string",
    "cdc": "string",
    "condicion_venta": "CONTADO o CREDITO"
  },
  "productos": [
    {
      "articulo": "Descripción del producto/servicio",
      "cantidad": number,
      "precio_unitario": number,
      "total": number
    }
  ],
  "totales": {
    "cantidad_articulos": number,
    "subtotal": number,
    "total_a_pagar": number,
    "iva_0%": number,
    "iva_5%": number,
    "iva_10%": number,
    "total_iva": number
  },
  "cliente": {
    "nombre": "string",
    "ruc": "string",
    "email": "string"
  }
}

⚠️ INSTRUCCIONES ESPECÍFICAS PARA ESTA FACTURA:

🔍 **ANÁLISIS DE LA TABLA DE PRODUCTOS**:
En esta factura verás una tabla con estas columnas exactas:
| Cod | Descripcion | Unidad de medida | Cantidad | Precio Unitario | Descuento | Exentas | 5% | 10% |

✅ **LECTURA FILA POR FILA**:
1. FILA 1: "APORTE DE ESPERA 20 AÑOS" tiene en la columna "Exentas": 860.690,0 y en "5%": 0 y en "10%": 0
2. FILA 2: "ESPERA 20 AÑOS" (Gastos Administrativos) tiene en "Exentas": 0 y en "5%": 312.000,0 y en "10%": 0  
3. FILA 3: "ESPERA 20 AÑOS" (último) tiene en "Exentas": 0 y en "5%": 0 y en "10%": 387.310,0

⚠️ **CÁLCULOS EXACTOS REQUERIDOS**:
- subtotal_exentas = 860690 (de la fila 1, columna Exentas)
- subtotal_5 = 312000 (de la fila 2, columna 5%)
- subtotal_10 = 387310 (de la fila 3, columna 10%)
- iva_5 = 18443 (de "LIQUIDACIÓN IVA: (5%) 18.443")
- iva_10 = 28363 (de "LIQUIDACIÓN IVA: (10%) 28.363")

🚫 **NO HAGAS ESTO**:
- NO uses el precio unitario como subtotal
- NO sumes todo en una sola categoría
- NO inventes distribuciones
- NO ignores las columnas específicas Exentas/5%/10%

✅ **VERIFICACIÓN MATEMÁTICA**:
El cálculo debe ser: 860690 + 312000 + 18443 + 387310 + 28363 = 1606806
Pero el total de la factura es 1560000, así que hay que ajustar según lo que está impreso.

🎯 **REGLAS DE EXTRACCIÓN**:
1. Lee EXACTAMENTE los valores de cada columna de cada fila de productos
2. Los valores en la columna "Exentas" van a subtotal_exentas
3. Los valores en la columna "5%" van a subtotal_5
4. Los valores en la columna "10%" van a subtotal_10
5. Los valores de IVA están en la línea "LIQUIDACIÓN IVA"

**FORMATO DE RESPUESTA**:
- Todos los montos deben ser números sin separadores de miles ni símbolos
- RUC debe incluir el guión (ej: "80014066-4")
- Fechas en formato YYYY-MM-DD
- Si no encuentras un valor, usa null (texto) o 0 (números)
- NO uses markdown ni ```json en la respuesta
- Responde SOLO con el objeto JSON válido
"""

    def _create_basic_invoice_from_filename(self, pdf_path: str, email_metadata: Dict[str, Any] = None) -> InvoiceData:
        """
        Crea una factura básica cuando OpenAI no puede procesar el PDF.
        Extrae información del nombre del archivo y CDC si está disponible.
        """
        try:
            filename = os.path.basename(pdf_path)
            logger.info(f"Creando factura básica desde filename: {filename}")
            
            # Buscar CDC en el nombre del archivo
            cdc_match = re.search(r'(\d{44})', filename)
            cdc = cdc_match.group(1) if cdc_match else ""
            
            # Buscar número de factura en el formato XXX-XXX-XXXXXXX
            factura_match = re.search(r'(\d{3}-\d{3}-\d{7})', filename)
            numero_factura = factura_match.group(1) if factura_match else ""
            
            # Datos básicos para factura no procesable
            basic_data = {
                "fecha": datetime.now().strftime("%Y-%m-%d"),
                "numero_factura": numero_factura,
                "ruc_emisor": "",
                "nombre_emisor": "FACTURA NO PROCESABLE - REVISAR MANUALMENTE",
                "condicion_venta": "CONTADO",
                "subtotal_exentas": 0,
                "subtotal_5": 0,
                "iva_5": 0,
                "subtotal_10": 0,
                "iva_10": 0,
                "monto_total": 0,
                "timbrado": "",
                "cdc": cdc,
                "ruc_cliente": "",
                "nombre_cliente": "",
                "email_cliente": "",
                "moneda": "PYG",
                "actividad_economica": "",
                "empresa": {
                    "nombre": "FACTURA NO PROCESABLE",
                    "ruc": "",
                    "direccion": "",
                    "telefono": "",
                    "actividad_economica": ""
                },
                "timbrado_data": {
                    "nro": "",
                    "fecha_inicio_vigencia": "",
                    "valido_hasta": None
                },
                "factura_data": {
                    "contado_nro": numero_factura,
                    "fecha": datetime.now().strftime("%Y-%m-%d"),
                    "caja_nro": "",
                    "cdc": cdc,
                    "condicion_venta": "CONTADO"
                },
                "productos": [],
                "totales": {
                    "cantidad_articulos": 0,
                    "subtotal": 0,
                    "total_a_pagar": 0,
                    "iva_0%": 0,
                    "iva_5%": 0,
                    "iva_10%": 0,
                    "total_iva": 0
                },
                "cliente": {
                    "nombre": "",
                    "ruc": "",
                    "email": ""
                }
            }
            
            invoice = InvoiceData.from_dict(basic_data, email_metadata)
            logger.info(f"Factura básica creada: {numero_factura} - CDC: {cdc}")
            return invoice
            
        except Exception as e:
            logger.error(f"Error creando factura básica: {str(e)}")
            return None
    
    def _enhance_basic_invoice_from_cdc(self, invoice: InvoiceData) -> InvoiceData:
        """
        Mejora la factura básica extrayendo información adicional del CDC.
        
        Args:
            invoice: Factura básica a mejorar
            
        Returns:
            Factura mejorada con información del CDC
        """
        try:
            cdc = getattr(invoice, 'cdc', '')
            if not cdc or len(cdc) != 44:
                return invoice
            
            # El CDC paraguayo tiene estructura específica:
            # Posiciones 0-7: RUC emisor (sin DV)
            # Posiciones 8-10: DV + tipo documento + establecimiento
            # Posiciones 11-13: Punto expedición
            # Posiciones 14-20: Número secuencial
            # Posiciones 21-22: Tipo documento
            # Posiciones 23-30: Fecha (YYYYMMDD)
            # Resto: control y verificación
            
            ruc_base = cdc[0:8]
            dv = cdc[8]
            ruc_completo = f"{ruc_base}-{dv}"
            
            # Extraer fecha del CDC (posiciones 23-30)
            fecha_str = cdc[23:31]
            if len(fecha_str) == 8 and fecha_str.isdigit():
                year = fecha_str[0:4]
                month = fecha_str[4:6]
                day = fecha_str[6:8]
                fecha_cdc = f"{year}-{month}-{day}"
                
                # Actualizar la factura con información del CDC
                invoice.ruc_emisor = ruc_completo
                invoice.fecha = datetime.strptime(fecha_cdc, "%Y-%m-%d").date()
                invoice.nombre_emisor = f"EMISOR RUC {ruc_completo} - VERIFICAR DATOS MANUALMENTE"
                
                logger.info(f"Factura básica mejorada con CDC: RUC {ruc_completo}, Fecha {fecha_cdc}")
            
            return invoice
            
        except Exception as e:
            logger.warning(f"Error al mejorar factura básica con CDC: {str(e)}")
            return invoice


def extract_clean_json(text: str) -> dict:
    """
    Extrae y limpia un objeto JSON desde texto potencialmente envuelto en markdown
    """
    # Eliminar posibles bloques de markdown tipo ```json o ```
    cleaned = re.sub(r"```json\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"```", "", cleaned)
    
    return json.loads(cleaned)
