import os
import logging
import time
from typing import List, Dict, Any, Optional
import argparse
from datetime import datetime

from app.config.settings import settings
from app.models.models import InvoiceData, ProcessResult, EmailConfig, JobStatus
from app.modules.email_processor.email_processor import EmailProcessor
from app.modules.openai_processor.openai_processor import OpenAIProcessor
from app.modules.excel_exporter.excel_exporter import ExcelExporter

# Configurar logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("invoicesync.log")
    ]
)

logger = logging.getLogger(__name__)

class InvoiceSync:
    def __init__(self):
        """Inicializa el sistema de sincronización de facturas usando OpenAI."""
        # Crear directorios necesarios
        os.makedirs(settings.TEMP_PDF_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(settings.EXCEL_OUTPUT_PATH), exist_ok=True)
        
        # Inicializar componentes
        self.email_processor = EmailProcessor()
        self.openai_processor = OpenAIProcessor()
        self.excel_exporter = ExcelExporter()
        
        # Estado del job
        self._job_status = JobStatus(
            running=False,
            interval_minutes=settings.JOB_INTERVAL_MINUTES,
            next_run=None,
            last_run=None,
            last_result=None
        )
        
        logger.info("Sistema InvoiceSync inicializado correctamente")
    
    def process_emails(self) -> ProcessResult:
        """
        Procesa correos electrónicos para extraer facturas.
        
        Returns:
            ProcessResult: Resultado del procesamiento.
        """
        logger.info("Iniciando procesamiento de correos")
        
        # Registrar inicio del procesamiento
        self._job_status.last_run = datetime.now().isoformat()
        
        # Procesar correos
        result = self.email_processor.process_emails()
        
        # Actualizar estado del job
        self._job_status.last_result = result
        
        return result

    def process_pdf(self, pdf_path: str, metadata: Dict[str, Any] = None) -> InvoiceData:
        """
        Procesa un archivo PDF para extraer datos de factura.
        
        Args:
            pdf_path: Ruta al archivo PDF.
            metadata: Metadatos adicionales.
            
        Returns:
            InvoiceData: Datos extraídos de la factura.
        """
        logger.info(f"Procesando PDF: {pdf_path}")
        return self.openai_processor.extract_invoice_data(pdf_path, metadata)
    
    def start_scheduled_job(self) -> JobStatus:
        """
        Inicia el trabajo programado para procesar correos periódicamente.
        
        Returns:
            JobStatus: Estado actual del trabajo.
        """
        if not self._job_status.running:
            self.email_processor.start_scheduled_job()
            self._job_status.running = True
            self._job_status.next_run = self._calculate_next_run()
            logger.info(f"Job programado iniciado. Próxima ejecución: {self._job_status.next_run}")
        
        return self._job_status
    
    def stop_scheduled_job(self) -> JobStatus:
        """
        Detiene el trabajo programado.
        
        Returns:
            JobStatus: Estado actual del trabajo.
        """
        if self._job_status.running:
            self.email_processor.stop_scheduled_job()
            self._job_status.running = False
            self._job_status.next_run = None
            logger.info("Job programado detenido")
        
        return self._job_status
    
    def get_job_status(self) -> JobStatus:
        """
        Obtiene el estado actual del trabajo programado.
        
        Returns:
            JobStatus: Estado actual del trabajo.
        """
        # Actualizar el tiempo de la próxima ejecución si el job está corriendo
        if self._job_status.running:
            self._job_status.next_run = self._calculate_next_run()
        
        return self._job_status
    
    def _calculate_next_run(self) -> str:
        """
        Calcula el tiempo de la próxima ejecución del job.
        
        Returns:
            str: Tiempo de la próxima ejecución en formato ISO.
        """
        # Esta es una estimación simple. El schedule.py podría tener un tiempo ligeramente diferente
        now = datetime.now()
        next_run = now.replace(second=0, microsecond=0)
        
        # Añadir los minutos del intervalo
        from datetime import timedelta
        next_run += timedelta(minutes=settings.JOB_INTERVAL_MINUTES)
        
        return next_run.isoformat()

def main():
    """Función principal para ejecutar desde línea de comandos."""
    parser = argparse.ArgumentParser(description="InvoiceSync: Sincronización de facturas desde correo")
    parser.add_argument("--process", action="store_true", help="Procesar correos")
    parser.add_argument("--start-job", action="store_true", help="Iniciar job programado")
    parser.add_argument("--stop-job", action="store_true", help="Detener job programado")
    parser.add_argument("--status", action="store_true", help="Mostrar estado")
    
    args = parser.parse_args()
    
    invoicesync = InvoiceSync()
    
    if args.process:
        result = invoicesync.process_emails()
        print(f"Resultado: {result.success}")
        print(f"Mensaje: {result.message}")
        print(f"Facturas procesadas: {result.invoice_count}")
    
    elif args.start_job:
        status = invoicesync.start_scheduled_job()
        print(f"Job iniciado: {status.running}")
        print(f"Próxima ejecución: {status.next_run}")
        
        # Mantener proceso vivo
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Deteniendo job...")
            invoicesync.stop_scheduled_job()
    
    elif args.stop_job:
        status = invoicesync.stop_scheduled_job()
        print(f"Job detenido: {not status.running}")
    
    elif args.status:
        status = invoicesync.get_job_status()
        print(f"Job activo: {status.running}")
        print(f"Próxima ejecución: {status.next_run}")
        print(f"Última ejecución: {status.last_run}")
        if status.last_result:
            print(f"Último resultado: {status.last_result.message}")
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
