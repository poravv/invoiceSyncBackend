import os
import sys
import logging

# Configurar el logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Añadir la ruta del directorio padre al path para poder importar desde app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.openai_processor.openai_processor import OpenAIProcessor

def test_extract_invoice():
    """
    Prueba la extracción de datos de una factura utilizando OpenAI.
    """
    # Ruta a un PDF de prueba (asegúrate de que existe)
    test_pdf_path = os.path.join('data', 'pdfs', 'test_factura.pdf')
    
    # Si no existe el archivo de prueba, mostrar un mensaje
    if not os.path.exists(test_pdf_path):
        logger.warning(f"El archivo de prueba {test_pdf_path} no existe. Esta prueba no procesará ningún PDF.")
        test_pdf_path = None
    
    # Crear una instancia del procesador
    processor = OpenAIProcessor()
    
    # Si tenemos un PDF de prueba, procesarlo
    if test_pdf_path:
        try:
            # Extraer datos
            logger.info(f"Procesando PDF de prueba: {test_pdf_path}")
            invoice_data = processor.extract_invoice_data(test_pdf_path)
            
            # Mostrar resultados
            logger.info(f"Datos extraídos: {invoice_data}")
            
            # Verificar campos obligatorios
            fields_to_check = [
                'fecha', 'ruc_emisor', 'nombre_emisor', 'numero_factura',
                'monto_total', 'iva', 'timbrado', 'cdc'
            ]
            
            missing_fields = [field for field in fields_to_check if not getattr(invoice_data, field)]
            
            if missing_fields:
                logger.warning(f"Campos faltantes: {missing_fields}")
            else:
                logger.info("Todos los campos obligatorios están presentes")
                
            # Verificar datos estructurados
            if invoice_data.empresa:
                logger.info(f"Datos de empresa extraídos: {invoice_data.empresa}")
            else:
                logger.warning("No se extrajeron datos de empresa")
                
            if invoice_data.productos:
                logger.info(f"Se extrajeron {len(invoice_data.productos)} productos")
            else:
                logger.warning("No se extrajeron productos")
                
            return True
            
        except Exception as e:
            logger.error(f"Error al probar la extracción: {str(e)}")
            return False
    else:
        logger.info("No se ejecutó la prueba porque no hay un PDF de prueba disponible")
        return None

if __name__ == "__main__":
    test_extract_invoice()
