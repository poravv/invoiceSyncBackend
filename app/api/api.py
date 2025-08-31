from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import logging
import uvicorn
from typing import List, Optional
import shutil
from datetime import datetime
from fastapi.responses import FileResponse
from fastapi import Response

from app.config.settings import settings
from app.models.models import InvoiceData, EmailConfig, ProcessResult, JobStatus, ExcelFileInfo, ExcelFileList, MultiEmailConfig
from app.main import InvoiceSync
from app.modules.scheduler.processing_lock import PROCESSING_LOCK
from app.modules.scheduler.task_queue import task_queue
from app.modules.email_processor.storage import save_binary

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

@app.post("/tasks/process")
async def enqueue_process_emails():
    """Encola una ejecución de procesamiento de correos y retorna un job_id."""
    def _runner():
        return invoice_sync.process_emails()

    job_id = task_queue.enqueue("process_emails", _runner)
    return {"job_id": job_id}

@app.get("/tasks/{job_id}")
async def get_task_status(job_id: str):
    """Consulta el estado de un job enviado a la cola."""
    job = task_queue.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job

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
            except Exception:
                logger.warning(f"Formato de fecha incorrecto: {date}")

        # Serializar extracción + exportación para no interferir con automatización
        with PROCESSING_LOCK:
            invoice_data = invoice_sync.openai_processor.extract_invoice_data(pdf_path, email_meta)
            invoices = [invoice_data] if invoice_data else []
            excel_path = invoice_sync.excel_exporter.export_invoices(invoices)

        excel_files = [excel_path] if excel_path else []

        if not excel_path:
            return ProcessResult(
                success=False,
                message="Error al exportar a Excel",
                invoice_count=0,
                invoices=invoices,
                excel_files=[]
            )

        return ProcessResult(
            success=True,
            message=f"Factura procesada correctamente. Excel: {excel_path}",
            invoice_count=1,
            invoices=invoices,
            excel_files=excel_files
        )
        
    except Exception as e:
        logger.error(f"Error al procesar el archivo: {str(e)}")
        return ProcessResult(
            success=False,
            message=f"Error al procesar el archivo: {str(e)}"
        )

