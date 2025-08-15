import os
import base64
import logging
import json
import re
from typing import Dict, Any, Optional
from datetime import datetime
import openai
import fitz  # PyMuPDF
from PyPDF2 import PdfReader
from pdfminer.high_level import extract_text

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
        Extrae datos de factura desde un archivo PDF usando OpenAI.
        Primero intenta procesamiento como texto, luego como imagen si falla.
        Incluye validación automática contra CDC.
        
        Args:
            pdf_path: Ruta al archivo PDF
            email_metadata: Metadatos del correo de donde se extrajo la factura.
            
        Returns:
            InvoiceData: Objeto con los datos extraídos.
        """
        try:
            # Estrategia 1: Procesar como texto (más preciso y rápido)
            if self._pdf_has_text(pdf_path):
                logger.info("📄 PDF tiene texto extractible")
                logger.info("📄 Procesando PDF como TEXTO")
                pdf_text = extract_text(pdf_path).strip()
                logger.info(f"📄 Texto extraído: {len(pdf_text)} caracteres")
                
                prompt = self._build_prompt() + "\n\nTexto de la factura:\n" + pdf_text
                messages = [{"role": "user", "content": prompt}]
                
                try:
                    # Procesar como texto
                    response = openai.ChatCompletion.create(
                        model="gpt-4o",
                        messages=messages,
                        max_tokens=1000,
                        temperature=0.3
                    )
                    
                    raw_output = response.choices[0].message.content
                    logger.info(f"Respuesta OpenAI (texto): {raw_output}")
                    
                    json_data = self._extract_clean_json(raw_output)
                    if json_data and isinstance(json_data, dict):
                        invoice = InvoiceData.from_dict(json_data, email_metadata)
                        if invoice:
                            logger.info("✅ Procesamiento como texto exitoso")
                            return self._validate_and_enhance_with_cdc(invoice)
                    
                except Exception as text_error:
                    logger.error(f"Error en procesamiento de texto: {text_error}")
                    logger.warning("❌ Procesamiento como texto falló, intentando como imagen")
            
            # Estrategia 2: Procesar como imagen (fallback)
            logger.info("🖼️ Procesando PDF como IMAGEN")
            image_data = self._convert_pdf_to_image(pdf_path)
            prompt = self._build_prompt()
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                ]
            }]

            # Enviar a OpenAI como imagen
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=1000,
                temperature=0.3
            )

            raw_output = response.choices[0].message.content
            logger.info(f"Respuesta OpenAI bruta: {raw_output}")
            
            try:
                json_data = self._extract_clean_json(raw_output)
                if not isinstance(json_data, dict):
                    raise ValueError("La respuesta no es un objeto JSON válido")

                # Crear factura desde datos de OpenAI
                invoice = InvoiceData.from_dict(json_data, email_metadata)
                if invoice is None:
                    raise ValueError("El resultado de from_dict fue None")

                logger.info("✅ Procesamiento como imagen exitoso")
                # ✨ NUEVA FUNCIONALIDAD: Validar y corregir fecha contra CDC
                validated_invoice = self._validate_and_enhance_with_cdc(invoice)
                return validated_invoice

            except Exception as e:
                logger.error(f"Error al procesar la respuesta JSON de OpenAI: {str(e)}")
                logger.error(f"Respuesta que causó el error: '{raw_output}'")
                
                # Si OpenAI rechaza el procesamiento, crear una factura básica
                if "no puedo ayudar" in raw_output.lower() or "lo siento" in raw_output.lower():
                    logger.warning("OpenAI rechazó procesar el PDF, creando entrada básica")
                    basic_invoice = self._create_basic_invoice_from_filename(pdf_path, email_metadata)
                    if basic_invoice:
                        return self._enhance_basic_invoice_from_cdc(basic_invoice)
                    return basic_invoice
                
                return None

        except Exception as e:
            logger.error(f"Error al procesar PDF con OpenAI: {str(e)}")
            # Fallback a factura básica mejorada
            basic_invoice = self._create_basic_invoice_from_filename(pdf_path, email_metadata)
            if basic_invoice:
                return self._enhance_basic_invoice_from_cdc(basic_invoice)
            return basic_invoice

    def _pdf_has_text(self, pdf_path: str) -> bool:
        """
        Determina si un PDF tiene texto extractible.
        
        Args:
            pdf_path: Ruta al archivo PDF
            
        Returns:
            bool: True si el PDF tiene texto extractible
        """
        try:
            reader = PdfReader(pdf_path)
            for page in reader.pages:
                text = page.extract_text()
                if text and text.strip():
                    logger.info(f"📄 PDF tiene texto extractible: {len(text)} caracteres")
                    return True
            logger.info("📄 PDF no tiene texto extractible (imagen escaneada)")
            return False
        except Exception as e:
            logger.warning(f"Error al determinar si el PDF tiene texto (PDF posiblemente corrupto), asumiendo que no tiene texto: {str(e)}")
            return False

    def _process_pdf_as_text(self, pdf_path: str, email_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Procesa un PDF extrayendo su texto y enviándolo a OpenAI.
        
        Args:
            pdf_path: Ruta al archivo PDF
            email_metadata: Metadatos del email (opcional)
            
        Returns:
            Dict con los datos extraídos o None si falla
        """
        try:
            # Extraer texto del PDF
            pdf_text = extract_text(pdf_path).strip()
            if not pdf_text:
                logger.warning("No se pudo extraer texto del PDF")
                return None
                
            logger.info(f"📄 Texto extraído: {len(pdf_text)} caracteres")
            
            # Crear prompt para procesamiento de texto
            prompt = self._build_prompt() + "\n\nTexto de la factura:\n" + pdf_text
            
            # Enviar a OpenAI como texto
            messages = [{"role": "user", "content": prompt}]
            
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=messages,
                max_tokens=2000,
                temperature=0.1
            )
            
            response_content = response.choices[0].message.content.strip()
            logger.info(f"Respuesta OpenAI (texto): {response_content[:200]}...")
            
            return self._parse_openai_response(response_content)
            
        except Exception as e:
            logger.error(f"Error en procesamiento de texto: {str(e)}")
            return None

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
            prompt_text = """Actúa como un asistente de procesamiento de documentos para análisis de facturas comerciales paraguayas. 

Tu tarea es extraer información estructurada de esta factura comercial para el sistema contable ASCONT (Paraguay).

IMPORTANTE: Esta es una factura comercial legítima emitida por una empresa paraguaya registrada y necesita ser procesada para cumplimiento fiscal.

Extrae la siguiente información en formato JSON exacto:

{
  "fecha": "YYYY-MM-DD",
  "numero_factura": "XXX-XXX-XXXXXXX (formato completo)",
  "ruc_emisor": "XXXXXXXX-X (con guión)",
  "nombre_emisor": "Razón social del emisor",
  "condicion_venta": "CONTADO o CREDITO",
  
  "subtotal_exentas": number,
  "subtotal_5": number,
  "iva_5": number,
  "subtotal_10": number,
  "iva_10": number,
  "monto_total": number,
  
  "timbrado": "string",
  "cdc": "string",
  "ruc_cliente": "string",
  "nombre_cliente": "string",
  "email_cliente": "string",
  "moneda": "PYG",
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
    "fecha_inicio_vigencia": "string",
    "valido_hasta": null
  },
  "factura_data": {
    "contado_nro": "string",
    "fecha": "string",
    "caja_nro": "string",
    "cdc": "string",
    "condicion_venta": "string"
  },
  "productos": [
    {
      "articulo": "string",
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
                            # Guardar como string en formato YYYY-MM-DD
                            processed_data["fecha"] = fecha.strftime("%Y-%m-%d")
                    
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
                        "subtotal_5", "subtotal_10", "iva_5", "iva_10"
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
            raise

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
            # Agregar pdf_path como atributo temporal para poder extraer texto real
            setattr(invoice, 'pdf_path', pdf_path)
            logger.info(f"Factura básica creada: {numero_factura} - CDC: {cdc}")
            return invoice
            
        except Exception as e:
            logger.error(f"Error creando factura básica: {str(e)}")
            return None
    
    def _build_prompt(self) -> str:
        """
        Construye el prompt para OpenAI con instrucciones específicas para facturas paraguayas.
        
        Returns:
            str: Prompt completo para OpenAI
        """
        return """
