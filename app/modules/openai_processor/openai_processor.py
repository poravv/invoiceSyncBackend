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
            
        except Exception as e:
            logger.error(f"❌ Error en procesamiento OpenAI: {str(e)}")
        
        # ESTRATEGIA 3: Fallback a factura básica
        logger.info("🔧 Creando factura básica como fallback")
        basic_invoice = self._create_basic_invoice_from_filename(pdf_path, email_metadata)
        if basic_invoice:
            return self._enhance_basic_invoice_with_cdc(basic_invoice)
        
        return basic_invoice

    def _process_as_text(self, pdf_path: str, email_metadata: Dict[str, Any] = None) -> Optional[InvoiceData]:
        """
        Procesa PDF extrayendo texto y enviándolo a OpenAI.
        
        Args:
            pdf_path: Ruta al PDF
            email_metadata: Metadatos del email
            
        Returns:
            InvoiceData procesada o None si falla
        """
        try:
            logger.info("📄 Procesando PDF como TEXTO")
            
            # PASO 1: Extraer texto del PDF con manejo de errores robusto
            logger.info("📄 PASO 1: Iniciando extracción de texto del PDF")
            try:
                pdf_text = extract_text(pdf_path)
                logger.info(f"📄 PASO 1a: extract_text completado, tipo resultado: {type(pdf_text)}")
                
                if isinstance(pdf_text, list):
                    logger.warning(f"📄 PASO 1b: extract_text devolvió lista con {len(pdf_text)} elementos")
                    logger.info(f"📄 PASO 1b: Elementos de la lista: {pdf_text[:3]}...")  # Primeros 3 elementos
                    pdf_text = " ".join(str(item) for item in pdf_text if item)
                    logger.info(f"📄 PASO 1b: Lista combinada exitosamente")
                
                pdf_text = str(pdf_text).strip()
                logger.info(f"📄 PASO 1c: Texto convertido a string y limpiado")
                
            except Exception as e:
                logger.error(f"📄 PASO 1 ERROR CRÍTICO en extract_text: {e}")
                logger.error(f"📄 PASO 1 ERROR tipo de excepción: {type(e)}")
                import traceback
                logger.error(f"📄 PASO 1 ERROR traceback: {traceback.format_exc()}")
                return None
                
            if not pdf_text:
                logger.warning("📄 PASO 1 FALLO: No se pudo extraer texto del PDF")
                return None
                
            logger.info(f"📄 PASO 1 ÉXITO: Texto extraído - {len(pdf_text)} caracteres")
            logger.info(f"📄 PASO 1 MUESTRA: '{pdf_text[:200]}...'")
            
            # PASO 2: Construir prompt y enviar a OpenAI
            logger.info("📄 PASO 2: Construyendo prompt para OpenAI")
            prompt = self._build_text_prompt(pdf_text)
            logger.info(f"📄 PASO 2a: Prompt construido - {len(prompt)} caracteres")
            
            messages = [{"role": "user", "content": prompt}]
            logger.info("📄 PASO 2b: Enviando request a OpenAI...")
            
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=1000,
                temperature=0.3
            )
            
            raw_output = response.choices[0].message.content
            logger.info(f"📄 PASO 2 ÉXITO: Respuesta OpenAI recibida - {len(raw_output)} caracteres")
            logger.info(f"📄 PASO 2 RESPUESTA COMPLETA: {raw_output}")
            
            # PASO 3: Procesar respuesta con validación mejorada
            logger.info("📄 PASO 3: Procesando respuesta de OpenAI")
            result = self._process_openai_response(raw_output, email_metadata, fallback_text=pdf_text)
            
            if result:
                logger.info("📄 PASO 3 ÉXITO: Procesamiento de texto completado exitosamente")
                logger.info(f"📄 PASO 3 RESULTADO: RUC={getattr(result, 'ruc_emisor', 'N/A')}, Nombre={getattr(result, 'nombre_emisor', 'N/A')}")
            else:
                logger.warning("📄 PASO 3 FALLO: _process_openai_response devolvió None")
            
            return result
            
        except Exception as e:
            logger.error(f"📄 ERROR GENERAL en procesamiento de texto: {str(e)}")
            logger.error(f"📄 ERROR GENERAL tipo: {type(e)}")
            import traceback
            logger.error(f"📄 ERROR GENERAL traceback: {traceback.format_exc()}")
            return None

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
Analiza esta imagen de factura paraguaya con EXTREMO CUIDADO. Lee todos los textos visibles, incluso si están escritos a mano o son difíciles de leer.

