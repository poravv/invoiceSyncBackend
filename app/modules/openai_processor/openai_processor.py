"""
OpenAI Invoice Processor Module

Este módulo maneja la extracción de datos de facturas paraguayas usando OpenAI API.
Implementa un sistema de doble flujo: procesamiento de texto y procesamiento de imagen
con validación automática contra CDC y manejo de errores robusto.

Arquitectura:
1. Procesamiento principal (extract_invoice_data)
2. Estrategias de extracción (texto vs imagen)
3. Validación y corrección de datos (CDC)
4. Fallback a facturas básicas (cuando OpenAI falla)
5. Utilidades de parsing y conversión

Autor: Sistema InvoiceSync
Fecha: Agosto 2025
"""

import os
import re
import json
import base64
import logging
import traceback
from typing import Dict, Any, Optional
from datetime import datetime
import pytesseract
from PIL import Image
import pdfplumber

import openai
import fitz  # PyMuPDF
from PyPDF2 import PdfReader
import io

from app.config.settings import settings
from app.models.models import InvoiceData
from pdfminer.high_level import extract_text
from pdfminer.high_level import extract_text as extract_text_pdfminer


logger = logging.getLogger(__name__)


class OpenAIProcessor:
    """
    Procesador de facturas usando OpenAI API.
    
    Funcionalidades principales:
    - Extracción dual (texto + imagen)
    - Validación contra CDC
    - Normalización de datos
    - Fallback inteligente
    """
    
    def __init__(self):
        """
        Inicializa el procesador de OpenAI con configuración de API key.
        """
        self.api_key = settings.OPENAI_API_KEY
        openai.api_key = self.api_key
        
        if not self.api_key:
            logger.warning("⚠️ API key de OpenAI no configurada. Funcionalidad limitada.")
    
    # =========================================================================
    # MÉTODOS PRINCIPALES DE PROCESAMIENTO
    # =========================================================================
    
    def extract_invoice_data(self, pdf_path: str, email_metadata: Dict[str, Any] = None) -> InvoiceData:
        """
        Método principal para extraer datos de facturas.
        
        Estrategia de procesamiento:
        1. Intento por texto (más rápido y preciso)
        2. Fallback por imagen (más robusto)
        3. Fallback a factura básica (último recurso)
        
        Args:
            pdf_path: Ruta al archivo PDF
            email_metadata: Metadatos del correo origen
            
        Returns:
            InvoiceData: Objeto con datos extraídos y validados
        """
        try:
            # ESTRATEGIA 1: Procesamiento como texto
            if self._pdf_has_extractable_text(pdf_path):
                logger.info("📄 PDF tiene texto extractible")
                result = self._process_as_text(pdf_path, email_metadata)
                if result:
                    return result
                    
                logger.warning("❌ Procesamiento como texto falló, intentando imagen")
            
            # ESTRATEGIA 2: Procesamiento como imagen
            logger.info("🖼️ Procesando PDF como imagen")
            result = self._process_as_image(pdf_path, email_metadata)
            if result:
                return result
                
            logger.warning("❌ Ambas estrategias OpenAI fallaron")

            if result is None:
                logger.warning("⛔ Result is None.")
                return None
            
        except Exception as e:
            logger.error(f"❌ Error en procesamiento OpenAI: {str(e)}")
            return None

    def _process_as_text(self, pdf_path: str, email_metadata: Dict[str, Any] = None) -> Optional[InvoiceData]:
        """
        Procesa PDF extrayendo texto (directo o con OCR) y enviándolo a OpenAI.
        
        Args:
            pdf_path: Ruta al archivo PDF
            email_metadata: Metadatos del email
            
        Returns:
            InvoiceData o None
        """
        try:
            logger.info("📄 Iniciando procesamiento del PDF como TEXTO")

            # === PASO 1: Extraer texto del PDF ===
            pdf_text = ""

            try:
                logger.info("📄 Intentando con pdfplumber...")
                with pdfplumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text() or ""
                        pdf_text += text + "\n"

                        table = page.extract_table({
                            "vertical_strategy": "lines",
                            "horizontal_strategy": "lines",
                            "intersection_tolerance": 5
                        })
                        if table:
                            pdf_text += "\n[TABLA DETECTADA]\n"
                            for row in table[1:]:  # omitimos el encabezado
                                cleaned_row = [self.force_decimal_format(cell or "") for cell in row]
                                pdf_text += " | ".join(cleaned_row) + "\n"

                pdf_text = pdf_text.strip()
                if pdf_text:
                    logger.info(f"✅ pdfplumber extrajo {len(pdf_text)} caracteres")
                    logger.info(f"✅ pdfplumber: {pdf_text[:3000]}")
            except Exception as e:
                logger.warning(f"⚠️ Falló pdfplumber: {e}")

            # === INTENTO 2: pdfminer (solo si pdf_text está vacío) ===
            if not pdf_text:
                try:
                    logger.info("📄 Intentando con pdfminer...")
                    pdf_text = extract_text_pdfminer(pdf_path).strip()
                    if pdf_text:
                        logger.info(f"✅ pdfminer extrajo {len(pdf_text)} caracteres")
                except Exception as e:
                    logger.warning(f"⚠️ Falló pdfminer: {e}")

            # === INTENTO 3: PyMuPDF (solo si pdf_text sigue vacío) ===
            if not pdf_text:
                try:
                    logger.info("📄 Intentando con PyMuPDF...")
                    with fitz.open(pdf_path) as doc:
                        pdf_text = "".join([page.get_text() for page in doc]).strip()
                    if pdf_text:
                        logger.info(f"✅ PyMuPDF extrajo {len(pdf_text)} caracteres")
                except Exception as e:
                    logger.warning(f"⚠️ Falló PyMuPDF: {e}")

            # === INTENTO 4: OCR (solo si pdf_text sigue vacío) ===
            if not pdf_text:
                try:
                    logger.info("📄 Intentando OCR con PyMuPDF + Tesseract...")
                    with fitz.open(pdf_path) as doc:
                        page = doc[0]
                        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                        img_bytes = pix.tobytes("png")
                        img = Image.open(io.BytesIO(img_bytes))
                        pdf_text = pytesseract.image_to_string(img, lang="spa").strip()
                    logger.info(f"✅ OCR extrajo {len(pdf_text)} caracteres")
                except Exception as e:
                    logger.error(f"❌ OCR falló: {e}")

            if not pdf_text:
                logger.warning("📄 No se pudo extraer texto del PDF con ningún método")
                return None
            # === FILTRO PARA DESCARTAR NOTAS DE REMISIÓN ===
            remision_keywords = ["remisión"]
            if any(kw in pdf_text.lower() for kw in remision_keywords):
                logger.warning("📄 Documento detectado como Nota de Remisión. Se omite del procesamiento.")
                return None

            logger.info(f"📄 Texto extraído para prompt: {pdf_text[:300]}...")

            # === PASO 2: Construir prompt y consultar OpenAI ===
            prompt = self._build_text_prompt(pdf_text)
            messages = [{"role": "user", "content": prompt}]

            logger.info("🤖 Enviando solicitud a OpenAI...")
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=1000,
                temperature=0.3
            )
            raw_output = response.choices[0].message.content
            logger.info(f"🤖 Respuesta OpenAI recibida: {len(raw_output)} caracteres")
            logger.debug(f"🔎 OpenAI Response Preview: {raw_output}...")

            # === PASO 3: Procesar la respuesta JSON ===
            try:
                result = self._process_openai_response(raw_output, email_metadata, fallback_text=pdf_text)
                if result:
                    logger.info("✅ Resultado procesado exitosamente desde respuesta de OpenAI")
                    return result
                else:
                    logger.warning("⛔ Result is None.")
                    return None
            except Exception as e:
                logger.warning(f"⚠️ Fallo procesando JSON con from_dict: {e}")
                logger.info("✅ Guardando respuesta parcial en observación")
                return None

        except Exception as e:
            logger.error(f"❌ Error general en _process_as_text: {e}")
            logger.error(traceback.format_exc())
            return None

    def force_decimal_format(self, cell: str) -> str:
        if not cell:
            return ""
        # Reemplazar formatos como "1.234,56" → "1234.56"
        cell = cell.replace(".", "") #.replace(",", ".")
        return cell
    
    
    def _process_as_image(self, pdf_path: str, email_metadata: Dict[str, Any] = None) -> Optional[InvoiceData]:
        """
        Procesa PDF convirtiéndolo a imagen y enviándolo a OpenAI Vision.
        
        Flujo independiente optimizado para procesamiento visual.
        
        Args:
            pdf_path: Ruta al PDF
            email_metadata: Metadatos del email
            
        Returns:
            InvoiceData procesada o None si falla
        """
        try:
            logger.info("🖼️ INICIANDO procesamiento como IMAGEN")
            
            # Convertir PDF a imagen
            image_data = self._convert_pdf_to_image(pdf_path)

            logger.info(f"Imagen base64 {image_data[:100]}...")

            ocr_text = self.extract_text_from_base64_image(image_data)
            #Si extrae texto de la imagen entra aqui 
            if ocr_text:

                # === FILTRO PARA DESCARTAR NOTAS DE REMISIÓN ===
                remision_keywords = ["nota de remisión", "remisión electrónica", "nota de entrega", "remisión de mercaderías"]
                if any(kw in ocr_text.lower() for kw in remision_keywords):
                    logger.warning("📄 Documento detectado como Nota de Remisión. Se omite del procesamiento.")
                    return None
                
                logger.info(f"🔍 Texto OCR desde imagen:\n{ocr_text[:500]}...")
                logger.info("🤖 Enviando solicitud a OpenAI...")
                prompt = self._build_text_prompt(ocr_text)
                response = openai.ChatCompletion.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1000,
                    temperature=0.3
                )
                
                raw_output = response.choices[0].message.content
                logger.info(f"🤖 Respuesta OpenAI recibida: {len(raw_output)} caracteres")
                logger.debug(f"🔎 OpenAI Response Preview: {raw_output}...")

                

                # === PASO 3: Procesar la respuesta JSON ===
                try:
                    result = self._process_openai_response(raw_output, email_metadata, fallback_text=ocr_text)
                    if result:
                        logger.info("✅ Resultado procesado exitosamente desde respuesta de OpenAI")
                        return result
                    else:
                        logger.warning("⛔ Result is None.")
                        return None
                except Exception as e:
                    logger.warning(f"⚠️ Fallo procesando JSON con from_dict: {e}")
                    logger.info("✅ Guardando respuesta parcial en observación")
                    return None
            else:
                prompt = self._build_enhanced_image_prompt()
                messages = [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                    ]
                }]

                # Enviar a OpenAI Vision con configuración optimizada
                response = openai.ChatCompletion.create(
                    model="gpt-4o",
                    messages=messages,
                    max_tokens=1500,  # Más tokens para respuestas complejas
                    temperature=0.1   # Más determinístico para mejor precisión
                )

                raw_output = response.choices[0].message.content
                logger.info(f"Respuesta OpenAI (imagen): {raw_output}")
                
                # Procesar respuesta con validaciones específicas para imágenes
                return self._process_image_response(raw_output, email_metadata, pdf_path)

        except Exception as e:
            logger.error(f"Error en procesamiento de imagen: {str(e)}")
            return None
    
    def _process_image_response(self, raw_output: str, email_metadata: Dict[str, Any] = None, pdf_path: str = None) -> Optional[InvoiceData]:
        """
        Procesa respuesta específica de procesamiento de imagen con validaciones mejoradas.
        
        Args:
            raw_output: Respuesta cruda de OpenAI Vision
            email_metadata: Metadatos del email
            pdf_path: Ruta al PDF original (para fallback)
            
        Returns:
            InvoiceData procesada o None si falla
        """
        try:
            logger.info("🖼️ Procesando respuesta de imagen con validaciones específicas")
            
            # Verificar si OpenAI rechazó el procesamiento
            if any(phrase in raw_output.lower() for phrase in ["no puedo ayudar", "lo siento", "no es posible"]):
                logger.warning("🚫 OpenAI rechazó procesar la imagen")
                return None
            
            # Extraer y normalizar JSON
            json_data = self._extract_and_normalize_json(raw_output)
            if not isinstance(json_data, dict):
                raise ValueError("Respuesta no es un objeto JSON válido")
            
            # Validaciones específicas para procesamiento de imagen
            json_data = self._enhance_image_extracted_data(json_data, pdf_path)

            # Crear factura desde datos de OpenAI
            invoice = InvoiceData.from_dict(json_data, email_metadata)
            
            if invoice is None:
                raise ValueError("El resultado de from_dict fue None")

            logger.info("✅ Procesamiento de imagen exitoso")
            
            # Validar y corregir fecha contra CDC
            return self._validate_and_enhance_with_cdc(invoice)

        except Exception as e:
            logger.error(f"Error procesando respuesta de imagen: {str(e)}")
            logger.error(f"Respuesta problemática: '{raw_output}'")
            return None
    
    def _enhance_image_extracted_data(self, data: dict, pdf_path: str = None) -> dict:
        """
        Mejora los datos extraídos de imagen con validaciones y correcciones específicas.
        
        Args:
            data: Datos extraídos por OpenAI Vision
            pdf_path: Ruta al PDF para validaciones adicionales
            
        Returns:
            dict: Datos mejorados y validados
        """
        try:
            logger.info("🖼️ Mejorando datos extraídos de imagen")
            
            # Si no hay montos de IVA pero sí productos, calcular como exentos
            if (data.get("subtotal_5", 0) == 0 and data.get("iva_5", 0) == 0 and 
                data.get("subtotal_10", 0) == 0 and data.get("iva_10", 0) == 0):
                
                # Buscar monto total en productos
                productos = data.get("productos", [])
                total_productos = 0
                
                for producto in productos:
                    if producto.get("total", 0) > 0:
                        total_productos += producto.get("total", 0)
                    elif producto.get("precio_unitario", 0) > 0 and producto.get("cantidad", 0) > 0:
                        total_productos += producto.get("precio_unitario", 0) * producto.get("cantidad", 0)
                
                # Si encontramos totales en productos, usar como exentos
                if total_productos > 0:
                    logger.info(f"🖼️ Detectado total en productos: {total_productos}, clasificando como exento")
                    data["subtotal_exentas"] = total_productos
                    data["monto_total"] = total_productos
                elif data.get("monto_total", 0) > 0:
                    # Si hay monto total pero sin desglose de IVA, asumir exento
                    logger.info(f"🖼️ Monto total encontrado sin IVA: {data.get('monto_total')}, clasificando como exento")
                    data["subtotal_exentas"] = data.get("monto_total", 0)
            
            # Validar consistencia de totales
            calculated_total = data.get("subtotal_exentas", 0) + data.get("subtotal_5", 0) + data.get("iva_5", 0) + data.get("subtotal_10", 0) + data.get("iva_10", 0)
            
            if data.get("monto_total", 0) == 0 and calculated_total > 0:
                logger.info(f"🖼️ Corrigiendo monto_total: {calculated_total}")
                data["monto_total"] = calculated_total
            
            logger.info(f"🖼️ Datos mejorados: exentas={data.get('subtotal_exentas', 0)}, total={data.get('monto_total', 0)}")
            return data
            
        except Exception as e:
            logger.error(f"Error mejorando datos de imagen: {e}")
            return data
    
    def _build_enhanced_image_prompt(self) -> str:
        """
        Construye prompt especializado y mejorado para procesamiento de imagen.
        
        Returns:
            str: Prompt optimizado para OpenAI Vision
        """
        return """
Analiza con extrema atención la imagen de una factura paraguaya. Tu objetivo es extraer datos 100% fieles al contenido visible en la imagen, sin hacer suposiciones ni cálculos automáticos.

Debes devolver el siguiente JSON:

{
  "fecha": "YYYY-MM-DD",
  "numero_factura": "XXX-XXX-XXXXXXX",
  "ruc_emisor": "XXXXXXXX-X",
  "nombre_emisor": "Razón social completa",
  "condicion_venta": "CONTADO",

  "subtotal_exentas": 0,
  "subtotal_5": 0,
  "iva_5": 0,
  "subtotal_10": 0,
  "iva_10": 0,
  "monto_total": 0,

  "timbrado": "número",
  "cdc": "44 dígitos",
  "ruc_cliente": "número",
  "nombre_cliente": "nombre completo",
  "email_cliente": null,
  "moneda": "GS",
  "actividad_economica": "descripción",

  "empresa": {
    "nombre": "nombre empresa",
    "ruc": "ruc empresa", 
    "direccion": "dirección completa",
    "telefono": "teléfono",
    "actividad_economica": "actividad"
  },
  "timbrado_data": {
    "nro": "número timbrado",
    "fecha_inicio_vigencia": "YYYY-MM-DD",
    "valido_hasta": null
  },
  "factura_data": {
    "contado_nro": null,
    "fecha": "YYYY-MM-DD",
    "caja_nro": null,
    "cdc": "44 dígitos",
    "condicion_venta": "CONTADO"
  },
  "productos": [
    {
      "articulo": "descripción completa del producto",
      "cantidad": 1,
      "precio_unitario": 0,
      "total": 0,
      "iva": 0
    }
  ],
  "totales": {
    "cantidad_articulos": 1,
    "subtotal": 0,
    "total_a_pagar": 0,
    "iva_0%": 0,
    "iva_5%": 0,
    "iva_10%": 0,
    "total_iva": 0
  },
  "cliente": {
    "nombre": "nombre cliente",
    "ruc": "ruc cliente",
    "email": null
  }
}

📌 INSTRUCCIONES CRÍTICAS:

1. 📋 LEE CADA FILA DE LA TABLA DE PRODUCTOS O SERVICIOS.
2. 🔍 Si la tabla tiene columna de IVA (Ej: "IVA %", "IVA 5%", "IVA 10%"), entonces:
   - Usa ese valor exacto para determinar el tipo de IVA.
   - Calcula el subtotal por cada tipo (5%, 10%, exento) sumando solo los productos con ese tipo de IVA.
3. ⚠️ Si **NO HAY columna de IVA visible**:
   - Supón que **todos los productos son exentos**.
   - Registra el total en `subtotal_exentas` y pon `iva_5`, `iva_10` en 0.
4. ✅ Incluye **el campo `iva` por producto** como 0, 5 o 10 según se indique explícitamente en la factura.
5. 🚫 **NO infieras el tipo de IVA** basándote en nombres de productos o montos. Solo usá la información visual explícita.
6. 🧮 El campo `monto_total` debe coincidir exactamente con el valor impreso en la factura. Si hay diferencia, incluye observación textual.
7. 🧾 Todos los campos de montos deben estar en guaraníes (`PYG`) sin realizar conversiones.

🎯 Tu prioridad es preservar la estructura, los valores visibles y evitar asumir o interpretar campos. Si no se ve, pon null o 0.
"""

    def _process_openai_response(self, raw_output: str, email_metadata: Dict[str, Any] = None, fallback_text: str = None) -> Optional[InvoiceData]:
        """
        Procesa la respuesta cruda de OpenAI y la convierte en InvoiceData.
        
        Args:
            raw_output: Respuesta cruda de OpenAI
            email_metadata: Metadatos del email
            fallback_text: Texto del PDF para extracción de datos básicos en caso de fallo
            
        Returns:
            InvoiceData procesada o None si falla
        """
        try:
            logger.info("🔄 PASO 3: INICIANDO procesamiento de respuesta OpenAI")
            logger.info(f"🔄 PASO 3a: Respuesta recibida - longitud: {len(raw_output)} caracteres")
            logger.info(f"🔄 PASO 3a: Primeros 300 chars: '{raw_output}...'")
            
            # PASO 3.1: Verificar si OpenAI rechazó el procesamiento
            logger.info("🔄 PASO 3.1: Verificando si OpenAI rechazó el procesamiento")
            rejection_phrases = ["no puedo ayudar", "lo siento", "no es posible"]
            for phrase in rejection_phrases:
                if phrase in raw_output.lower():
                    logger.warning(f"🚫 PASO 3.1 RECHAZO: OpenAI rechazó con frase: '{phrase}'")
                    return None
            
            logger.info("✅ PASO 3.1 ÉXITO: OpenAI no rechazó el procesamiento")
            
            # PASO 3.2: Extraer y normalizar JSON
            logger.info("🔄 PASO 3.2: Extrayendo y normalizando JSON")
            json_data = self._extract_and_normalize_json(raw_output)
            logger.info(f"🔄 PASO 3.2a: JSON extraído, tipo: {type(json_data)}")
            
            if not isinstance(json_data, dict):
                logger.warning(f"⚠️ PASO 3.2 FALLO: JSON inválido - tipo: {type(json_data)}, valor: {json_data}")
                raise ValueError("Respuesta no es un objeto JSON válido")
            
            logger.info(f"✅ PASO 3.2 ÉXITO: JSON válido extraído con {len(json_data)} campos")
            logger.info(f"🔄 PASO 3.2 CAMPOS: {list(json_data.keys())}")
            
            # Loggear algunos campos clave
            key_fields = ["ruc_emisor", "nombre_emisor", "fecha", "monto_total", "cdc"]
            for field in key_fields:
                if field in json_data:
                    logger.info(f"🔄 PASO 3.2 {field}: {json_data[field]}")

            
            
            logger.info("🔄 PASO 3.3: Creando factura desde datos de OpenAI")
            # PASO 3.3: Crear factura desde datos de OpenAI
            #invoice = InvoiceData.from_dict(json_data, email_metadata)
            try:
                invoice = InvoiceData.from_dict(json_data, email_metadata)
            except Exception as e:
                logger.warning(f"❌ from_dict falló: {e}")
                logger.warning("⛔ Result is None. No se generará InvoiceData vacío.")
                return None
                # # Aquí podés asignar manualmente campos clave si están disponibles
                # for campo in ['ruc_emisor', 'nombre_emisor', 'fecha', 'monto_total']:
                #     if campo in json_data:
                #         setattr(invoice, campo, json_data[campo])


            logger.info(f"🔄 PASO 3.3a: from_dict completado, resultado: {type(invoice)}")

            logger.info("✅ PASO 3.3 ÉXITO: Factura creada exitosamente")
            logger.info(f"🔄 PASO 3.3 FACTURA: RUC={getattr(invoice, 'ruc_emisor', 'N/A')}, Nombre={getattr(invoice, 'nombre_emisor', 'N/A')}")
            
            # PASO 3.4: Validar y corregir fecha contra CDC
            logger.info("🔄 PASO 3.4: Validando y mejorando con CDC")
            try:
                enhanced_invoice = self._validate_and_enhance_with_cdc(invoice)
                logger.info("✅ PASO 3.4 ÉXITO: Validación CDC completada")
                logger.info(f"🔄 PASO 3.4 RESULTADO FINAL: RUC={getattr(enhanced_invoice, 'ruc_emisor', 'N/A')}, Nombre={getattr(enhanced_invoice, 'nombre_emisor', 'N/A')}")
                return enhanced_invoice
            except Exception as e:
                logger.warning(f"⚠️ PASO 3.4 ERROR en validación CDC: {e}, devolviendo factura sin validar")
                logger.warning(f"⚠️ PASO 3.4 ERROR tipo: {type(e)}")
                return invoice

        except Exception as e:
            logger.error(f"❌ PASO 3 ERROR GENERAL procesando respuesta OpenAI: {str(e)}")
            logger.error(f"❌ PASO 3 ERROR GENERAL tipo: {type(e)}")
            logger.error(f"❌ PASO 3 ERROR GENERAL respuesta problemática: '{raw_output}...'")
            logger.error(f"❌ PASO 3 ERROR GENERAL traceback: {traceback.format_exc()}")

            
            return None
    

    # =========================================================================
    # UTILIDADES DE DETECCIÓN Y CONVERSIÓN
    # =========================================================================
    
    def _pdf_has_extractable_text(self, pdf_path: str) -> bool:
        """
        Verifica si un PDF contiene texto digital directamente extraíble.
        Si no, intenta hacer OCR en la primera página como fallback.

        Args:
            pdf_path (str): Ruta al archivo PDF

        Returns:
            bool: True si se puede extraer texto (digital u OCR), False en caso contrario
        """
        try:
            # --- Primera opción: extraer texto directamente con PyPDF2 ---
            reader = PdfReader(pdf_path)
            for page_num, page in enumerate(reader.pages):
                text = page.extract_text()
                if text and text.strip():
                    logger.info(f"✅ Texto extraído en página {page_num + 1} con PyPDF2 ({len(text)} caracteres)")
                    return True

            # --- Fallback: usar PyMuPDF + OCR (Tesseract) ---
            with fitz.open(pdf_path) as doc:
                if len(doc) == 0:
                    logger.info("❌ Documento vacío")
                    return False

                page = doc[0]
                pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                image_bytes = pix.tobytes("png")
                image = Image.open(io.BytesIO(image_bytes))

                text_ocr = pytesseract.image_to_string(image, lang="spa")

                if text_ocr and text_ocr.strip():
                    logger.info(f"🔍 Texto extraído por OCR: {len(text_ocr)} caracteres")
                    return True

            logger.info("🟥 No se pudo extraer texto (ni digital ni por OCR)")
            return False

        except Exception as e:
            logger.warning(f"⚠️ Error al verificar texto en PDF: {e}")
            return False

    def _convert_pdf_to_image(self, pdf_path: str) -> str:
        """
        Convierte la primera página de un PDF a imagen base64.
        
        Args:
            pdf_path: Ruta al archivo PDF
            
        Returns:
            str: Imagen codificada en base64
            
        Raises:
            Exception: Si no se puede convertir el PDF
        """
        try:
            logger.info(f"🖼️ Convirtiendo PDF a imagen: {os.path.basename(pdf_path)}")
            
            # Abrir PDF y obtener primera página
            doc = fitz.open(pdf_path)
            page = doc[0]
            
            # Renderizar a imagen de alta calidad (300 DPI)
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3),alpha=False)
            img_data = pix.tobytes("jpeg")
            doc.close()
            
            logger.info(f"🖼️ Imagen generada: {len(img_data)} bytes, {pix.width}x{pix.height}px")
            
            # Codificar a base64
            base64_image = base64.b64encode(img_data).decode('utf-8')
            #logger.info(f"🖼️ Base64 generado: {len(base64_image)} caracteres")
            
            return base64_image
            
        except Exception as e:
            logger.error(f"❌ Error convirtiendo PDF a imagen: {str(e)}")
            raise

    @staticmethod
    def extract_text_from_base64_image(base64_image: str, lang="spa") -> str:
        """
        Extrae texto OCR desde una imagen codificada en base64.

        Args:
            base64_image (str): Imagen codificada en base64.
            lang (str): Idioma para Tesseract (por defecto 'spa').

        Returns:
            str: Texto extraído.
        """
        try:
            logger.info("🔍 Aplicando OCR a imagen base64...")
            image_bytes = base64.b64decode(base64_image)
            image = Image.open(io.BytesIO(image_bytes))
            text = pytesseract.image_to_string(image, lang=lang)
            logger.info(f"✅ OCR completado: {len(text)} caracteres extraídos")
            return text.strip()
        except Exception as e:
            logger.error(f"❌ Error al aplicar OCR a la imagen: {str(e)}")
            return ""
    
    # =========================================================================
    # PROCESAMIENTO DE JSON Y NORMALIZACIÓN
    # =========================================================================
    
    def _extract_and_normalize_json(self, text: str) -> dict:
        """
        Extrae JSON desde texto y normaliza campos problemáticos.
        
        Maneja:
        - Bloques markdown ```json```
        - Campos que vienen como listas en lugar de valores únicos
        - Conversión de tipos numéricos
        - Limpieza de CDC y RUCs
        
        Args:
            text: Texto que contiene JSON
            
        Returns:
            dict: JSON normalizado
        """
        try:
            logger.info("🔧 JSON EXTRACCIÓN: Iniciando extracción de JSON")
            logger.info(f"🔧 JSON EXTRACCIÓN: Texto de entrada - longitud: {len(text)} caracteres")
            logger.info(f"🔧 JSON EXTRACCIÓN: Primeros 300 chars: '{text[:300]}...'")
            
            # PASO 1: Buscar bloques JSON específicamente
            logger.info("🔧 JSON PASO 1: Buscando JSON en bloque markdown")
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL | re.IGNORECASE)
            if json_match:
                json_content = json_match.group(1)
                logger.info(f"✅ JSON PASO 1 ÉXITO: JSON encontrado en bloque markdown")
                logger.info(f"🔧 JSON PASO 1: Contenido extraído - longitud: {len(json_content)}")
                logger.info(f"🔧 JSON PASO 1: Primeros 200 chars: '{json_content[:200]}...'")
            else:
                logger.info("⚠️ JSON PASO 1: No encontrado en bloque markdown, probando sin markdown")
                # Buscar JSON sin bloques markdown
                json_match = re.search(r'(\{.*\})', text, re.DOTALL)
                if json_match:
                    json_content = json_match.group(1)
                    logger.info(f"✅ JSON PASO 2 ÉXITO: JSON encontrado sin markdown")
                    logger.info(f"🔧 JSON PASO 2: Contenido extraído - longitud: {len(json_content)}")
                    logger.info(f"🔧 JSON PASO 2: Primeros 200 chars: '{json_content[:200]}...'")
                else:
                    logger.warning("⚠️ JSON PASO 2 FALLO: No encontrado patrón JSON, limpiando manualmente")
                    # Fallback: limpiar manualmente
                    cleaned = re.sub(r"```json\s*", "", text.strip(), flags=re.IGNORECASE)
                    cleaned = re.sub(r"```.*$", "", cleaned, flags=re.MULTILINE)
                    cleaned = re.sub(r"^.*?(\{)", r"\1", cleaned, flags=re.DOTALL)
                    cleaned = re.sub(r"(\}).*$", r"\1", cleaned, flags=re.DOTALL)
                    json_content = cleaned.strip()
                    logger.info(f"🔧 JSON PASO 3: JSON limpiado manualmente - longitud: {len(json_content)}")
                    logger.info(f"🔧 JSON PASO 3: Resultado: '{json_content[:200]}...'")
            
            # PASO 2: Parsear JSON
            logger.info("🔧 JSON PARSEO: Intentando parsear JSON")
            try:
                data = json.loads(json_content)
                logger.info(f"✅ JSON PARSEO ÉXITO: JSON parseado exitosamente con {len(data)} campos")
                logger.info(f"🔧 JSON PARSEO: Campos encontrados: {list(data.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON PARSEO ERROR: JSONDecodeError - {e}")
                logger.error(f"❌ JSON PARSEO ERROR: Posición del error: línea {e.lineno}, columna {e.colno}")
                logger.error(f"❌ JSON PARSEO ERROR: Contenido problemático: '{json_content}'")
                raise
            except Exception as e:
                logger.error(f"❌ JSON PARSEO ERROR GENERAL: {e}")
                logger.error(f"❌ JSON PARSEO ERROR GENERAL: Tipo: {type(e)}")
                raise
            
            # PASO 3: Normalizar campos problemáticos
            logger.info("🔧 JSON NORMALIZACIÓN: Iniciando normalización de campos")
            normalized_data = self._normalize_json_fields(data)
            logger.info(f"✅ JSON NORMALIZACIÓN ÉXITO: Normalización completada")
            logger.info(f"🔧 JSON NORMALIZACIÓN: Datos finales: {normalized_data}")
            return normalized_data
            
        except Exception as e:
            logger.error(f"❌ JSON EXTRACCIÓN ERROR GENERAL: {e}")
            logger.error(f"❌ JSON EXTRACCIÓN ERROR GENERAL: Tipo: {type(e)}")
            import traceback
            logger.error(f"❌ JSON EXTRACCIÓN ERROR GENERAL: Traceback: {traceback.format_exc()}")
            raise
    
    def _normalize_json_fields(self, data: dict) -> dict:
        """
        Normaliza campos que pueden venir con formatos incorrectos desde OpenAI.
        
        Problemas comunes:
        - Campos numéricos que vienen como listas
        - CDCs con espacios
        - Strings que vienen como listas
        - Moneda extranjera con tipo de cambio
        
        Args:
            data: Dictionary con datos crudos
            
        Returns:
            dict: Dictionary con datos normalizados
        """
        try:
            # Normalizar campos numéricos (pueden venir como listas)
            numeric_fields = [
                'subtotal_exentas', 'subtotal_5', 'iva_5', 'subtotal_10', 
                'iva_10', 'monto_total'
            ]
            
            for field in numeric_fields:
                if field in data:
                    value = data[field]
                    
                    # Si es lista, tomar primer elemento
                    if isinstance(value, list):
                        logger.warning(f"🔧 Campo {field} vino como lista: {value}")
                        value = value[0] if value else 0
                    
                    # Convertir a número
                    data[field] = self._safe_convert_to_float(value)

            # Limpiar CDC (remover espacios)
            if 'cdc' in data and isinstance(data['cdc'], str):
                data['cdc'] = re.sub(r'\s+', '', data['cdc'])

            # Normalizar campos string que pueden venir como listas
            string_fields = ['numero_factura', 'ruc_emisor', 'nombre_emisor', 'fecha', 'timbrado']
            for field in string_fields:
                if field in data and isinstance(data[field], list):
                    logger.warning(f"🔧 Campo {field} vino como lista: {data[field]}")
                    data[field] = data[field][0] if data[field] else ""

            # Normalizar condicion_venta (puede ser null)
            if 'condicion_venta' in data:
                if data['condicion_venta'] is None:
                    data['condicion_venta'] = "CONTADO"
                elif isinstance(data['condicion_venta'], list):
                    data['condicion_venta'] = data['condicion_venta'][0] if data['condicion_venta'] else "CONTADO"

            # === Normalizar MONEDA ===
            if 'moneda' in data:
                moneda = str(data['moneda']).strip().upper()
                if moneda in ["USD", "DOLAR", "DOLLAR", "$"]:
                    data['moneda'] = "USD"
                elif moneda in ["PYG", "GUARANI", "GS"]:
                    data['moneda'] = "PYG"
                else:
                    logger.warning(f"🔍 Moneda desconocida: {moneda}")
                    data['moneda'] = "PYG"  # Fallback seguro

            # === Normalizar TIPO DE CAMBIO ===
            if 'tipo_cambio' in data:
                try:
                    data['tipo_cambio'] = float(str(data['tipo_cambio']).replace(",", "."))
                except Exception:
                    logger.warning(f"⚠️ Error al convertir tipo_cambio: {data['tipo_cambio']}")
                    data['tipo_cambio'] = None

            logger.info(f"JSON Normalizado: {data}")
            data = self._autocorrect_iva_consistency(data)
            logger.info(f"JSON Normalizado: {data}")
            return data

        except Exception as e:
            logger.error(f"❌ Error normalizando campos: {e}")
            return data

    def _safe_convert_to_float(self, value: Any) -> float:
        """
        Convierte cualquier valor a float de forma segura.
        
        Args:
            value: Valor a convertir
            
        Returns:
            float: Valor convertido o 0.0 si no se puede convertir
        """
        if value is None:
            return 0.0
            
        if isinstance(value, (int, float)):
            return float(value)
            
        try:
            # Limpiar string y convertir
            if isinstance(value, str):
                clean_value = re.sub(r'[^\d.,]', '', str(value))
                if clean_value:
                    return float(clean_value.replace(',', '.'))
            return 0.0
        except:
            return 0.0
    
    # =========================================================================
    # CONSTRUCCIÓN DE PROMPTS
    # =========================================================================
    
    def _build_text_prompt(self, pdf_text: str) -> str:
        """
        Construye prompt específico para procesamiento de texto.
        
        Args:
            pdf_text: Texto extraído del PDF
            
        Returns:
            str: Prompt completo para OpenAI
        """
        base_prompt = self._get_base_prompt()
        return f"{base_prompt}\n\nTexto de la factura:\n{pdf_text}"
    
    def _build_image_prompt(self) -> str:
        """
        Construye prompt específico para procesamiento de imagen.
        
        Returns:
            str: Prompt para OpenAI Vision
        """
        return """
Analiza con extrema atención la imagen de una factura paraguaya. Tu objetivo es extraer datos 100% fieles al contenido visible en la imagen, sin hacer suposiciones ni cálculos automáticos.

Debes devolver el siguiente JSON:

{
  "fecha": "YYYY-MM-DD",
  "numero_factura": "XXX-XXX-XXXXXXX",
  "ruc_emisor": "XXXXXXXX-X",
  "nombre_emisor": "Razón social completa",
  "condicion_venta": "CONTADO",
  "tipo_cambio": 7650,-> Si es USD incluye como cambio puede

  "subtotal_exentas": 0,
  "subtotal_5": 0,
  "iva_5": 0,
  "subtotal_10": 0,
  "iva_10": 0,
  "monto_total": 0,

  "timbrado": "número",
  "cdc": "44 dígitos",
  "ruc_cliente": "número",
  "nombre_cliente": "nombre completo",
  "email_cliente": null,
  "moneda": "GS",
  "actividad_economica": "descripción",

  "empresa": {
    "nombre": "nombre empresa",
    "ruc": "ruc empresa", 
    "direccion": "dirección completa",
    "telefono": "teléfono",
    "actividad_economica": "actividad"
  },
  "timbrado_data": {
    "nro": "número timbrado",
    "fecha_inicio_vigencia": "YYYY-MM-DD",
    "valido_hasta": null
  },
  "factura_data": {
    "contado_nro": null,
    "fecha": "YYYY-MM-DD",
    "caja_nro": null,
    "cdc": "44 dígitos",
    "condicion_venta": "CONTADO"
  },
  "productos": [
    {
      "articulo": "descripción completa del producto",
      "cantidad": 1,
      "precio_unitario": 0,
      "total": 0,
      "iva": 0
    }
  ],
  "totales": {
    "cantidad_articulos": 1,
    "subtotal": 0,
    "total_a_pagar": 0,
    "iva_0%": 0,
    "iva_5%": 0,
    "iva_10%": 0,
    "total_iva": 0
  },
  "cliente": {
    "nombre": "nombre cliente",
    "ruc": "ruc cliente",
    "email": null
  }
}

📌 INSTRUCCIONES CRÍTICAS:

1. 📋 LEE CADA FILA DE LA TABLA DE PRODUCTOS O SERVICIOS.
2. 🔍 Si la tabla tiene columna de IVA (Ej: "IVA %", "IVA 5%", "IVA 10%"), entonces:
   - Usa ese valor exacto para determinar el tipo de IVA.
   - Calcula el subtotal por cada tipo (5%, 10%, exento) sumando solo los productos con ese tipo de IVA.
3. ⚠️ Si **NO HAY columna de IVA visible**:
   - Supón que **todos los productos son exentos**.
   - Registra el total en `subtotal_exentas` y pon `iva_5`, `iva_10` en 0.
4. ✅ Incluye **el campo `iva` por producto** como 0, 5 o 10 según se indique explícitamente en la factura.
5. 🚫 **NO infieras el tipo de IVA** basándote en nombres de productos o montos. Solo usá la información visual explícita.
6. 🧮 El campo `monto_total` debe coincidir exactamente con el valor impreso en la factura. Si hay diferencia, incluye observación textual.
7. 🧾 Todos los campos de montos deben estar en guaraníes (`PYG`) sin realizar conversiones.
8.	💱 Si la factura está expresada en dólares (USD), incluye el campo "moneda": "USD" y el "tipo_cambio" si está visible en la factura.
9.	💸 En facturas en USD, mantén los montos con decimales tal como están impresos. No conviertas a guaraníes ni redondees.
10. Asegurate de leer correctamente filas y columnas para que los montos de iva y los totales tengan sentido 
    ejemplo: si solo tiene iva5 debe el monto de la factura debe ir en subtotal_5.
🧾 Si la tabla de productos no incluye una columna de IVA, debes asumir que todos los productos pertenecen al mismo tipo de IVA que aparece en el resumen "LIQUIDACIÓN IVA".
👉 En ese caso:
- Si solo aparece IVA al 5%, todos los productos deben tener `"iva": 5`.
- Si solo aparece IVA al 10%, todos los productos deben tener `"iva": 10`.
⚠️ Nunca asumas un tipo de IVA por el nombre del producto o por redondeos.
🎯 Tu prioridad es preservar la estructura, los valores visibles y evitar asumir o interpretar campos. Si no se ve, pon null o 0.
🧠 Si el resumen final (LIQUIDACIÓN IVA) muestra solo un tipo de IVA con monto positivo (por ejemplo, solo IVA 5%), y los demás son 0:
➡️ Entonces TODOS los productos deben tener ese mismo tipo de IVA.
⚠️ Si las columnas "Exentas", "5%", "10%" están presentes en la tabla de productos, debes asegurarte de que los montos vayan en la columna correspondiente. Nunca coloques montos en `subtotal_10` si el total impreso aparece bajo la columna `5%`.
⚠️ Esto se aplica incluso si no está indicado el tipo de IVA por producto.
"""
        #return self._get_base_prompt()
    
    def _get_base_prompt(self) -> str:
        """
        Prompt base con instrucciones para extracción de facturas paraguayas.
        
        Returns:
            str: Prompt base con formato JSON requerido
        """
        return """
Analiza cuidadosamente el siguiente contenido textual de una factura electrónica paraguaya. Tu tarea es extraer los datos con la **máxima fidelidad posible**, sin asumir ni calcular montos que no estén explícitamente especificados.

Debes devolver un JSON con esta estructura:

{
  "fecha": "YYYY-MM-DD",
  "numero_factura": "XXX-XXX-XXXXXXX",
  "ruc_emisor": "XXXXXXXX-X",
  "nombre_emisor": "Razón social completa",
  "condicion_venta": "CONTADO",
  "tipo_cambio": 7650,-> Si es USD incluye como cambio puede

  "subtotal_exentas": 0,
  "subtotal_5": 0,
  "iva_5": 0,
  "subtotal_10": 0,
  "iva_10": 0,
  "monto_total": 0,

  "timbrado": "número",
  "cdc": "44 dígitos",
  "ruc_cliente": "número",
  "nombre_cliente": "nombre completo",
  "email_cliente": null,
  "moneda": "GS",
  "actividad_economica": "descripción",

  "empresa": {
    "nombre": "nombre empresa",
    "ruc": "ruc empresa", 
    "direccion": "dirección completa",
    "telefono": "teléfono",
    "actividad_economica": "actividad"
  },
  "timbrado_data": {
    "nro": "número timbrado",
    "fecha_inicio_vigencia": "YYYY-MM-DD",
    "valido_hasta": null
  },
  "factura_data": {
    "contado_nro": null,
    "fecha": "YYYY-MM-DD",
    "caja_nro": null,
    "cdc": "44 dígitos",
    "condicion_venta": "CONTADO"
  },
  "productos": [
    {
      "articulo": "descripción completa del producto",
      "cantidad": 1,
      "precio_unitario": 0,
      "total": 0,
      "iva": 0
    }
  ],
  "totales": {
    "cantidad_articulos": 1,
    "subtotal": 0,
    "total_a_pagar": 0,
    "iva_0%": 0,
    "iva_5%": 0,
    "iva_10%": 0,
    "total_iva": 0
  },
  "cliente": {
    "nombre": "nombre cliente",
    "ruc": "ruc cliente",
    "email": null
  }
}

📌 INSTRUCCIONES CLAVE PARA EL ANÁLISIS DE TEXTO:

1. 🧾 LOCALIZA LA TABLA DE PRODUCTOS: Si hay columnas de IVA explícitas (como "IVA", "%", "5%", "10%", "Exentas"), úsalas como fuente confiable para clasificar los productos.
2. ❌ NO infieras el tipo de IVA por nombre del producto ni realices cálculos automáticos de IVA.
3. 🔢 LEE LOS TOTALES IMPRESOS en la parte final de la factura. Usa el "Total a pagar" como `monto_total`.
4. ✅ Si NO hay columnas de IVA visibles ni totales discriminados por tipo:
   - Coloca todos los productos como exentos (`iva = 0`)
   - Suma sus montos en `subtotal_exentas`
   - Los campos de `iva_5`, `iva_10` deben ser 0
5. 🔐 NO modifiques ni recalcules valores. Usa los valores impresos tal como están.
6. 📋 Incluye el campo `iva` por producto con valor 0, 5 o 10, según indique la tabla.
7. 🧠 Si algún valor es ilegible o está ausente, coloca `null` o 0 según el caso.
8.	💱 Si la factura está expresada en dólares (USD), incluye el campo "moneda": "USD" y el "tipo_cambio" si está visible en la factura.
9.	💸 En facturas en USD, mantén los montos con decimales tal como están impresos. No conviertas a guaraníes ni redondees.
10. Asegurate de leer correctamente filas y columnas para que los montos de iva y los totales tengan sentido 
    ejemplo: si solo tiene iva5 debe el monto de la factura debe ir en subtotal_5.
⚠️ No infieras el valor del IVA por producto si no está explícitamente impreso al lado del ítem.

🧾 Si la tabla de productos no incluye una columna de IVA, debes asumir que todos los productos pertenecen al mismo tipo de IVA que aparece en el resumen "LIQUIDACIÓN IVA".
👉 En ese caso:
- Si solo aparece IVA al 5%, todos los productos deben tener `"iva": 5`.
- Si solo aparece IVA al 10%, todos los productos deben tener `"iva": 10`.
⚠️ Nunca asumas un tipo de IVA por el nombre del producto o por redondeos.
    
🎯 Tu prioridad es la **fidelidad exacta al texto visible de la factura** y la correcta clasificación del IVA.
🧠 Si el resumen final (LIQUIDACIÓN IVA) muestra solo un tipo de IVA con monto positivo (por ejemplo, solo IVA 5%), y los demás son 0:
➡️ Entonces TODOS los productos deben tener ese mismo tipo de IVA.
⚠️ Si las columnas "Exentas", "5%", "10%" están presentes en la tabla de productos, debes asegurarte de que los montos vayan en la columna correspondiente. Nunca coloques montos en `subtotal_10` si el total impreso aparece bajo la columna `5%`.
⚠️ Esto se aplica incluso si no está indicado el tipo de IVA por producto.
"""
    
    # =========================================================================
    # VALIDACIÓN Y CORRECCIÓN CDC
    # =========================================================================
    
    def _validate_and_enhance_with_cdc(self, invoice: InvoiceData) -> InvoiceData:
        """
        Valida y mejora la fecha de emisión de la factura usando el CDC (Código de Control).
        La fecha del CDC es más confiable que la extraída por OCR o IA, ya que está codificada oficialmente.

        Si la fecha de OpenAI es posterior a la del CDC o está ausente, se reemplaza por la del CDC.
        Si el CDC no tiene una fecha válida o lógica (antes de 2020, o formato inválido), se omite la corrección.

        Args:
            invoice (InvoiceData): Objeto con datos extraídos de la factura.

        Returns:
            InvoiceData: Factura corregida o sin modificar si el CDC no aplica.
        """
        try:
            logger.info("🔍 VALIDANDO factura contra CDC")
            cdc = getattr(invoice, 'cdc', '').replace(" ", "").strip()

            if not cdc or len(cdc) != 44 or not cdc.isdigit():
                logger.warning("❌ CDC no disponible o inválido (longitud ≠ 44 o no numérico). Omitiendo validación.")
                return invoice

            # Extraer fecha desde posición 11 a 18 (CDC estándar paraguayo)
            fecha_raw = cdc[10:18]  # cdc[10:18] corresponde a posiciones 11 a 18 (0-based index)
            logger.info(f"📆 Fecha extraída cruda del CDC: {fecha_raw}")

            # Validar patrón de fecha AAAAMMDD
            if not re.match(r'20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])', fecha_raw):
                logger.warning(f"⚠️ Fecha en CDC no cumple formato válido: {fecha_raw}")
                return invoice

            # Convertir a objeto fecha
            try:
                fecha_cdc = datetime.strptime(fecha_raw, "%Y%m%d").date()
            except ValueError as e:
                logger.warning(f"⚠️ Fecha inválida al parsear: {fecha_raw} – Error: {e}")
                return invoice

            # Validación lógica (evitar fechas ridículas)
            if fecha_cdc.year < 2020 or fecha_cdc > datetime.today().date():
                logger.warning(f"⚠️ Fecha CDC fuera de rango lógico: {fecha_cdc}")
                return invoice

            # Comparación con la fecha de OpenAI
            if invoice.fecha:
                fecha_openai = invoice.fecha
                logger.info(f"📅 Fecha OpenAI: {fecha_openai} | 📅 Fecha CDC: {fecha_cdc}")

                if fecha_openai > fecha_cdc:
                    logger.warning(f"🔁 CORRECCIÓN: Fecha OpenAI ({fecha_openai}) > CDC ({fecha_cdc}) → Se corrige.")
                    invoice.fecha = fecha_cdc
                else:
                    logger.info("✅ Fecha OpenAI es confiable o anterior a CDC. No se corrige.")
            else:
                logger.info(f"🛠️ Estableciendo fecha directamente desde CDC: {fecha_cdc}")
                invoice.fecha = fecha_cdc

            return invoice

        except Exception as e:
            logger.error(f"❌ Error inesperado al validar contra CDC: {str(e)}")
            return invoice
   
    def _autocorrect_iva_consistency(self, data: dict) -> dict:
        """
        Si los productos tienen iva = 0 pero el resumen muestra solo IVA al 5% o 10%, corrige todos los productos.

        Args:
            data: Diccionario JSON extraído por OpenAI
        
        Returns:
            dict: JSON corregido si aplica
        """
        try:
            productos = data.get("productos", [])
            iva_5 = data.get("iva_5", 0)
            iva_10 = data.get("iva_10", 0)

            if not productos:
                return data  # Nada que corregir

            # Detectar si todos los productos tienen iva = 0 o falta el campo
            todos_sin_iva = all(str(p.get("iva", 0)).strip() in ["0", ""] for p in productos)

            if todos_sin_iva:
                if iva_5 > 0 and iva_10 == 0:
                    logger.info("🔁 Corrigiendo todos los productos a IVA 5%")
                    for p in productos:
                        p["iva"] = 5
                    data["productos"] = productos
                    return data
                elif iva_10 > 0 and iva_5 == 0:
                    logger.info("🔁 Corrigiendo todos los productos a IVA 10%")
                    for p in productos:
                        p["iva"] = 10
                    data["productos"] = productos
                    return data
                else:
                    logger.info("🧾 No se puede inferir un único IVA dominante, no se corrige")

            return data

        except Exception as e:
            logger.warning(f"⚠️ Error en _autocorrect_iva_consistency: {e}")
            return data
    

    def _extract_real_company_name_from_pdf(self, pdf_path: str, ruc: str) -> str:
        """
        Extrae el nombre REAL de la empresa del PDF.
        
        NO inventa nombres - busca patrones reales como S.A., S.R.L., etc.
        en el texto del documento.
        
        Args:
            pdf_path: Ruta al PDF (puede ser None)
            ruc: RUC de la empresa
            
        Returns:
            str: Nombre real extraído o indicación de revisión manual
        """
        try:
            if not pdf_path or not os.path.exists(pdf_path):
                logger.warning("🔍 PDF no disponible para extracción de nombre")
                return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
            
            # Extraer texto completo del PDF
            try:
                text_content = extract_text_pdfminer(pdf_path)
                if isinstance(text_content, list):
                    logger.warning("🔍 extract_text devolvió lista, combinando elementos")
                    text_content = " ".join(str(item) for item in text_content if item)
                text_content = str(text_content).strip()
            except Exception as e:
                logger.error(f"🔍 Error extrayendo texto del PDF: {e}")
                return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
                
            if not text_content:
                logger.warning("🔍 No se pudo extraer texto del PDF")
                return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
            
            logger.info(f"🔍 Buscando nombre real en {len(text_content)} caracteres de texto")
            
            # Patrones para nombres empresariales paraguayos REALES
            company_patterns = [
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+S\.A\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+S\.R\.L\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+S\.A\.E\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+LTDA)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?)(?:\s+COOPERATIVA)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?S\.A\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?S\.R\.L\.)',
                r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,.-]+?COOPERATIVA)',
            ]
            
            # Buscar patrones de nombres empresariales
            for pattern in company_patterns:
                matches = re.findall(pattern, text_content, re.IGNORECASE)
                if matches:
                    # Tomar el primer match y limpiarlo
                    nombre_encontrado = matches[0].strip()
                    # Reconstruir nombre completo con sufijo
                    if "S.A." in pattern:
                        nombre_completo = f"{nombre_encontrado} S.A."
                    elif "S.R.L." in pattern:
                        nombre_completo = f"{nombre_encontrado} S.R.L."
                    elif "S.A.E." in pattern:
                        nombre_completo = f"{nombre_encontrado} S.A.E."
                    elif "LTDA" in pattern:
                        nombre_completo = f"{nombre_encontrado} LTDA"
                    elif "COOPERATIVA" in pattern:
                        nombre_completo = f"{nombre_encontrado} COOPERATIVA"
                    else:
                        nombre_completo = nombre_encontrado
                        
                    logger.info(f"🔍 ✅ Nombre real encontrado: {nombre_completo}")
                    return nombre_completo
            
            # Si no encontró patrones específicos, buscar líneas con el RUC
            ruc_clean = ruc.replace('-', '')
            lines = text_content.split('\n')
            for line in lines:
                if ruc_clean in line or ruc in line:
                    # Buscar nombre en la misma línea
                    line_clean = line.strip()
                    if len(line_clean) > 20:  # Línea tiene contenido suficiente
                        # Remover RUC de la línea y ver qué queda
                        line_without_ruc = re.sub(r'\d{7,9}-?\d', '', line_clean).strip()
                        if len(line_without_ruc) > 5:
                            logger.info(f"🔍 ✅ Nombre extraído de línea con RUC: {line_without_ruc}")
                            return line_without_ruc
            
            logger.warning("🔍 No se encontró nombre empresarial real en el PDF")
            return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
            
        except Exception as e:
            logger.error(f"❌ Error extrayendo nombre real: {e}")
            return f"RUC {ruc} - REVISAR NOMBRE MANUALMENTE"
    
    
    def extract_invoice_data_from_xml(self, xml_path: str, email_metadata: Dict[str, Any] = None) -> Optional[InvoiceData]:
        """
        Procesa un archivo XML de factura electrónica paraguaya usando OpenAI para estructurar los datos.

        Args:
            xml_path: Ruta al archivo XML (XML DTE oficial)
            email_metadata: Metadatos del correo electrónico

        Returns:
            InvoiceData o None
        """
        try:
            logger.info(f"📄 Procesando XML con OpenAI: {xml_path}")

            if not os.path.exists(xml_path):
                logger.warning("⛔ El archivo XML no existe")
                return None

            with open(xml_path, "r", encoding="utf-8") as f:
                xml_content = f.read()

            logger.info(f"✅ XML cargado: {len(xml_content)} caracteres")

            # Construir prompt para OpenAI
            prompt = self._build_prompt_for_xml(xml_content)
            messages = [{"role": "user", "content": prompt}]

            logger.info("🤖 Enviando XML a OpenAI...")
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=1500,
                temperature=0.2
            )

            raw_output = response.choices[0].message.content
            logger.info(f"🤖 Respuesta OpenAI recibida: {len(raw_output)} caracteres")

            # Procesar JSON
            result = self._process_openai_response(raw_output, email_metadata)
            if result:
                logger.info("✅ Resultado procesado exitosamente desde XML")
                return result
            else:
                logger.warning("⛔ Result is None.")
                return None

        except Exception as e:
            logger.error(f"❌ Error procesando XML con OpenAI: {e}")
            return None


    def _build_prompt_for_xml(self, xml_content: str) -> str:
        """
        Construye un prompt especializado para interpretar XML DTE de facturas paraguayas.

        Args:
            xml_content: Contenido del archivo XML

        Returns:
            str: Prompt completo para OpenAI
        """
        base_prompt = self._get_base_prompt()

        return f"""
A continuación se provee el contenido bruto de un archivo XML correspondiente a una factura electrónica de Paraguay. Tu tarea es analizarlo cuidadosamente y extraer todos los datos relevantes siguiendo el siguiente formato JSON estructurado. 

Debes devolver únicamente el JSON sin explicación adicional. 

{base_prompt}

Contenido XML:
```xml
{xml_content}
```
"""