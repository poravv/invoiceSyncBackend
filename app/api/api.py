from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import logging
import uvicorn
from typing import List, Optional
import shutil
from datetime import datetime

from config.settings import settings
from models.models import InvoiceData, EmailConfig, ProcessResult, JobStatus
from main import InvoiceSync

# Configurar logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("invoicesync_api.log")
    ]
)

logger = logging.getLogger(__name__)

# Crear la aplicación FastAPI
app = FastAPI(
    title="InvoiceSync API",
    description="API para procesar facturas desde correo electrónico y exportarlas a Excel",
    version="2.0.0"
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, limitar a dominios específicos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instancia global del procesador
invoice_sync = InvoiceSync()

# Tarea en segundo plano para procesar correos
def process_emails_task():
    """Tarea en segundo plano para procesar correos."""
    try:
        result = invoice_sync.process_emails()
        logger.info(f"Tarea en segundo plano completada: {result.message}")
    except Exception as e:
        logger.error(f"Error en tarea en segundo plano: {str(e)}")

@app.get("/")
async def root():
    """Endpoint raíz para verificar que la API está funcionando."""
    return {"message": "InvoiceSync API está en funcionamiento"}

@app.post("/process", response_model=ProcessResult)
async def process_emails(background_tasks: BackgroundTasks, run_async: bool = False):
    """
    Procesa correos electrónicos para extraer facturas.
    
    Args:
        background_tasks: Gestor de tareas en segundo plano.
        run_async: Si es True, el procesamiento se ejecuta en segundo plano.
        
    Returns:
        ProcessResult: Resultado del procesamiento.
    """
    try:
        if run_async:
            # Ejecutar en segundo plano
            background_tasks.add_task(process_emails_task)
            return ProcessResult(
                success=True,
                message="Procesamiento iniciado en segundo plano"
            )
        else:
            # Ejecutar de forma síncrona
            result = invoice_sync.process_emails()
            return result
    except Exception as e:
        logger.error(f"Error al procesar correos: {str(e)}")
        return ProcessResult(
            success=False,
            message=f"Error al procesar correos: {str(e)}"
        )

@app.post("/upload", response_model=ProcessResult)
async def upload_pdf(
    file: UploadFile = File(...),
    sender: Optional[str] = Form(None),
    date: Optional[str] = Form(None)
):
    """
    Sube un archivo PDF para procesarlo directamente.
    
    Args:
        file: Archivo PDF a procesar.
        sender: Remitente (opcional).
        date: Fecha del documento (opcional).
        
    Returns:
        ProcessResult: Resultado del procesamiento.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos PDF")
    
    try:
        # Guardar el archivo
        pdf_path = os.path.join(settings.TEMP_PDF_DIR, file.filename)
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Preparar metadatos
        email_meta = {
            "sender": sender or "Carga manual",
        }
        
        # Convertir fecha si se proporciona
        if date:
            try:
                email_meta["date"] = datetime.strptime(date, "%Y-%m-%d")
            except:
                logger.warning(f"Formato de fecha incorrecto: {date}")
        
        # Procesar con OpenAI
        invoice_data = invoice_sync.process_pdf(pdf_path, email_meta)
        
        # Exportar a Excel
        invoices = [invoice_data]
        excel_path = invoice_sync.excel_exporter.export_invoices(invoices)
        
        if not excel_path:
            return ProcessResult(
                success=False,
                message="Error al exportar a Excel",
                invoice_count=0,
                invoices=invoices
            )
        
        return ProcessResult(
            success=True,
            message=f"Factura procesada correctamente. Excel: {excel_path}",
            invoice_count=1,
            invoices=invoices
        )
        
    except Exception as e:
        logger.error(f"Error al procesar el archivo: {str(e)}")
        return ProcessResult(
            success=False,
            message=f"Error al procesar el archivo: {str(e)}"
        )

@app.get("/excel")
async def get_excel():
    """
    Descarga el archivo Excel con las facturas procesadas.
    
    Returns:
        FileResponse: Archivo Excel para descargar.
    """
    excel_path = settings.EXCEL_OUTPUT_PATH
    
    if not os.path.exists(excel_path):
        raise HTTPException(status_code=404, detail="Archivo Excel no encontrado")
    
    return FileResponse(
        path=excel_path,
        filename="facturas.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.get("/status")
async def get_status():
    """
    Obtiene el estado actual del sistema.
    
    Returns:
        dict: Estado del sistema.
    """
    excel_path = settings.EXCEL_OUTPUT_PATH
    job_status = invoice_sync.get_job_status()
    
    status_info = {
        "status": "active",
        "excel_exists": os.path.exists(excel_path),
        "last_modified": datetime.fromtimestamp(os.path.getmtime(excel_path)) if os.path.exists(excel_path) else None,
        "temp_dir": settings.TEMP_PDF_DIR,
        "email_configured": bool(settings.EMAIL_USERNAME and settings.EMAIL_PASSWORD),
        "openai_configured": bool(settings.OPENAI_API_KEY),
        "job": {
            "running": job_status.running,
            "interval_minutes": job_status.interval_minutes,
            "next_run": job_status.next_run,
            "last_run": job_status.last_run
        }
    }
    
    return status_info

@app.post("/job/start", response_model=JobStatus)
async def start_job():
    """
    Inicia el trabajo programado para procesar correos periódicamente.
    
    Returns:
        JobStatus: Estado del trabajo.
    """
    try:
        job_status = invoice_sync.start_scheduled_job()
        return job_status
    except Exception as e:
        logger.error(f"Error al iniciar el job: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al iniciar el job: {str(e)}")

@app.post("/job/stop", response_model=JobStatus)
async def stop_job():
    """
    Detiene el trabajo programado.
    
    Returns:
        JobStatus: Estado del trabajo.
    """
    try:
        job_status = invoice_sync.stop_scheduled_job()
        return job_status
    except Exception as e:
        logger.error(f"Error al detener el job: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al detener el job: {str(e)}")

@app.get("/job/status", response_model=JobStatus)
async def job_status():
    """
    Obtiene el estado actual del trabajo programado.
    
    Returns:
        JobStatus: Estado del trabajo.
    """
    return invoice_sync.get_job_status()

def start():
    """Inicia el servidor API."""
    uvicorn.run(
        "app.api.api:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True
    )

if __name__ == "__main__":
    start()
