#!/usr/bin/env python3
"""
Script de prueba para verificar el procesamiento de XML mejorado
"""

import os
import sys
import logging

# Configurar logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Agregar el directorio del proyecto al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.modules.openai_processor.processor import OpenAIProcessor
from app.config.settings import settings

def test_xml_processing():
    """Prueba el procesamiento de XML con el procesador mejorado"""
    
    # Verificar que tenemos la API key
    if not settings.OPENAI_API_KEY:
        print("❌ Error: OPENAI_API_KEY no configurada")
        return False
    
    print("🔑 API Key configurada correctamente")
    
    # Crear el procesador
    processor = OpenAIProcessor()
    print("✅ Procesador OpenAI creado")
    
    # Buscar archivos XML para probar
    xml_dir = "/app/data/temp_pdfs"  # Ruta en Docker
    if not os.path.exists(xml_dir):
        xml_dir = "data/temp_pdfs"  # Ruta local
    
    if not os.path.exists(xml_dir):
        print(f"❌ Error: Directorio de XML no encontrado: {xml_dir}")
        return False
    
    # Encontrar el primer archivo XML
    xml_files = [f for f in os.listdir(xml_dir) if f.endswith('.xml')]
    
    if not xml_files:
        print("❌ Error: No se encontraron archivos XML para probar")
        return False
    
    test_xml = os.path.join(xml_dir, xml_files[0])
    print(f"📄 Probando con archivo: {test_xml}")
    
    try:
        # Procesar el XML
        result = processor.extract_invoice_data_from_xml(test_xml)
        
        if result:
            print("✅ XML procesado exitosamente!")
            print(f"📊 Resultado: {result}")
            return True
        else:
            print("❌ XML no pudo ser procesado")
            return False
            
    except Exception as e:
        print(f"❌ Error procesando XML: {e}")
        return False

if __name__ == "__main__":
    print("🧪 Iniciando prueba de procesamiento de XML...")
    success = test_xml_processing()
    
    if success:
        print("🎉 Prueba completada exitosamente!")
    else:
        print("💥 Prueba falló")
        sys.exit(1)
