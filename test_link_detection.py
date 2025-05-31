#!/usr/bin/env python3
"""
Script de prueba para validar la detección de enlaces de facturas electrónicas.
"""

import sys
import os
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Agregar el directorio del proyecto al path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.modules.email_processor.email_processor import EmailProcessor

def create_test_email_with_siga_link():
    """Crea un email de prueba con un enlace de factura SIGA."""
    
    # Crear el mensaje
    msg = MIMEMultipart()
    msg['Subject'] = 'FACTURA ELECTRONICA - Prueba'
    msg['From'] = 'test@example.com'
    msg['To'] = 'recipient@example.com'
    
    # Contenido HTML similar al ejemplo del usuario
    html_content = """
    <html>
    <body>
        <p>EN EL PRESENTE CORREO SE ADJUNTA EL SIGUIENTE DOCUMENTO</p>
        
        <p><strong>FACTURA ELECTRONICA</strong><br>
        NUMERO: 001-001-0000561<br>
        EMITIDO POR: DASE GROUP E.A.S.<br>
        A NOMBRE DE: VARGAS RAMIREZ, CARLOS VICENTE<br>
        FECHA DE EMISION: 2025-05-06<br>
        MONEDA: PYG<br>
        MONTO: 100.000</p>
        
        <p>Para visualizar o descargar acceder al siguiente link</p>
        
        <p><a href="https://facte.siga.com.py/FacturaE/printDE?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317">VISUALIZAR DOCUMENTO</a><br>
        <a href="https://facte.siga.com.py/FacturaE/downloadXML?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317">DESCARGAR XML</a></p>
    </body>
    </html>
    """
    
    # Crear parte HTML
    html_part = MIMEText(html_content, 'html')
    msg.attach(html_part)
    
    return msg

def test_link_extraction():
    """Prueba la extracción de enlaces."""
    
    print("=== Test de Detección de Enlaces de Facturas Electrónicas ===\n")
    
    # Crear email de prueba
    test_email = create_test_email_with_siga_link()
    
    # Crear instancia del procesador
    processor = EmailProcessor()
    
    # Extraer enlaces del email
    links = processor._extract_links_from_email(test_email)
    
    print(f"Enlaces encontrados: {len(links)}")
    for i, link in enumerate(links, 1):
        print(f"  {i}. {link}")
    
    # Verificar que se encontraron los enlaces esperados
    expected_links = [
        "https://facte.siga.com.py/FacturaE/printDE?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317",
        "https://facte.siga.com.py/FacturaE/downloadXML?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317"
    ]
    
    print(f"\nEnlaces esperados: {len(expected_links)}")
    for i, link in enumerate(expected_links, 1):
        print(f"  {i}. {link}")
    
    # Verificar resultados
    found_printDE = any("printDE" in link for link in links)
    found_downloadXML = any("downloadXML" in link for link in links)
    
    print(f"\n=== Resultados ===")
    print(f"✓ Enlace de visualización encontrado: {'SÍ' if found_printDE else 'NO'}")
    print(f"✓ Enlace de descarga XML encontrado: {'SÍ' if found_downloadXML else 'NO'}")
    
    if found_printDE:
        print(f"\n=== Test de Descarga ===")
        printDE_link = next(link for link in links if "printDE" in link)
        print(f"Intentando acceder a: {printDE_link}")
        
        # Simular descarga (sin ejecutar realmente para evitar hacer requests)
        filename = processor._generate_filename_from_url(printDE_link, "pdf")
        print(f"Nombre de archivo generado: {filename}")
    
    return links

if __name__ == "__main__":
    try:
        links = test_link_extraction()
        
        if links:
            print(f"\n✅ Test exitoso: Se detectaron {len(links)} enlaces")
        else:
            print(f"\n❌ Test fallido: No se detectaron enlaces")
            
    except Exception as e:
        print(f"\n❌ Error durante el test: {str(e)}")
        import traceback
        traceback.print_exc()
