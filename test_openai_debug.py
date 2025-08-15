#!/usr/bin/env python3
"""
Script para capturar la respuesta cruda de OpenAI y verificar cálculos
"""
import sys
import os
import json
import re
from datetime import datetime

# Agregar el directorio de la aplicación al path
sys.path.append('/app')

from app.modules.openai_processor.openai_processor import OpenAIProcessor
from app.config.settings import settings

def test_openai_raw_response():
    """Analiza la respuesta cruda de OpenAI para verificar datos"""
    
    # Override del método para capturar respuesta cruda
    class DebugOpenAIProcessor(OpenAIProcessor):
        def extract_invoice_data(self, pdf_path: str, email_metadata = None):
            """Override para capturar respuesta cruda"""
            import base64
            import openai
            
            openai.api_key = settings.OPENAI_API_KEY
            
            # Convertir PDF a imagen
            images = self._pdf_to_images(pdf_path)
            if not images:
                print("❌ No se pudieron convertir PDFs a imágenes")
                return None
            
            # Usar solo la primera página
            image = images[0]
            
            # Convertir imagen a base64
            import io
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            image_base64 = base64.b64encode(buffer.getvalue()).decode()
            
            # Crear prompt
            prompt = self._build_prompt()
            
            try:
                response = openai.ChatCompletion.create(
                    model="gpt-4-vision-preview",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_base64}"}
                            }
                        ]
                    }],
                    max_tokens=2000,
                    temperature=0.1
                )
                
                raw_output = response.choices[0].message.content
                
                print(f"\n🔍 RESPUESTA CRUDA DE OPENAI para {os.path.basename(pdf_path)}:")
                print("="*100)
                print(raw_output)
                print("="*100)
                
                # Parsear JSON
                from app.modules.openai_processor.openai_processor import extract_clean_json
                json_data = extract_clean_json(raw_output)
                
                print(f"\n📊 JSON PARSEADO:")
                print(json.dumps(json_data, indent=2, ensure_ascii=False))
                
                # Verificar tipos de datos
                problematic_fields = []
                for field in ['monto_total', 'subtotal_10', 'iva_10', 'subtotal_5', 'iva_5', 'subtotal_exentas']:
                    value = json_data.get(field)
                    if isinstance(value, list):
                        problematic_fields.append(f"{field}: {value}")
                
                if problematic_fields:
                    print(f"\n⚠️  CAMPOS PROBLEMÁTICOS (listas):")
                    for field in problematic_fields:
                        print(f"   - {field}")
                else:
                    print(f"\n✅ TODOS LOS CAMPOS NUMÉRICOS SON CORRECTOS")
                
                # Verificar cálculos
                print(f"\n🧮 VERIFICACIÓN DE CÁLCULOS:")
                subtotal_10 = float(json_data.get('subtotal_10', 0))
                iva_10 = float(json_data.get('iva_10', 0))
                subtotal_5 = float(json_data.get('subtotal_5', 0))
                iva_5 = float(json_data.get('iva_5', 0))
                subtotal_exentas = float(json_data.get('subtotal_exentas', 0))
                monto_total = float(json_data.get('monto_total', 0))
                
                # Cálculo esperado
                iva_10_calculado = subtotal_10 * 0.10
                iva_5_calculado = subtotal_5 * 0.05
                total_calculado = subtotal_10 + iva_10 + subtotal_5 + iva_5 + subtotal_exentas
                
                print(f"   Subtotal 10%: {subtotal_10:,.0f}")
                print(f"   IVA 10% (reportado): {iva_10:,.0f}")
                print(f"   IVA 10% (calculado): {iva_10_calculado:,.0f}")
                print(f"   Diferencia IVA 10%: {abs(iva_10 - iva_10_calculado):,.0f}")
                
                print(f"   Subtotal 5%: {subtotal_5:,.0f}")
                print(f"   IVA 5% (reportado): {iva_5:,.0f}")
                print(f"   IVA 5% (calculado): {iva_5_calculado:,.0f}")
                print(f"   Diferencia IVA 5%: {abs(iva_5 - iva_5_calculado):,.0f}")
                
                print(f"   Exentas: {subtotal_exentas:,.0f}")
                print(f"   Total (reportado): {monto_total:,.0f}")
                print(f"   Total (calculado): {total_calculado:,.0f}")
                print(f"   Diferencia Total: {abs(monto_total - total_calculado):,.0f}")
                
                return json_data
                
            except Exception as e:
                print(f"💥 ERROR: {str(e)}")
                import traceback
                traceback.print_exc()
                return None
    
    # Probar con un PDF específico
    pdf_path = "/app/data/temp_pdfs/20250815001707920_ed0c74bb_01800092430001001045746222025081119198923040.pdf"
    
    processor = DebugOpenAIProcessor()
    result = processor.extract_invoice_data(pdf_path)

if __name__ == "__main__":
    test_openai_raw_response()
