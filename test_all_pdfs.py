#!/usr/bin/env python3
"""
Test de procesamiento completo de todos los PDFs locales
"""

import os
import sys
import glob
from pathlib import Path

# Agregar el directorio padre al path para importar los módulos
sys.path.append('/app')

from app.modules.openai_processor.openai_processor import OpenAIProcessor

def test_all_pdfs():
    """Procesa todos los PDFs locales"""
    
    print("=== Test de Procesamiento de Todos los PDFs ===")
    
    # Directorio donde están los PDFs
    pdf_dir = "/app/data/temp_pdfs"
    
    # Verificar si el directorio existe
    if not os.path.exists(pdf_dir):
        print(f"❌ ERROR: Directorio {pdf_dir} no existe")
        return
    
    # Buscar todos los PDFs
    pdf_files = glob.glob(os.path.join(pdf_dir, "*.pdf"))
    
    if not pdf_files:
        print(f"❌ ERROR: No se encontraron PDFs en {pdf_dir}")
        return
    
    print(f"📄 Encontrados {len(pdf_files)} archivos PDF:")
    for i, pdf_file in enumerate(pdf_files, 1):
        filename = os.path.basename(pdf_file)
        print(f"   {i}. {filename}")
    
    print("\n" + "="*60)
    
    # Inicializar OpenAI Processor
    openai_processor = OpenAIProcessor()
    
    # Procesar cada PDF
    resultados = []
    
    for i, pdf_file in enumerate(pdf_files, 1):
        filename = os.path.basename(pdf_file)
        print(f"\n🔄 Procesando {i}/{len(pdf_files)}: {filename}")
        print("-" * 50)
        
        try:
            # Procesar el PDF
            resultado = openai_processor.extract_invoice_data(pdf_file)
            
            if resultado:
                print(f"✅ ÉXITO: {filename}")
                print(f"   📄 Número: {resultado.numero_factura or 'N/A'}")
                print(f"   🏢 Emisor: {resultado.nombre_emisor or 'N/A'}")
                print(f"   💰 Total: {resultado.monto_total or 'N/A'}")
                print(f"   📊 Exentas: {resultado.subtotal_exentas or 'N/A'}")
                print(f"   📊 Sub5%: {resultado.subtotal_5 or 'N/A'}")
                print(f"   📊 Sub10%: {resultado.subtotal_10 or 'N/A'}")
                
                # Verificar cálculo matemático
                try:
                    exentas = float(resultado.subtotal_exentas or 0)
                    sub5 = float(resultado.subtotal_5 or 0)
                    iva5 = float(resultado.iva_5 or 0)
                    sub10 = float(resultado.subtotal_10 or 0)
                    iva10 = float(resultado.iva_10 or 0)
                    total = float(resultado.monto_total or 0)
                    
                    suma_calculada = exentas + sub5 + iva5 + sub10 + iva10
                    diferencia = abs(suma_calculada - total)
                    
                    print(f"   🧮 Suma: {suma_calculada:,.0f} | Total: {total:,.0f} | Diff: {diferencia:,.0f}")
                    
                    if diferencia <= 50000:  # Tolerancia de 50,000 Gs
                        print("   ✅ Cálculo OK")
                    else:
                        print("   ⚠️  Revisar cálculos")
                        
                except Exception as e:
                    print(f"   ❌ Error en cálculo: {e}")
                
                resultados.append({
                    'archivo': filename,
                    'exito': True,
                    'datos': resultado
                })
            else:
                print(f"❌ FALLO: {filename} - No se pudo procesar")
                resultados.append({
                    'archivo': filename,
                    'exito': False,
                    'error': 'No se pudo procesar'
                })
                
        except Exception as e:
            print(f"❌ ERROR: {filename} - {str(e)}")
            resultados.append({
                'archivo': filename,
                'exito': False,
                'error': str(e)
            })
    
    # Resumen final
    print("\n" + "="*60)
    print("📊 RESUMEN FINAL:")
    
    exitosos = sum(1 for r in resultados if r['exito'])
    fallidos = len(resultados) - exitosos
    
    print(f"   ✅ Exitosos: {exitosos}/{len(resultados)}")
    print(f"   ❌ Fallidos: {fallidos}/{len(resultados)}")
    print(f"   📈 Tasa de éxito: {(exitosos/len(resultados)*100):.1f}%")
    
    if fallidos > 0:
        print("\n❌ Archivos con errores:")
        for resultado in resultados:
            if not resultado['exito']:
                print(f"   - {resultado['archivo']}: {resultado.get('error', 'Error desconocido')}")
    
    print("\n🎉 Test completado")
    return resultados

if __name__ == "__main__":
    test_all_pdfs()