@app.post("/upload-xml", response_model=ProcessResult)
async def upload_xml(
    file: UploadFile = File(...),
    sender: Optional[str] = Form(None),
    date: Optional[str] = Form(None)
):
    """
    Sube un archivo XML SIFEN para procesarlo directamente con el parser nativo (fallback OpenAI).
    """
    if not (file.filename.lower().endswith('.xml')):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos XML")

    try:
        # Guardar el archivo XML
        xml_path = os.path.join(settings.TEMP_PDF_DIR, file.filename)
        with open(xml_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Metadatos opcionales
        email_meta = {
            "sender": sender or "Carga manual",
        }
        if date:
            try:
                email_meta["date"] = datetime.strptime(date, "%Y-%m-%d")
            except Exception:
                logger.warning(f"Formato de fecha incorrecto: {date}")

        with PROCESSING_LOCK:
            # Procesar XML
            invoice_data = invoice_sync.openai_processor.extract_invoice_data_from_xml(xml_path, email_meta)

            invoices = [invoice_data] if invoice_data else []
            if not invoices:
                return ProcessResult(
                    success=False,
                    message="No se pudo extraer información desde el XML",
                    invoice_count=0,
                    invoices=[],
                    excel_files=[]
                )

            # Exportar a Excel
            excel_path = invoice_sync.excel_exporter.export_invoices(invoices)
            excel_files = [excel_path] if excel_path else []

        if not excel_path:
            return ProcessResult(
                success=False,
                message="Error al exportar a Excel",
                invoice_count=1,
                invoices=invoices,
                excel_files=[]
            )

        return ProcessResult(
            success=True,
            message=f"Factura XML procesada correctamente. Excel: {excel_path}",
            invoice_count=1,
            invoices=invoices,
            excel_files=excel_files
        )

    except Exception as e:
        logger.error(f"Error al procesar el XML: {str(e)}")
        return ProcessResult(
            success=False,
            message=f"Error al procesar el XML: {str(e)}"
        )

@app.post("/tasks/upload-pdf")
async def enqueue_upload_pdf(
    file: UploadFile = File(...),
    sender: Optional[str] = Form(None),
    date: Optional[str] = Form(None)
):
    """Encola el procesamiento de un PDF manual y retorna job_id."""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos PDF")

    try:
        file_bytes = await file.read()
        pdf_path = save_binary(file_bytes, file.filename, force_pdf=True)
        email_meta = {"sender": sender or "Carga manual"}
        if date:
            try:
                email_meta["date"] = datetime.strptime(date, "%Y-%m-%d")
            except Exception:
                logger.warning(f"Formato de fecha incorrecto: {date}")

        def _runner():
            inv = invoice_sync.openai_processor.extract_invoice_data(pdf_path, email_meta)
            invoices = [inv] if inv else []
            path = invoice_sync.excel_exporter.export_invoices(invoices)
            return ProcessResult(
                success=bool(invoices and path),
                message=(f"Factura procesada correctamente. Excel: {path}" if invoices and path else
                         ("No se pudo extraer factura" if not invoices else "Error al exportar a Excel")),
                invoice_count=len(invoices),
                invoices=invoices,
                excel_files=([path] if path else [])
            )

        job_id = task_queue.enqueue("upload_pdf", _runner)
        return {"job_id": job_id}
    except Exception as e:
        logger.error(f"Error al encolar PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tasks/upload-xml")
async def enqueue_upload_xml(
    file: UploadFile = File(...),
    sender: Optional[str] = Form(None),
    date: Optional[str] = Form(None)
):
    """Encola el procesamiento de un XML manual y retorna job_id."""
    if not file.filename.lower().endswith('.xml'):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos XML")

    try:
        file_bytes = await file.read()
        xml_path = save_binary(file_bytes, file.filename)
        email_meta = {"sender": sender or "Carga manual"}
        if date:
            try:
                email_meta["date"] = datetime.strptime(date, "%Y-%m-%d")
            except Exception:
                logger.warning(f"Formato de fecha incorrecto: {date}")

        def _runner():
            inv = invoice_sync.openai_processor.extract_invoice_data_from_xml(xml_path, email_meta)
            invoices = [inv] if inv else []
            if not invoices:
                return ProcessResult(success=False, message="No se pudo extraer información desde el XML",
                                    invoice_count=0, invoices=[], excel_files=[])
            path = invoice_sync.excel_exporter.export_invoices(invoices)
            return ProcessResult(
                success=bool(path),
                message=(f"Factura XML procesada correctamente. Excel: {path}" if path else "Error al exportar a Excel"),
                invoice_count=len(invoices),
                invoices=invoices,
                excel_files=([path] if path else [])
            )

        job_id = task_queue.enqueue("upload_xml", _runner)
        return {"job_id": job_id}
    except Exception as e:
        logger.error(f"Error al encolar XML: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/excel")
async def get_excel():
    """
    Descarga el archivo Excel más reciente (última fecha modificación).
    """
    try:
        # Obtener lista de archivos Excel disponibles
        excel_files = invoice_sync.excel_exporter.get_available_excel_files()
        
        if not excel_files:
            raise HTTPException(status_code=404, detail="No se encontraron archivos Excel")
        
        # Ordenar por fecha de modificación (más reciente primero)
        excel_files.sort(key=lambda x: x.last_modified, reverse=True)
        latest_file = excel_files[0]
        
        if not os.path.exists(latest_file.path):
            raise HTTPException(status_code=404, detail="Archivo Excel no encontrado")
        
        response = FileResponse(
            path=latest_file.path,
            filename=latest_file.filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        # Agregar headers para evitar caché
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
        return response
        
    except Exception as e:
        logger.error(f"Error al obtener archivo Excel: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al obtener archivo Excel: {str(e)}")

@app.get("/excel/list", response_model=ExcelFileList)
async def list_excel_files():
    """
    Obtiene la lista de archivos Excel disponibles por mes.
    
    Returns:
        ExcelFileList: Lista de archivos Excel disponibles con metadatos
    """
    try:
        excel_files = invoice_sync.excel_exporter.get_available_excel_files()
        return ExcelFileList(
            files=excel_files,
            total_count=len(excel_files)
        )
    except Exception as e:
        logger.error(f"Error al listar archivos Excel: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al listar archivos Excel: {str(e)}")

@app.get("/excel/{year_month}")
async def get_excel_by_month(year_month: str):
    """
    Descarga el archivo Excel de un mes específico.
    
    Args:
        year_month: Mes en formato YYYY-MM
    """
    try:
        # Validar formato del mes
        try:
            datetime.strptime(year_month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de mes incorrecto. Use YYYY-MM")
        
        # Obtener ruta del archivo Excel para ese mes
        excel_path = invoice_sync.excel_exporter.get_excel_by_month(year_month)
        
        if not excel_path:
            raise HTTPException(status_code=404, detail=f"Archivo Excel no encontrado para {year_month}")
        
        filename = f"facturas_ascont_{year_month}.xlsx"
        
        response = FileResponse(
            path=excel_path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        # Agregar headers para evitar caché
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error al obtener archivo Excel de {year_month}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al obtener archivo Excel: {str(e)}")

@app.post("/email-config/test")
async def test_email_config(config: MultiEmailConfig):
    """
    Prueba la conexión a una configuración de correo.
    
    Args:
        config: Configuración de correo a probar
        
    Returns:
        dict: Resultado de la prueba
    """
    try:
        from app.modules.email_processor.email_processor import EmailProcessor
        from app.models.models import EmailConfig
        
        # Crear configuración temporal para probar
        test_config = EmailConfig(
            host=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
            search_criteria=config.search_criteria,
            search_terms=config.search_terms
        )
        
        # Crear procesador temporal
        processor = EmailProcessor(test_config)
        
        # Intentar conectar
        success = processor.connect()
        processor.disconnect()
        
        if success:
            return {"success": True, "message": "Conexión exitosa"}
        else:
            return {"success": False, "message": "Error al conectar"}
            
    except Exception as e:
        logger.error(f"Error al probar configuración de correo: {str(e)}")
        return {"success": False, "message": f"Error: {str(e)}"}


@app.get("/status")
async def get_status():
    """
    Obtiene el estado actual del sistema.
    
    Returns:
        dict: Estado del sistema.
    """
    try:
        # Obtener archivos Excel disponibles
        excel_files = invoice_sync.excel_exporter.get_available_excel_files()
        excel_exists = len(excel_files) > 0
        last_modified = excel_files[0].last_modified if excel_files else None
        
        # Estado del job
        job_status = invoice_sync.get_job_status()
        
        # Configuraciones de correo
        email_configs = settings.get_all_email_configs()
        
        status_info = {
            "status": "active",
            "excel_files_count": len(excel_files),
            "excel_exists": excel_exists,
            "last_modified": last_modified,
            "temp_dir": settings.TEMP_PDF_DIR,
            "excel_output_dir": settings.EXCEL_OUTPUT_DIR,
            "email_configs_count": len(email_configs),
            "email_configured": len(email_configs) > 0 and all(
                config.get('username') and config.get('password') 
                for config in email_configs
            ),
            "openai_configured": bool(settings.OPENAI_API_KEY),
            "job": {
                "running": job_status.running,
                "interval_minutes": job_status.interval_minutes,
                "next_run": job_status.next_run,
                "last_run": job_status.last_run
            },
            "excel_files": excel_files
        }
        
        return status_info
        
    except Exception as e:
        logger.error(f"Error al obtener estado: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al obtener estado: {str(e)}")

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
