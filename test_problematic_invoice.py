#!/usr/bin/env python3
"""
Test específico para la factura problemática 004-013-0001823
"""

import os
import sys
import base64

# Agregar el directorio padre al path para importar los módulos
sys.path.append('/app')

from app.modules.openai_processor.openai_processor import OpenAIProcessor

def test_problematic_invoice():
    """Procesa específicamente la factura problemática"""
    
    print("=== Test de Factura Problemática ===")
    
    # Buscar el PDF específico
    pdf_path = "/app/data/temp_pdfs/20250815011357888_6f135487_07800112458004013000182322025070319401680728.PDF"
    
    if not os.path.exists(pdf_path):
        print(f"❌ ERROR: PDF no encontrado: {pdf_path}")
        # Buscar archivos similares
        import glob
        similar_files = glob.glob("/app/data/temp_pdfs/*07800112458004013000182322025070319401680728*")
        if similar_files:
            print("📁 Archivos similares encontrados:")
            for f in similar_files:
                print(f"   - {f}")
                pdf_path = f  # Usar el primero encontrado
        else:
            print("❌ No se encontraron archivos similares")
            return
    
    print(f"📄 Procesando: {os.path.basename(pdf_path)}")
    
    # Verificar si se puede convertir a imagen
    print("🖼️ Probando conversión a imagen...")
    openai_processor = OpenAIProcessor()
    
    try:
        image_data = openai_processor._convert_pdf_to_image(pdf_path)
        print(f"✅ Conversión exitosa: {len(image_data)} caracteres base64")
        
        # Guardar imagen para inspección
        image_bytes = base64.b64decode(image_data)
        debug_image_path = "/app/data/temp_pdfs/debug_image.jpg"
        with open(debug_image_path, "wb") as f:
            f.write(image_bytes)
        print(f"🖼️ Imagen guardada para inspección: {debug_image_path}")
        
    except Exception as e:
        print(f"❌ Error en conversión a imagen: {e}")
        return
    
    try:
        # Procesar el PDF
        print("🔄 Iniciando procesamiento con OpenAI...")
        resultado = openai_processor.extract_invoice_data(pdf_path)
        
        if resultado:
            print("✅ PROCESAMIENTO EXITOSO")
            print(f"   📄 Número: {resultado.numero_factura}")
            print(f"   🏢 Emisor: {resultado.nombre_emisor}")
            print(f"   💰 Total: {resultado.monto_total}")
            print(f"   📊 Exentas: {resultado.subtotal_exentas}")
            print(f"   📊 Sub5%: {resultado.subtotal_5}")
            print(f"   📊 Sub10%: {resultado.subtotal_10}")
        else:
            print("❌ PROCESAMIENTO FALLÓ - resultado es None")
            
    except Exception as e:
        print(f"❌ ERROR DURANTE PROCESAMIENTO: {str(e)}")
        import traceback
        print(f"📜 Traceback completo:")
        traceback.print_exc()

if __name__ == "__main__":
    test_problematic_invoice()