Extrae TODOS los siguientes campos en formato JSON:

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
  "moneda": "PYG",
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
      "total": 0
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

🔍 INSTRUCCIONES ESPECÍFICAS PARA IMAGEN:

1. LEE CUIDADOSAMENTE toda la tabla de productos/servicios
2. BUSCA los precios unitarios y totales de cada producto
3. Si NO encuentras columnas específicas de "IVA 5%" o "IVA 10%", entonces:
   - Coloca TODOS los montos en "subtotal_exentas"
   - El "monto_total" debe ser igual a "subtotal_exentas"
   - Deja en 0 los campos de IVA 5% y 10%

4. Si SÍ encuentras columnas de IVA, distribúyelos correctamente

5. SUMA todos los productos para verificar el total

6. Lee nombres y RUCs con MÁXIMO CUIDADO, incluso si están borrosos

7. NO inventes datos que no puedas ver claramente

8. Si algo no es legible, coloca null o 0 según corresponda

IMPORTANTE: Esta factura podría NO tener IVA (ser 100% exenta). En ese caso, todos los montos van en "subtotal_exentas" y "monto_total".
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
            logger.info(f"🔄 PASO 3a: Primeros 300 chars: '{raw_output[:300]}...'")
            
            # PASO 3.1: Verificar si OpenAI rechazó el procesamiento
            logger.info("🔄 PASO 3.1: Verificando si OpenAI rechazó el procesamiento")
            rejection_phrases = ["no puedo ayudar", "lo siento", "no es posible"]
            for phrase in rejection_phrases:
                if phrase in raw_output.lower():
                    logger.warning(f"🚫 PASO 3.1 RECHAZO: OpenAI rechazó con frase: '{phrase}'")
                    if fallback_text:
                        logger.info("🔧 PASO 3.1 FALLBACK: Intentando extraer datos básicos del texto")
                        return self._extract_basic_data_from_text(fallback_text, email_metadata)
                    return None
            
            logger.info("✅ PASO 3.1 ÉXITO: OpenAI no rechazó el procesamiento")
            
            # PASO 3.2: Extraer y normalizar JSON
            logger.info("🔄 PASO 3.2: Extrayendo y normalizando JSON")
            json_data = self._extract_and_normalize_json(raw_output)
            logger.info(f"🔄 PASO 3.2a: JSON extraído, tipo: {type(json_data)}")
            
            if not isinstance(json_data, dict):
                logger.warning(f"⚠️ PASO 3.2 FALLO: JSON inválido - tipo: {type(json_data)}, valor: {json_data}")
                if fallback_text:
                    logger.info("🔧 PASO 3.2 FALLBACK: Extrayendo datos básicos del texto")
                    return self._extract_basic_data_from_text(fallback_text, email_metadata)
                raise ValueError("Respuesta no es un objeto JSON válido")
            
            logger.info(f"✅ PASO 3.2 ÉXITO: JSON válido extraído con {len(json_data)} campos")
            logger.info(f"🔄 PASO 3.2 CAMPOS: {list(json_data.keys())}")
            
            # Loggear algunos campos clave
            key_fields = ["ruc_emisor", "nombre_emisor", "fecha", "monto_total", "cdc"]
            for field in key_fields:
                if field in json_data:
                    logger.info(f"🔄 PASO 3.2 {field}: {json_data[field]}")

            # PASO 3.3: Crear factura desde datos de OpenAI
            logger.info("🔄 PASO 3.3: Creando factura desde datos de OpenAI")
            invoice = InvoiceData.from_dict(json_data, email_metadata)
            logger.info(f"🔄 PASO 3.3a: from_dict completado, resultado: {type(invoice)}")
            
            if invoice is None:
                logger.warning("⚠️ PASO 3.3 FALLO: from_dict devolvió None")
                if fallback_text:
                    logger.info("🔧 PASO 3.3 FALLBACK: Extrayendo datos básicos del texto")
                    return self._extract_basic_data_from_text(fallback_text, email_metadata)
                raise ValueError("El resultado de from_dict fue None")

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
                import traceback
                logger.warning(f"⚠️ PASO 3.4 ERROR traceback: {traceback.format_exc()}")
                return invoice

        except Exception as e:
            logger.error(f"❌ PASO 3 ERROR GENERAL procesando respuesta OpenAI: {str(e)}")
            logger.error(f"❌ PASO 3 ERROR GENERAL tipo: {type(e)}")
            logger.error(f"❌ PASO 3 ERROR GENERAL respuesta problemática: '{raw_output[:200]}...'")
            import traceback
            logger.error(f"❌ PASO 3 ERROR GENERAL traceback: {traceback.format_exc()}")
            
            if fallback_text:
                logger.info("🔧 PASO 3 FALLBACK FINAL: Extrayendo datos básicos del texto")
                return self._extract_basic_data_from_text(fallback_text, email_metadata)
            
            return None
    
    def _extract_basic_data_from_text(self, pdf_text: str, email_metadata: Dict[str, Any] = None) -> Optional[InvoiceData]:
        """
        Extrae datos básicos directamente del texto del PDF usando regex.
        
        Esta función sirve como fallback cuando OpenAI falla.
        
        Args:
            pdf_text: Texto extraído del PDF
            email_metadata: Metadatos del email
            
        Returns:
            InvoiceData con datos básicos extraídos o None si falla
        """
        try:
            logger.info("🔧 Extrayendo datos básicos del texto PDF")
            
            # Diccionario para datos extraídos
            data = {
                "fecha": None,
                "numero_factura": "",
                "ruc_emisor": "",
                "nombre_emisor": "",
                "condicion_venta": "CONTADO",
                "subtotal_exentas": 0,
                "subtotal_5": 0,
                "iva_5": 0,
                "subtotal_10": 0,
                "iva_10": 0,
                "monto_total": 0,
                "timbrado": "",
                "cdc": "",
                "moneda": "PYG"
            }
            
            # Buscar CDC (44 dígitos)
            cdc_match = re.search(r'\b(\d{44})\b', pdf_text)
            if cdc_match:
                data["cdc"] = cdc_match.group(1)
                logger.info(f"🔧 CDC encontrado: {data['cdc']}")
            
            # Buscar RUC (formato X{7,8}-X)
            ruc_match = re.search(r'\b(\d{7,8}-\d)\b', pdf_text)
            if ruc_match:
                data["ruc_emisor"] = ruc_match.group(1)
                logger.info(f"🔧 RUC encontrado: {data['ruc_emisor']}")
            
            # Buscar fecha (varios formatos)
            fecha_patterns = [
                r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b',  # DD/MM/YYYY
                r'\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b',  # YYYY/MM/DD
                r'(\d{1,2})\s+de\s+\w+\s+de\s+(\d{4})',   # DD de MONTH de YYYY
            ]
            
            for pattern in fecha_patterns:
                fecha_match = re.search(pattern, pdf_text, re.IGNORECASE)
                if fecha_match:
                    try:
                        # Determinar formato y parsear
                        grupos = fecha_match.groups()
                        if len(grupos) == 3:
                            if len(grupos[0]) == 4:  # YYYY first
                                fecha_str = f"{grupos[0]}-{grupos[1].zfill(2)}-{grupos[2].zfill(2)}"
                            else:  # DD first
                                fecha_str = f"{grupos[2]}-{grupos[1].zfill(2)}-{grupos[0].zfill(2)}"
                            
                            from app.utils.date_utils import try_parse_date
                            fecha_parsed = try_parse_date(fecha_str)
                            if fecha_parsed:
                                data["fecha"] = fecha_parsed
                                logger.info(f"🔧 Fecha encontrada: {fecha_str}")
                                break
                    except Exception as e:
                        logger.warning(f"🔧 Error parseando fecha: {e}")
                        continue
            
            # Buscar montos (buscar números con formato monetario)
            total_patterns = [
                r'total[:\s]*[\w\s]*?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)',
                r'(?:gs|₲)[:\s]*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)',
                r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*(?:gs|₲)',
            ]
            
            for pattern in total_patterns:
                total_match = re.search(pattern, pdf_text, re.IGNORECASE)
                if total_match:
                    try:
                        monto_str = total_match.group(1).replace(',', '').replace('.', '')
                        # Asumir que los últimos 2 dígitos son centavos si el número es grande
                        if len(monto_str) > 4:
                            monto = float(monto_str[:-2] + '.' + monto_str[-2:])
                        else:
                            monto = float(monto_str)
                        
                        if monto > 0:
                            data["monto_total"] = monto
                            data["subtotal_exentas"] = monto  # Asumir exento por defecto
                            logger.info(f"🔧 Monto total encontrado: {monto}")
                            break
                    except (ValueError, IndexError) as e:
                        logger.warning(f"🔧 Error parseando monto: {e}")
                        continue
            
            # Buscar timbrado
            timbrado_match = re.search(r'timbrado[:\s]*(\d+)', pdf_text, re.IGNORECASE)
            if timbrado_match:
                data["timbrado"] = timbrado_match.group(1)
                logger.info(f"🔧 Timbrado encontrado: {data['timbrado']}")
            
            # Buscar número de factura
            factura_patterns = [
                r'factura[:\s]*(\d{3}-\d{3}-\d{7})',
                r'n[úu]mero[:\s]*(\d{3}-\d{3}-\d{7})',
                r'(\d{3}-\d{3}-\d{7})',
            ]
            
            for pattern in factura_patterns:
                factura_match = re.search(pattern, pdf_text, re.IGNORECASE)
                if factura_match:
                    data["numero_factura"] = factura_match.group(1)
                    logger.info(f"🔧 Número factura encontrado: {data['numero_factura']}")
                    break
            
            # Crear factura con datos extraídos
            invoice = InvoiceData.from_dict(data, email_metadata)
            if invoice:
                logger.info("✅ Datos básicos extraídos exitosamente del texto")
                # Intentar mejora con CDC si está disponible
                if data.get("cdc"):
                    return self._enhance_basic_invoice_with_cdc(invoice)
                return invoice
            else:
                logger.warning("⚠️ No se pudo crear factura con datos básicos")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error extrayendo datos básicos del texto: {e}")
            return None
    
    # =========================================================================
    # UTILIDADES DE DETECCIÓN Y CONVERSIÓN
    # =========================================================================
    
    def _pdf_has_extractable_text(self, pdf_path: str) -> bool:
        """
        Determina si un PDF tiene texto extractible o es una imagen escaneada.
        
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
            logger.warning(f"⚠️ Error verificando texto en PDF: {str(e)}")
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
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
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
        
        Args:
            data: Dictionary con datos crudos
            
        Returns:
            dict: Dictionary con datos normalizados
        """
        try:
            logger.info("🔧 NORMALIZACIÓN: Iniciando normalización de campos JSON")
            logger.info(f"🔧 NORMALIZACIÓN: Campos de entrada: {list(data.keys())}")
            
            # PASO 1: Normalizar campos numéricos (pueden venir como listas)
            logger.info("🔧 NORMALIZACIÓN PASO 1: Procesando campos numéricos")
            numeric_fields = [
                'subtotal_exentas', 'subtotal_5', 'iva_5', 'subtotal_10', 
                'iva_10', 'monto_total'
            ]
            
            for field in numeric_fields:
                if field in data:
                    original_value = data[field]
                    logger.info(f"🔧 NORMALIZACIÓN {field}: Valor original: {original_value} (tipo: {type(original_value)})")
                    
                    # Si es lista, tomar primer elemento
                    if isinstance(original_value, list):
                        logger.warning(f"🔧 NORMALIZACIÓN {field}: Campo vino como lista: {original_value}")
                        if original_value:
                            data[field] = self._safe_float_convert(original_value[0])
                            logger.info(f"🔧 NORMALIZACIÓN {field}: Tomado primer elemento: {data[field]}")
                        else:
                            data[field] = 0.0
                            logger.info(f"🔧 NORMALIZACIÓN {field}: Lista vacía, asignado 0.0")
                    else:
                        # Convertir a float de manera segura
                        data[field] = self._safe_float_convert(original_value)
                        logger.info(f"🔧 NORMALIZACIÓN {field}: Convertido a float: {data[field]}")
            
            # PASO 2: Limpiar campos de string (CDC, RUCs, etc.)
            logger.info("🔧 NORMALIZACIÓN PASO 2: Procesando campos de string")
            string_fields = ['cdc', 'ruc_emisor', 'numero_factura', 'timbrado']
            
            for field in string_fields:
                if field in data:
                    original_value = data[field]
                    logger.info(f"🔧 NORMALIZACIÓN {field}: Valor original: '{original_value}' (tipo: {type(original_value)})")
                    
                    if isinstance(original_value, list):
                        logger.warning(f"🔧 NORMALIZACIÓN {field}: Campo string vino como lista: {original_value}")
                        if original_value:
                            data[field] = str(original_value[0]).strip()
                            logger.info(f"🔧 NORMALIZACIÓN {field}: Tomado primer elemento: '{data[field]}'")
                        else:
                            data[field] = ""
                            logger.info(f"🔧 NORMALIZACIÓN {field}: Lista vacía, asignado string vacío")
                    elif original_value is not None:
                        data[field] = str(original_value).strip()
                        logger.info(f"🔧 NORMALIZACIÓN {field}: Convertido a string: '{data[field]}'")
            
            # PASO 3: Limpiar CDC específicamente (remover espacios)
            if 'cdc' in data and isinstance(data['cdc'], str):
                original_cdc = data['cdc']
                data['cdc'] = re.sub(r'\s+', '', data['cdc'])
                logger.info(f"🔧 NORMALIZACIÓN CDC: Limpiado de '{original_cdc}' a '{data['cdc']}'")
            
            # PASO 4: Normalizar fechas
            if 'fecha' in data and data['fecha']:
                original_fecha = data['fecha']
                logger.info(f"🔧 NORMALIZACIÓN fecha: Valor original: '{original_fecha}' (tipo: {type(original_fecha)})")
                
                if isinstance(original_fecha, list) and original_fecha:
                    data['fecha'] = str(original_fecha[0])
                    logger.info(f"🔧 NORMALIZACIÓN fecha: Lista convertida a: '{data['fecha']}'")
                elif original_fecha is not None:
                    data['fecha'] = str(original_fecha)
                    logger.info(f"🔧 NORMALIZACIÓN fecha: Convertido a string: '{data['fecha']}'")
            
            logger.info("✅ NORMALIZACIÓN ÉXITO: Todos los campos normalizados")
            logger.info(f"🔧 NORMALIZACIÓN RESULTADO: {data}")
            return data
            
        except Exception as e:
            logger.error(f"❌ NORMALIZACIÓN ERROR: {e}")
            logger.error(f"❌ NORMALIZACIÓN ERROR tipo: {type(e)}")
            import traceback
            logger.error(f"❌ NORMALIZACIÓN ERROR traceback: {traceback.format_exc()}")
            # Devolver datos originales en caso de error
            return data
    
    def _safe_float_convert(self, value):
        """Convierte un valor a float de manera segura."""
        try:
            if value is None or value == "":
                return 0.0
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                # Limpiar separadores de miles y comas
                cleaned = value.replace(',', '').replace(' ', '')
                return float(cleaned) if cleaned else 0.0
            return 0.0
        except (ValueError, TypeError):
            logger.warning(f"🔧 No se pudo convertir '{value}' a float, usando 0.0")
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

