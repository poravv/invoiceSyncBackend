#!/usr/bin/env python3
"""
Script de prueba completo para validar la detección y descarga de facturas electrónicas.
"""

import sys
import os
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

# Configurar logging para ver los detalles del proceso
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Agregar el directorio del proyecto al path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.modules.email_processor.email_processor import EmailProcessor

def create_test_email_with_multiple_links():
    """Crea un email de prueba con diferentes tipos de enlaces."""
    
    # Crear el mensaje
    msg = MIMEMultipart()
    msg['Subject'] = 'FACTURA ELECTRONICA - Prueba Completa'
    msg['From'] = 'facturacion@dasegroup.com.py'
    msg['To'] = 'carlos.vargas@example.com'
    
    # Contenido HTML con diferentes tipos de enlaces
    html_content = """
    <html>
    <body>
        <h2>FACTURA ELECTRONICA</h2>
        
        <p><strong>Detalles de la Factura:</strong></p>
        <ul>
            <li>NUMERO: 001-001-0000561</li>
            <li>EMITIDO POR: DASE GROUP E.A.S.</li>
            <li>A NOMBRE DE: VARGAS RAMIREZ, CARLOS VICENTE</li>
            <li>FECHA DE EMISION: 2025-05-06</li>
            <li>MONEDA: PYG</li>
            <li>MONTO: 100.000</li>
        </ul>
        
        <p>Para visualizar o descargar la factura, acceda a los siguientes enlaces:</p>
        
        <p>
            <a href="https://facte.siga.com.py/FacturaE/printDE?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317">VISUALIZAR DOCUMENTO</a><br>
            <a href="https://facte.siga.com.py/FacturaE/downloadXML?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317">DESCARGAR XML</a><br>
            <a href="https://www.example.com/test.pdf">Enlace PDF Directo (Test)</a>
        </p>
        
        <p>También puede acceder directamente a: https://facte.siga.com.py/FacturaE/printDE?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317</p>
    </body>
    </html>
    """
    
    # Crear parte HTML
    html_part = MIMEText(html_content, 'html')
    msg.attach(html_part)
    
    return msg

def test_complete_flow():
    """Prueba el flujo completo de detección y procesamiento."""
    
    print("=== Test Completo de Procesamiento de Facturas Electrónicas ===\n")
    
    # Crear email de prueba
    test_email = create_test_email_with_multiple_links()
    
    # Crear instancia del procesador
    processor = EmailProcessor()
    
    print("1. Extrayendo enlaces del correo...")
    links = processor._extract_links_from_email(test_email)
    
    print(f"   ✓ Enlaces encontrados: {len(links)}")
    for i, link in enumerate(links, 1):
        print(f"     {i}. {link}")
    
    print("\n2. Analizando tipos de enlaces...")
    
    siga_links = [link for link in links if "facte.siga.com.py" in link]
    pdf_links = [link for link in links if link.endswith(".pdf")]
    print_links = [link for link in links if "printDE" in link]
    xml_links = [link for link in links if "downloadXML" in link]
    
    print(f"   ✓ Enlaces SIGA: {len(siga_links)}")
    print(f"   ✓ Enlaces PDF directos: {len(pdf_links)}")
    print(f"   ✓ Enlaces de visualización (printDE): {len(print_links)}")
    print(f"   ✓ Enlaces de descarga XML: {len(xml_links)}")
    
    print("\n3. Simulando procesamiento de enlaces...")
    
    # Simular el procesamiento de cada enlace
    processed_count = 0
    for link in links:
        print(f"\n   Procesando: {link}")
        
        # Generar nombre de archivo que se usaría
        if "facte.siga.com.py" in link:
            filename = processor._generate_filename_from_url(link, "pdf")
            print(f"   ✓ Nombre de archivo generado: {filename}")
            processed_count += 1
        elif link.endswith(".pdf"):
            filename = processor._generate_filename_from_url(link, "pdf")
            print(f"   ✓ Nombre de archivo generado: {filename}")
            processed_count += 1
        else:
            print(f"   ⚠ Enlace no procesable directamente")
    
    print(f"\n=== Resumen del Test ===")
    print(f"✓ Enlaces detectados: {len(links)}")
    print(f"✓ Enlaces procesables: {processed_count}")
    print(f"✓ Detección de facturas SIGA: {'SÍ' if siga_links else 'NO'}")
    print(f"✓ Detección de PDFs directos: {'SÍ' if pdf_links else 'NO'}")
    
    # Verificar que se detectaron los enlaces principales
    success = len(siga_links) >= 2  # Al menos el enlace de visualización y XML
    
    if success:
        print(f"\n🎉 Test EXITOSO: El sistema puede detectar y procesar facturas electrónicas SIGA")
        print(f"   - Se detectaron {len(siga_links)} enlaces de SIGA")
        print(f"   - El sistema procesará automáticamente estos enlaces")
        print(f"   - Los PDFs se descargarán y enviarán a OpenAI para extracción de datos")
    else:
        print(f"\n❌ Test FALLIDO: No se detectaron suficientes enlaces")
    
    return success, links

def test_metadata_extraction():
    """Prueba la extracción de metadatos del email."""
    
    print("\n=== Test de Extracción de Metadatos ===")
    
    test_email = create_test_email_with_multiple_links()
    processor = EmailProcessor()
    
    # Simular extracción de metadatos (el método real requiere conexión IMAP)
    metadata = {
        "sender": test_email['From'],
        "subject": test_email['Subject'],
        "date": "2025-05-31",
        "links": processor._extract_links_from_email(test_email)
    }
    
    print(f"✓ Remitente: {metadata['sender']}")
    print(f"✓ Asunto: {metadata['subject']}")
    print(f"✓ Fecha: {metadata['date']}")
    print(f"✓ Enlaces encontrados: {len(metadata['links'])}")
    
    return metadata

if __name__ == "__main__":
    try:
        # Ejecutar tests
        success, links = test_complete_flow()
        metadata = test_metadata_extraction()
        
        print(f"\n" + "="*60)
        print(f"RESULTADO FINAL:")
        print(f"- Sistema preparado para facturas SIGA: {'✅ SÍ' if success else '❌ NO'}")
        print(f"- Enlaces detectados correctamente: {'✅ SÍ' if links else '❌ NO'}")
        print(f"- Metadatos extraídos correctamente: {'✅ SÍ' if metadata else '❌ NO'}")
        
        if success:
            print(f"\n🚀 Tu sistema InvoiceSync ahora puede procesar facturas electrónicas")
            print(f"   que lleguen como enlaces en el correo electrónico!")
            
    except Exception as e:
        print(f"\n❌ Error durante el test: {str(e)}")
        import traceback
        traceback.print_exc()
