#!/usr/bin/env python3
"""
Script para analizar específicamente los PDFs en temp_pdfs y ver qué devuelve OpenAI
"""
import sys
import os
import json
from datetime import datetime

# Agregar el directorio de la aplicación al path
sys.path.append('/app')

from app.modules.openai_processor.openai_processor import OpenAIProcessor
from app.models.models import InvoiceData

def test_pdf_processing():
    """Procesa algunos PDFs específicos para análisis"""
    
    # Directorio de PDFs
    pdf_dir = "/app/data/temp_pdfs"
    
    # Lista de PDFs a analizar
    test_pdfs = [
        "20250815001700842_8778745b_Factura_electrónica_003-001-0516895.pdf",
        "20250815001707920_ed0c74bb_01800092430001001045746222025081119198923040.pdf", 
        "20250815001724189_fd028e1d_01800092430001001041121322025050917848239468.pdf"
    ]
    
    processor = OpenAIProcessor()
    
    for pdf_name in test_pdfs:
        pdf_path = os.path.join(pdf_dir, pdf_name)
        if not os.path.exists(pdf_path):
            print(f"❌ PDF no encontrado: {pdf_path}")
            continue
            
        print(f"\n{'='*80}")
        print(f"🔍 ANALIZANDO: {pdf_name}")
        print(f"{'='*80}")
        
        try:
            # Procesar con OpenAI
            email_metadata = {
                "sender": "test@example.com",
                "subject": "Test análisis PDF"
            }
            
            invoice = processor.extract_invoice_data(pdf_path, email_metadata)
            
            if invoice:
                print(f"✅ PROCESAMIENTO EXITOSO")
                print(f"📄 Número de factura: {invoice.numero_factura}")
                print(f"🏢 RUC Emisor: {invoice.ruc_emisor}")
                print(f"💰 Monto total: {invoice.monto_total}")
                print(f"📊 Subtotal 10%: {invoice.subtotal_10}")
                print(f"📊 IVA 10%: {invoice.iva_10}")
                print(f"📊 Subtotal 5%: {invoice.subtotal_5}")
                print(f"📊 IVA 5%: {invoice.iva_5}")
                print(f"📊 Exentas: {invoice.subtotal_exentas}")
                print(f"🔄 Condición: {invoice.condicion_venta}")
                
                # Validaciones
                if isinstance(invoice.monto_total, list):
                    print(f"⚠️  PROBLEMA: monto_total es una lista: {invoice.monto_total}")
                if isinstance(invoice.subtotal_10, list):
                    print(f"⚠️  PROBLEMA: subtotal_10 es una lista: {invoice.subtotal_10}")
                if isinstance(invoice.iva_10, list):
                    print(f"⚠️  PROBLEMA: iva_10 es una lista: {invoice.iva_10}")
                    
            else:
                print(f"❌ ERROR: No se pudo procesar el PDF")
                
        except Exception as e:
            print(f"💥 ERROR DURANTE PROCESAMIENTO: {str(e)}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    test_pdf_processing()