INSTRUCCIONES CRÍTICAS:
- TODOS los campos son obligatorios. Si no encuentras el valor, devuelve null para texto o 0 para números
- Los montos deben ser números sin separadores de miles ni símbolos
- RUC debe incluir el guión (ej: "80014066-4")
- Fechas en formato YYYY-MM-DD
- NO uses markdown ni ```json en la respuesta
- Extrae EXACTAMENTE los valores de cada columna de la tabla de productos
- Separa correctamente los importes por tasa de IVA (Exentas, 5%, 10%)

Ten en cuenta que:
La factura puede estar en formato vertical u horizontal
Algunos campos pueden estar escritos a mano
El orden y posición de los campos puede variar
Algunos campos pueden estar ausentes o tener nombres ligeramente diferentes

Busca específicamente:
- Información de la empresa: nombre completo, RUC, dirección y teléfono (pueden estar en el encabezado o pie)
- Datos del timbrado: número, fechas de vigencia (suelen estar en la parte superior)
- Número de factura y fecha (pueden estar en cualquier parte superior)
- Productos/servicios: descripción, cantidad, precio unitario y total
- Totales: subtotal, IVA (discriminado en 0%, 5%, 10%), total a pagar
- Información del cliente: nombre y RUC (pueden estar al inicio o al final)

Si encuentras campos escritos a mano, intenta interpretarlos lo mejor posible.
Si no puedes leer algo con certeza, déjalo en blanco.
"""
        #return self._get_base_prompt()
    
    def _get_base_prompt(self) -> str:
        """
        Prompt base con instrucciones para extracción de facturas paraguayas.
        
        Returns:
            str: Prompt base con formato JSON requerido
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

⚠️ INSTRUCCIONES CRÍTICAS PARA MONTOS:
- Si la factura NO tiene IVA 5% ni IVA 10%, pero sí tiene un monto total, coloca ese monto en "subtotal_exentas" y "monto_total"
- Si encuentras productos con precios, súmalos correctamente y distribúyelos según la tasa de IVA aplicable
- Los montos deben ser números sin separadores de miles ni símbolos
- RUC debe incluir el guión (ej: "80014066-4")
- Fechas en formato YYYY-MM-DD
- NO uses markdown ni ```json en la respuesta
- Si "condicion_venta" no está clara, usa "CONTADO"

