#!/usr/bin/env python3
"""
Script simple para ver logs detallados del procesamiento
"""
import sys
import os
import json

# Agregar el directorio de la aplicación al path
sys.path.append('/app')

from app.modules.openai_processor.openai_processor import OpenAIProcessor
from app.models.models import InvoiceData

def test_specific_pdf():
    """Procesa un PDF específico con logging detallado"""
    
    # PDF que parece tener problemas según los logs
    pdf_path = "/app/data/temp_pdfs/20250815001707920_ed0c74bb_01800092430001001045746222025081119198923040.pdf"
    
    print(f"🔍 ANALIZANDO: {os.path.basename(pdf_path)}")
    
    processor = OpenAIProcessor()
    
    try:
        email_metadata = {
            "sender": "test@example.com",
            "subject": "Test análisis específico"
        }
        
        # Habilitar logging más detallado
        import logging
        logging.basicConfig(level=logging.DEBUG)
        
        invoice = processor.extract_invoice_data(pdf_path, email_metadata)
        
        if invoice:
            print(f"\n✅ PROCESAMIENTO EXITOSO")
            print(f"📊 DATOS EXTRAÍDOS:")
            print(f"   📄 Número: {invoice.numero_factura}")
            print(f"   🏢 RUC: {invoice.ruc_emisor}")
            print(f"   💰 Total: {invoice.monto_total} (tipo: {type(invoice.monto_total)})")
            print(f"   📊 Sub10%: {invoice.subtotal_10} (tipo: {type(invoice.subtotal_10)})")
            print(f"   📊 IVA10%: {invoice.iva_10} (tipo: {type(invoice.iva_10)})")
            print(f"   📊 Sub5%: {invoice.subtotal_5} (tipo: {type(invoice.subtotal_5)})")
            print(f"   📊 IVA5%: {invoice.iva_5} (tipo: {type(invoice.iva_5)})")
            print(f"   📊 Exentas: {invoice.subtotal_exentas} (tipo: {type(invoice.subtotal_exentas)})")
            
            # Verificar cálculos
            print(f"\n🧮 VERIFICACIÓN DE CÁLCULOS:")
            try:
                total_calculado = invoice.subtotal_10 + invoice.iva_10 + invoice.subtotal_5 + invoice.iva_5 + invoice.subtotal_exentas
                print(f"   Suma componentes: {total_calculado:,.0f}")
                print(f"   Total reportado: {invoice.monto_total:,.0f}")
                print(f"   Diferencia: {abs(total_calculado - invoice.monto_total):,.0f}")
                
                if abs(total_calculado - invoice.monto_total) > 1:
                    print(f"   ⚠️  HAY DISCREPANCIA EN LOS TOTALES")
                else:
                    print(f"   ✅ TOTALES COINCIDEN")
                    
            except Exception as calc_error:
                print(f"   💥 ERROR EN CÁLCULO: {calc_error}")
                
        else:
            print(f"❌ NO SE PUDO PROCESAR EL PDF")
            
    except Exception as e:
        print(f"💥 ERROR GENERAL: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_specific_pdf()