Analiza cuidadosamente esta factura paraguaya y extrae TODOS los siguientes campos en formato JSON estructurado. Esta información será usada para el sistema contable ASCONT, así que es MUY IMPORTANTE que extraigas los importes correctamente según las tasas de IVA:

{
  "fecha": "YYYY-MM-DD",
  "numero_factura": "XXX-XXX-XXXXXXX (formato completo)",
  "ruc_emisor": "XXXXXXXX-X (con guión)",
  "nombre_emisor": "Razón social del emisor",
  "condicion_venta": "CONTADO o CREDITO",
  
  "subtotal_exentas": number,
  "subtotal_5": number,
  "iva_5": number,
  "subtotal_10": number,
  "iva_10": number,
  "monto_total": number,
  
  "timbrado": "string",
  "cdc": "string",
  "ruc_cliente": "string",
  "nombre_cliente": "string",
  "email_cliente": "string",
  "moneda": "PYG",
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
      "articulo": "string",
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

⚠️ INSTRUCCIONES CRÍTICAS:
- TODOS los campos son obligatorios. Si no encuentras el valor, devuelve null para texto o 0 para números
- Los montos deben ser números sin separadores de miles ni símbolos
- RUC debe incluir el guión (ej: "80014066-4")
- Fechas en formato YYYY-MM-DD
- NO uses markdown ni ```json en la respuesta
- Extrae EXACTAMENTE los valores de cada columna de la tabla de productos
- Separa correctamente los importes por tasa de IVA (Exentas, 5%, 10%)
"""

    def _validate_and_enhance_with_cdc(self, invoice: InvoiceData) -> InvoiceData:
        """
        Valida la fecha extraída por OpenAI contra el CDC y corrige si es necesario.
        
        Args:
            invoice: Factura extraída por OpenAI
            
        Returns:
            Factura validada y potencialmente corregida
        """
        try:
            logger.info(f"🔍 VALIDANDO factura contra CDC")
            cdc = getattr(invoice, 'cdc', '')
            
            if not cdc or len(cdc) != 44:
                logger.info(f"🔍 CDC no disponible o inválido, devolviendo factura sin validar")
                return invoice
            
            # Extraer fecha del CDC
            import re
            fecha_pattern = re.search(r'(20\d{2})(\d{2})(\d{2})', cdc)
            if fecha_pattern:
                year_cdc = fecha_pattern.group(1)
                month_cdc = fecha_pattern.group(2)
                day_cdc = fecha_pattern.group(3)
                fecha_cdc_str = f"{year_cdc}-{month_cdc}-{day_cdc}"
                
                # Comparar con la fecha extraída por OpenAI
                if invoice.fecha:
                    fecha_openai_str = invoice.fecha.strftime("%Y-%m-%d")
                    logger.info(f"🔍 Comparando fechas - OpenAI: {fecha_openai_str}, CDC: {fecha_cdc_str}")
                    
                    # Si las fechas difieren, usar la del CDC (más confiable)
                    if fecha_openai_str != fecha_cdc_str:
                        logger.warning(f"🔍 ¡DISCREPANCIA DETECTADA! OpenAI extrajo {fecha_openai_str}, pero CDC indica {fecha_cdc_str}")
                        logger.info(f"🔍 Corrigiendo fecha usando CDC...")
                        
                        # Corregir la fecha usando CDC
                        from datetime import datetime
                        invoice.fecha = datetime.strptime(fecha_cdc_str, "%Y-%m-%d")
                        # Recalcular mes_proceso
                        invoice.mes_proceso = invoice.fecha.strftime("%Y-%m")
                        
                        logger.info(f"🔍 ✅ Fecha corregida: {invoice.fecha}, mes_proceso: {invoice.mes_proceso}")
                    else:
                        logger.info(f"🔍 ✅ Fechas coinciden, no hay corrección necesaria")
                else:
                    logger.info(f"🔍 No hay fecha de OpenAI, usando fecha del CDC")
                    from datetime import datetime
                    invoice.fecha = datetime.strptime(fecha_cdc_str, "%Y-%m-%d")
                    invoice.mes_proceso = invoice.fecha.strftime("%Y-%m")
            else:
                logger.warning(f"🔍 No se pudo extraer fecha del CDC: {cdc}")
            
            return invoice
            
        except Exception as e:
            logger.error(f"Error validando factura contra CDC: {str(e)}")
            return invoice
    
    def _enhance_basic_invoice_from_cdc(self, invoice: InvoiceData) -> InvoiceData:
        """
        Mejora la factura básica extrayendo información adicional del CDC.
        
        Args:
            invoice: Factura básica a mejorar
            
        Returns:
            Factura mejorada con información del CDC
        """
        try:
            logger.info(f"🔧 INICIANDO mejora de factura básica con CDC")
            cdc = getattr(invoice, 'cdc', '')
            logger.info(f"🔧 CDC encontrado: {cdc} (longitud: {len(cdc)})")
            if not cdc or len(cdc) != 44:
                logger.warning(f"🔧 CDC inválido o faltante. CDC: {cdc}, longitud: {len(cdc)}")
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
            
            # Extraer fecha del CDC - buscar patrón YYYYMMDD en el CDC
            # CDC: 07800112458004013000182322025070319401680728
            # Busquemos el patrón 20250703 (fecha correcta)
            fecha_str = None
            fecha_cdc = None
            
            # Buscar patrón de fecha YYYYMMDD en el CDC
            import re
            # Buscar patrón 20XX (año 2020-2099) seguido de MM DD
            fecha_pattern = re.search(r'(20\d{2})(\d{2})(\d{2})', cdc)
            if fecha_pattern:
                year = fecha_pattern.group(1)
                month = fecha_pattern.group(2)
                day = fecha_pattern.group(3)
                fecha_str = f"{year}{month}{day}"
                fecha_cdc = f"{year}-{month}-{day}"
                logger.info(f"🔧 Fecha extraída del CDC: {fecha_str} -> {fecha_cdc}")
            else:
                logger.warning(f"🔧 No se pudo extraer fecha del CDC: {cdc}")
            
            if fecha_cdc:
                
                logger.info(f"🔧 ANTES - RUC: {invoice.ruc_emisor}, Nombre: {invoice.nombre_emisor}")
                
                # Actualizar la factura con información del CDC
                invoice.ruc_emisor = ruc_completo
                invoice.fecha = datetime.strptime(fecha_cdc, "%Y-%m-%d").date()
                
                # Intentar extraer nombre real del PDF en lugar de inventar
                nombre_real = self._extract_real_company_name_from_pdf(invoice.pdf_path if hasattr(invoice, 'pdf_path') else None, ruc_completo)
                invoice.nombre_emisor = nombre_real
                
                # Intentar extraer número de factura del filename si no está presente
                if not invoice.numero_documento or invoice.numero_documento == "":
                    # Buscar patrón XXX-XXX-XXXXXXX en cualquier parte del filename
                    import os
                    filename = os.path.basename(invoice.cdc) if hasattr(invoice, 'cdc') else ""
                    # Buscar en diferentes posibles fuentes
                    search_sources = [filename, cdc]
                    
                    for source in search_sources:
                        if source:
                            factura_match = re.search(r'(\d{3}-\d{3}-\d{7})', source)
                            if factura_match:
                                invoice.numero_documento = factura_match.group(1)
                                logger.info(f"🔧 Número de factura extraído: {invoice.numero_documento}")
                                break
                
                logger.info(f"🔧 DESPUÉS - RUC: {invoice.ruc_emisor}, Nombre: {invoice.nombre_emisor}")
                logger.info(f"✅ Factura básica mejorada con CDC: RUC {ruc_completo}, Fecha {fecha_cdc}")
            else:
                logger.warning(f"🔧 Fecha inválida en CDC: {fecha_str}")
            
            return invoice
            
        except Exception as e:
            logger.error(f"❌ Error al mejorar factura básica con CDC: {str(e)}")
            logger.error(f"❌ Traceback completo:", exc_info=True)
            return invoice

    def _extract_real_company_name_from_pdf(self, pdf_path: str, ruc: str) -> str:
        """
        Extrae el nombre real de la empresa del PDF, buscando patrones como S.A., S.R.L., etc.
        NO inventa nombres - extrae texto real del documento.
        
        Args:
            pdf_path: Ruta al PDF (puede ser None)
            ruc: RUC de la empresa
            
        Returns:
            str: Nombre real extraído o indicación de revisión manual
        """
        try:
            if not pdf_path or not os.path.exists(pdf_path):
                logger.warning(f"PDF no disponible para extraer nombre real")
                return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
            
            # Extraer texto completo del PDF
            try:
                from pdfminer.high_level import extract_text
                text_content = extract_text(pdf_path).strip()
            except Exception as e:
                logger.error(f"Error extrayendo texto del PDF: {e}")
                text_content = None
                
            if not text_content:
                logger.warning(f"No se pudo extraer texto del PDF para buscar nombre")
                return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
            
            logger.info(f"🔍 Texto extraído del PDF: {len(text_content)} caracteres")
            
            # Buscar patrones de nombres empresariales paraguayos reales
            company_patterns = [
                # Patrones con sufijos empresariales
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+S\.A\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+S\.R\.L\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+S\.A\.E\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+LTDA)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+COOPERATIVA)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+SOCIEDAD ANONIMA)',
                
                # Patrones completos con sufijos
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?S\.A\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?S\.R\.L\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?S\.A\.E\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?LTDA)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?COOPERATIVA)',
            ]
            
            # Buscar nombres cerca del RUC
            ruc_clean = ruc.replace('-', '')
            
            for pattern in company_patterns:
                matches = re.finditer(pattern, text_content.upper(), re.IGNORECASE)
                for match in matches:
                    candidate_name = match.group(1).strip()
                    
                    # Filtrar nombres muy cortos o que parezcan otros datos
                    if len(candidate_name) > 5 and not re.match(r'^\d+', candidate_name):
                        # Verificar si está cerca del RUC en el texto
                        start_pos = max(0, match.start() - 200)
                        end_pos = min(len(text_content), match.end() + 200)
                        context = text_content[start_pos:end_pos].upper()
                        
                        if ruc_clean in context or ruc in context:
                            # Limpiar y formatear el nombre encontrado
                            clean_name = re.sub(r'\s+', ' ', candidate_name).strip()
                            logger.info(f"🏢 Nombre real encontrado en PDF: {clean_name}")
                            return clean_name
            
            # Si no encontramos patrones específicos, buscar líneas que contengan el RUC
            lines = text_content.split('\n')
            for line in lines:
                line_upper = line.upper().strip()
                if (ruc_clean in line_upper or ruc in line_upper) and len(line_upper) > 10:
                    # Buscar si la línea contiene sufijos empresariales
                    if any(suffix in line_upper for suffix in ['S.A.', 'S.R.L.', 'LTDA', 'COOPERATIVA', 'S.A.E.']):
                        # Extraer la parte que parece el nombre
                        clean_line = re.sub(r'RUC:?\s*\d+-?\d', '', line_upper).strip()
                        clean_line = re.sub(r'\s+', ' ', clean_line).strip()
                        if len(clean_line) > 5:
                            logger.info(f"🏢 Nombre encontrado en línea con RUC: {clean_line}")
                            return clean_line
            
            logger.warning(f"No se pudo encontrar nombre empresarial real en el PDF")
            return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
            
        except Exception as e:
            logger.error(f"Error extrayendo nombre real del PDF: {e}")
            return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
            
            return invoice
            
        except Exception as e:
            logger.error(f"❌ Error al mejorar factura básica con CDC: {str(e)}")
            logger.error(f"❌ Traceback completo:", exc_info=True)
            return invoice

    def _extract_clean_json(self, text: str) -> dict:
        """
        Extrae y limpia un objeto JSON desde texto potencialmente envuelto en ```json ... ``` o ```
        Normaliza campos problemáticos que pueden venir como listas o formatos incorrectos.
        
        Args:
            text: Texto que contiene el JSON
            
        Returns:
            dict: Objeto JSON parseado y normalizado
        """
        try:
            # Eliminar posibles bloques de markdown tipo ```json o ```
            cleaned = re.sub(r"```json\s*", "", text.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r"```", "", cleaned)
            
            # Parsear JSON
            data = json.loads(cleaned)
            
            # Normalizar campos problemáticos
            data = self._normalize_json_fields(data)
            
            return data
            
        except Exception as e:
            logger.error(f"Error extrayendo y limpiando JSON: {e}")
            raise
    
    def _normalize_json_fields(self, data: dict) -> dict:
        """Normaliza campos problemáticos que pueden venir como listas o formatos incorrectos"""
        try:
            # Campos numéricos que pueden venir mal formateados
            numeric_fields = [
                'subtotal_exentas', 'subtotal_5', 'iva_5', 'subtotal_10', 
                'iva_10', 'monto_total'
            ]
            
            for field in numeric_fields:
                if field in data:
                    value = data[field]
                    # Si es una lista, tomar el primer elemento
                    if isinstance(value, list):
                        logger.warning(f"🔧 Campo {field} vino como lista: {value}, tomando primer elemento")
                        value = value[0] if value else 0
                    # Convertir a número
                    if isinstance(value, str):
                        # Remover espacios y caracteres no numéricos excepto puntos y comas
                        clean_value = re.sub(r'[^\d.,]', '', str(value))
                        if clean_value:
                            value = float(clean_value.replace(',', '.'))
                        else:
                            value = 0
                    data[field] = float(value) if value is not None else 0.0
            
            # Limpiar CDC si viene con espacios
            if 'cdc' in data and isinstance(data['cdc'], str):
                data['cdc'] = re.sub(r'\s+', '', data['cdc'])
            
            # Normalizar número de factura (puede venir como lista)
            if 'numero_factura' in data and isinstance(data['numero_factura'], list):
                data['numero_factura'] = data['numero_factura'][0] if data['numero_factura'] else ""
            
            # Normalizar otros campos string que pueden venir como lista
            string_fields = ['ruc_emisor', 'nombre_emisor', 'fecha', 'timbrado']
            for field in string_fields:
                if field in data and isinstance(data[field], list):
                    logger.warning(f"🔧 Campo {field} vino como lista: {data[field]}, tomando primer elemento")
                    data[field] = data[field][0] if data[field] else ""
                    
            return data
            
        except Exception as e:
            logger.error(f"Error normalizando campos JSON: {e}")
            return data