🔍 PROCESO DE EXTRACCIÓN PASO A PASO:
1. Busca la tabla de productos/servicios
2. Extrae cantidad, descripción, precio unitario y total de cada producto
3. Identifica si hay columnas de IVA 5% o IVA 10%
4. Si NO hay columnas de IVA, trata los importes como exentos
5. Suma correctamente todos los totales
6. Verifica que monto_total coincida con la suma de subtotales + IVA

IMPORTANTE: Si no encuentras montos específicos de IVA 5% o IVA 10% en la tabla, pero sí hay un total general, colócalo en "subtotal_exentas" y el mismo valor en "monto_total".
"""
    
    # =========================================================================
    # VALIDACIÓN Y CORRECCIÓN CDC
    # =========================================================================
    
    def _validate_and_enhance_with_cdc(self, invoice: InvoiceData) -> InvoiceData:
        """
        Valida fecha extraída contra CDC y corrige inconsistencias.
        
        El CDC paraguayo contiene la fecha real de emisión, por lo que
        es más confiable que la extracción de OpenAI.
        
        Args:
            invoice: Factura procesada por OpenAI
            
        Returns:
            Factura validada y corregida si es necesario
        """
        try:
            logger.info("🔍 VALIDANDO factura contra CDC")
            cdc = getattr(invoice, 'cdc', '')
            
            if not cdc or len(cdc) != 44:
                logger.info("🔍 CDC no disponible o inválido, devolviendo sin validar")
                return invoice
            
            # Extraer fecha del CDC (formato YYYYMMDD)
            fecha_pattern = re.search(r'(20\d{2})(\d{2})(\d{2})', cdc)
            if not fecha_pattern:
                logger.warning(f"🔍 No se pudo extraer fecha del CDC: {cdc}")
                return invoice
                
            year_cdc = fecha_pattern.group(1)
            month_cdc = fecha_pattern.group(2)
            day_cdc = fecha_pattern.group(3)
            fecha_cdc_str = f"{year_cdc}-{month_cdc}-{day_cdc}"
            
            # Comparar fechas
            if invoice.fecha:
                fecha_openai_str = invoice.fecha.strftime("%Y-%m-%d")
                logger.info(f"🔍 Comparando fechas - OpenAI: {fecha_openai_str}, CDC: {fecha_cdc_str}")
                
                if fecha_openai_str != fecha_cdc_str:
                    logger.warning(f"🔍 ¡DISCREPANCIA DETECTADA! Corrigiendo {fecha_openai_str} → {fecha_cdc_str}")
                    invoice.fecha = datetime.strptime(fecha_cdc_str, "%Y-%m-%d").date()
                    logger.info(f"🔍 ✅ Fecha corregida usando CDC")
                else:
                    logger.info(f"🔍 ✅ Fechas coinciden, no hay corrección necesaria")
            else:
                logger.info(f"🔍 Estableciendo fecha desde CDC: {fecha_cdc_str}")
                invoice.fecha = datetime.strptime(fecha_cdc_str, "%Y-%m-%d").date()
            
            return invoice
            
        except Exception as e:
            logger.error(f"❌ Error validando contra CDC: {str(e)}")
            return invoice
    
    # =========================================================================
    # SISTEMA DE FALLBACK - FACTURAS BÁSICAS
    # =========================================================================
    
    def _create_basic_invoice_from_filename(self, pdf_path: str, email_metadata: Dict[str, Any] = None) -> InvoiceData:
        """
        Crea factura básica cuando OpenAI no puede procesar el PDF.
        
        Extrae información disponible del filename y CDC.
        
        Args:
            pdf_path: Ruta al PDF
            email_metadata: Metadatos del email
            
        Returns:
            InvoiceData básica para revisión manual
        """
        try:
            filename = os.path.basename(pdf_path)
            logger.info(f"🔧 Creando factura básica desde: {filename}")
            
            # Extraer CDC del filename (44 dígitos consecutivos)
            cdc_match = re.search(r'(\d{44})', filename)
            cdc = cdc_match.group(1) if cdc_match else ""
            
            # Extraer número de factura (formato XXX-XXX-XXXXXXX)
            factura_match = re.search(r'(\d{3}-\d{3}-\d{7})', filename)
            numero_factura = factura_match.group(1) if factura_match else ""
            
            # Estructura básica para factura no procesable
            basic_data = self._get_basic_invoice_structure(numero_factura, cdc)
            
            invoice = InvoiceData.from_dict(basic_data, email_metadata)
            
            # Agregar path para extracción posterior de nombre real
            setattr(invoice, 'pdf_path', pdf_path)
            
            logger.info(f"🔧 Factura básica creada: {numero_factura} - CDC: {cdc}")
            return invoice
            
        except Exception as e:
            logger.error(f"❌ Error creando factura básica: {str(e)}")
            return None

    def _get_basic_invoice_structure(self, numero_factura: str, cdc: str) -> dict:
        """
        Retorna estructura básica para facturas no procesables.
        
        Args:
            numero_factura: Número extraído del filename
            cdc: CDC extraído del filename
            
        Returns:
            dict: Estructura básica de factura
        """
        return {
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

    def _enhance_basic_invoice_with_cdc(self, invoice: InvoiceData) -> InvoiceData:
        """
        Mejora factura básica extrayendo información del CDC y texto real del PDF.
        
        Mejoras aplicadas:
        1. RUC desde CDC
        2. Fecha desde CDC  
        3. Nombre real desde PDF (NO inventado)
        
        Args:
            invoice: Factura básica a mejorar
            
        Returns:
            Factura mejorada con datos reales
        """
        try:
            logger.info("🔧 INICIANDO mejora de factura básica con CDC")
            cdc = getattr(invoice, 'cdc', '')
            
            # Manejo robusto del CDC - puede ser string, lista, etc.
            if isinstance(cdc, list):
                logger.warning(f"🔧 CDC es lista: {cdc}, tomando primer elemento")
                cdc = str(cdc[0]) if cdc and len(cdc) > 0 else ''
            else:
                cdc = str(cdc) if cdc else ''
            
            # Limpiar CDC de caracteres no numéricos
            cdc = re.sub(r'[^0-9]', '', cdc)
            
            if not cdc or len(cdc) != 44:
                logger.warning(f"🔧 CDC inválido: {cdc} (longitud: {len(cdc)})")
                return invoice
            
            logger.info(f"🔧 CDC válido encontrado: {cdc}")
            
            # Extraer RUC del CDC (primeros 9 caracteres: 8 dígitos + DV)
            ruc_base = cdc[0:8]
            dv = cdc[8]
            ruc_completo = f"{ruc_base}-{dv}"
            
            # Extraer fecha del CDC
            fecha_pattern = re.search(r'(20\d{2})(\d{2})(\d{2})', cdc)
            if fecha_pattern:
                year = fecha_pattern.group(1)
                month = fecha_pattern.group(2) 
                day = fecha_pattern.group(3)
                fecha_cdc = f"{year}-{month}-{day}"
                
                logger.info(f"🔧 Datos extraídos del CDC:")
                logger.info(f"   📄 RUC: {ruc_completo}")
                logger.info(f"   📅 Fecha: {fecha_cdc}")
                
                # Actualizar datos básicos desde CDC
                invoice.ruc_emisor = ruc_completo
                try:
                    invoice.fecha = datetime.strptime(fecha_cdc, "%Y-%m-%d").date()
                except ValueError as e:
                    logger.warning(f"🔧 Error parseando fecha del CDC: {e}")
                    # No actualizar fecha si hay error
                
                # Extraer nombre real del PDF (NO inventar)
                pdf_path = getattr(invoice, 'pdf_path', None)
                nombre_real = self._extract_real_company_name_from_pdf(pdf_path, ruc_completo)
                invoice.nombre_emisor = nombre_real
                
                logger.info(f"🔧 Factura básica mejorada:")
                logger.info(f"   🏢 RUC: {invoice.ruc_emisor}")
                logger.info(f"   🏢 Nombre: {invoice.nombre_emisor}")
                logger.info(f"   📅 Fecha: {fecha_cdc}")
                logger.info("✅ Mejoras aplicadas exitosamente")
                
            else:
                logger.warning("🔧 No se pudo extraer fecha del CDC")
            
            return invoice
            
        except Exception as e:
            logger.error(f"❌ Error mejorando factura básica: {str(e)}")
            import traceback
            logger.error(f"❌ Traceback: {traceback.format_exc()}")
            return invoice

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
                text_content = extract_text(pdf_path)
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
