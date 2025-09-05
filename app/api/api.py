from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import logging
import uvicorn
import uuid
import time
from typing import List, Optional, Dict, Any
import shutil
from datetime import datetime
from fastapi.responses import FileResponse
from fastapi import Response
from pydantic import BaseModel

from app.config.settings import settings
from app.models.models import InvoiceData, EmailConfig, ProcessResult, JobStatus, ExcelFileInfo, ExcelFileList, MultiEmailConfig
from app.main import InvoiceSync
from app.modules.scheduler.processing_lock import PROCESSING_LOCK
from app.modules.scheduler.task_queue import task_queue
from app.modules.email_processor.storage import save_binary
from app.modules.prefs.prefs import get_auto_refresh as prefs_get_auto_refresh, set_auto_refresh as prefs_set_auto_refresh
from app.modules.excel_exporter import ExcelExporterCompleto, MongoDBExporter
from app.modules.mongo_query_service import get_mongo_query_service

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

# Payloads
class IntervalPayload(BaseModel):
    minutes: int

# Preferencias UI
class AutoRefreshPayload(BaseModel):
    enabled: bool
    interval_ms: int = 30000
    uid: Optional[str] = None

class AutoRefreshPref(BaseModel):
    uid: str
    enabled: bool
    interval_ms: int

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

@app.post("/process-direct")
async def process_emails_direct():
    """Procesa correos directamente sin cola de tareas (modo simple)."""
    try:
        # Ejecutar procesamiento directamente
        result = invoice_sync.process_emails()
        
        if result and hasattr(result, 'success') and result.success:
            return {
                "success": True,
                "message": result.message,
                "invoice_count": getattr(result, 'invoice_count', 0)
            }
        else:
            return {
                "success": False,
                "message": getattr(result, 'message', 'Error en el procesamiento'),
                "invoice_count": 0
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/tasks/process")
async def enqueue_process_emails():
    """Encola una ejecución de procesamiento de correos y retorna un job_id."""
    
    # Verificar si el job automático está ejecutándose
    job_status = invoice_sync.get_job_status()
    if job_status.running:
        # Retornar error inmediatamente si el job automático está activo
        job_id = str(uuid.uuid4().hex)
        task_queue._jobs[job_id] = {
            'job_id': job_id,
            'action': 'process_emails',
            'status': 'error',
            'created_at': time.time(),
            'started_at': time.time(),
            'finished_at': time.time(),
            'message': 'No se puede procesar manualmente mientras la automatización esté activa. Detenga la automatización primero.',
            'result': ProcessResult(
                success=False,
                message='No se puede procesar manualmente mientras la automatización esté activa. Detenga la automatización primero.',
                invoice_count=0,
                processed_emails=0
            ),
            '_func': None,
        }
        return {"job_id": job_id}
    
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

@app.delete("/tasks/cleanup")
async def cleanup_old_tasks():
    """Limpia tareas antiguas que están atoradas."""
    cleanup_count = 0
    current_time = time.time()
    
    # Limpiar tareas que llevan más de 1 hora atoradas
    with task_queue._lock:
        jobs_to_remove = []
        for job_id, job in task_queue._jobs.items():
            if job.get('status') == 'running':
                created_at = job.get('created_at', current_time)
                # Si la tarea lleva más de 1 hora "running", marcarla como error
                if current_time - created_at > 3600:  # 1 hora
                    job['status'] = 'error'
                    job['message'] = 'Tarea cancelada por tiempo excesivo'
                    job['finished_at'] = current_time
                    cleanup_count += 1
                    
            # Eliminar tareas completadas que tengan más de 24 horas
            elif job.get('status') in ['done', 'error']:
                created_at = job.get('created_at', current_time)
                if current_time - created_at > 86400:  # 24 horas
                    jobs_to_remove.append(job_id)
                    cleanup_count += 1
        
        # Remover tareas antiguas
        for job_id in jobs_to_remove:
            del task_queue._jobs[job_id]
    
    return {"message": f"Se limpiaron {cleanup_count} tareas", "cleaned_count": cleanup_count}

@app.get("/tasks/debug")
async def debug_tasks():
    """Debug endpoint para ver el estado de todas las tareas."""
    current_time = time.time()
    task_info = []
    
    with task_queue._lock:
        for job_id, job in task_queue._jobs.items():
            job_copy = {k: v for k, v in job.items() if k != '_func'}
            created_at = job.get('created_at', current_time)
            running_time = current_time - created_at
            job_copy['running_time_seconds'] = running_time
            task_info.append(job_copy)
    
    return {
        "total_tasks": len(task_info),
        "tasks": task_info,
        "processing_lock_available": not PROCESSING_LOCK.locked()
    }

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


@app.get("/health")
async def health_check():
    """
    Health check endpoint for container health checks.
    
    Returns:
        dict: Simple health status.
    """
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

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

@app.post("/job/interval", response_model=JobStatus)
async def set_job_interval(payload: IntervalPayload):
    """Ajusta el intervalo (minutos) del job de automatización."""
    try:
        status = invoice_sync.update_job_interval(payload.minutes)
        return status
    except Exception as e:
        logger.error(f"Error al ajustar intervalo del job: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error al ajustar intervalo: {str(e)}")

@app.get("/cache/stats")
async def cache_stats():
    """
    Obtiene estadísticas del cache de OpenAI.
    
    Returns:
        dict: Estadísticas del cache.
    """
    try:
        if hasattr(invoice_sync.openai_processor, 'cache') and invoice_sync.openai_processor.cache:
            stats = invoice_sync.openai_processor.cache.get_cache_stats()
            return {
                "cache_enabled": True,
                **stats
            }
        else:
            return {"cache_enabled": False, "message": "Cache no habilitado"}
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas del cache: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo estadísticas del cache: {str(e)}")

@app.post("/cache/clear")
async def clear_cache(older_than_hours: Optional[int] = None):
    """
    Limpia el cache de OpenAI.
    
    Args:
        older_than_hours: Si se especifica, elimina solo cache más viejo que X horas
    
    Returns:
        dict: Resultado de la limpieza.
    """
    try:
        if hasattr(invoice_sync.openai_processor, 'cache') and invoice_sync.openai_processor.cache:
            files_removed = invoice_sync.openai_processor.cache.clear_cache(older_than_hours)
            return {
                "success": True,
                "files_removed": files_removed,
                "message": f"Cache limpiado: {files_removed} archivos eliminados"
            }
        else:
            return {"success": False, "message": "Cache no habilitado"}
    except Exception as e:
        logger.error(f"Error limpiando cache: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error limpiando cache: {str(e)}")

@app.get("/imap/pool/stats")
async def imap_pool_stats():
    """
    Obtiene estadísticas del pool de conexiones IMAP.
    
    Returns:
        dict: Estadísticas del pool de conexiones.
    """
    try:
        from app.modules.email_processor.connection_pool import get_imap_pool
        pool = get_imap_pool()
        stats = pool.get_pool_stats()
        
        return {
            "pool_enabled": True,
            "configurations": stats,
            "total_pools": len(stats)
        }
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas del pool IMAP: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo estadísticas del pool: {str(e)}")

@app.get("/excel/stats")
async def excel_stats():
    """
    Obtiene estadísticas del archivo Excel actual.
    
    Returns:
        dict: Estadísticas del Excel.
    """
    try:
        from app.modules.excel_exporter.incremental_exporter import IncrementalExcelExporter
        exporter = IncrementalExcelExporter()
        stats = exporter.get_stats()
        
        return {
            "incremental_export_enabled": True,
            **stats
        }
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas del Excel: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo estadísticas del Excel: {str(e)}")

@app.get("/health/detailed")
async def detailed_health():
    """
    Health check comprensivo con métricas detalladas de todos los componentes.
    
    Returns:
        dict: Estado detallado del sistema con métricas de performance.
    """
    try:
        from app.modules.monitoring import get_health_checker
        health_checker = get_health_checker()
        health_report = await health_checker.comprehensive_health_check()
        
        return health_report
    except Exception as e:
        logger.error(f"Error en health check detallado: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en health check: {str(e)}")

@app.get("/health/trends")
async def health_trends():
    """
    Obtiene tendencias de salud del sistema basadas en histórico.
    
    Returns:
        dict: Tendencias y métricas históricas.
    """
    try:
        from app.modules.monitoring import get_health_checker
        health_checker = get_health_checker()
        trends = health_checker.get_health_trends()
        
        return trends
    except Exception as e:
        logger.error(f"Error obteniendo tendencias de salud: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo tendencias: {str(e)}")

@app.post("/system/force-restart")
async def force_system_restart():
    """
    Endpoint de emergencia para forzar reinicio del sistema cuando hay bloqueos.
    
    Returns:
        dict: Confirmación de reinicio.
    """
    global invoice_sync
    
    try:
        logger.warning("🚨 REINICIO DE EMERGENCIA SOLICITADO - Forzando limpieza del sistema")
        
        # Detener job programado si está corriendo
        try:
            invoice_sync.stop_scheduled_job()
            logger.info("✅ Job programado detenido")
        except Exception as e:
            logger.warning(f"⚠️ Error deteniendo job: {e}")
        
        # Limpiar tareas pendientes
        try:
            task_queue.cleanup_old_tasks()
            logger.info("✅ Tareas limpiadas")
        except Exception as e:
            logger.warning(f"⚠️ Error limpiando tareas: {e}")
        
        # Liberar lock de procesamiento
        try:
            if PROCESSING_LOCK.locked():
                PROCESSING_LOCK.release()
                logger.info("✅ Processing lock liberado")
        except Exception as e:
            logger.warning(f"⚠️ Error liberando lock: {e}")
        
        # Reinicializar invoice_sync
        try:
            invoice_sync = InvoiceSync()
            logger.info("✅ InvoiceSync reinicializado")
        except Exception as e:
            logger.warning(f"⚠️ Error reinicializando InvoiceSync: {e}")
        
        return {
            "success": True,
            "message": "Sistema reiniciado exitosamente",
            "timestamp": datetime.now().isoformat(),
            "actions": [
                "Job programado detenido",
                "Tareas limpiadas", 
                "Processing lock liberado",
                "InvoiceSync reinicializado"
            ]
        }
    except Exception as e:
        logger.error(f"❌ Error en reinicio de emergencia: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en reinicio: {str(e)}")

@app.get("/system/health")
async def get_system_health():
    """
    Endpoint de salud del sistema con información detallada.
    
    Returns:
        dict: Estado de salud del sistema.
    """
    try:
        import psutil
        import threading
        
        # Información básica del sistema
        health_info = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "uptime_seconds": time.time() - getattr(app.state, 'start_time', time.time()),
            
            # Estado de threads
            "active_threads": threading.active_count(),
            "thread_names": [t.name for t in threading.enumerate()],
            
            # Estado de procesamiento
            "processing_lock_acquired": PROCESSING_LOCK.locked(),
            "pending_tasks": task_queue.get_pending_tasks_count(),
            
            # Job programado
            "scheduled_job_running": invoice_sync.get_job_status().get("running", False),
            
            # Memoria y CPU
            "memory_usage_mb": psutil.Process().memory_info().rss / 1024 / 1024,
            "cpu_percent": psutil.Process().cpu_percent(),
        }
        
        # Determinar estado general
        if health_info["active_threads"] > 20:
            health_info["status"] = "warning"
            health_info["warning"] = "Alto número de threads activos"
        elif health_info["memory_usage_mb"] > 500:
            health_info["status"] = "warning"  
            health_info["warning"] = "Alto uso de memoria"
        elif health_info["processing_lock_acquired"] and health_info["pending_tasks"] == 0:
            health_info["status"] = "warning"
            health_info["warning"] = "Processing lock adquirido sin tareas pendientes"
            
        return health_info
        
    except Exception as e:
        logger.error(f"Error obteniendo salud del sistema: {str(e)}")
        return {
            "status": "error",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }

def start():
    """Inicia el servidor API."""
    # Guardar tiempo de inicio
    app.state.start_time = time.time()
    
    uvicorn.run(
        "app.api.api:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True
    )

if __name__ == "__main__":
    start()

# -----------------------------
# Preferencias (UI / Auto‑refresh)
# -----------------------------

@app.get("/prefs/auto-refresh", response_model=AutoRefreshPref)
async def get_auto_refresh(uid: Optional[str] = Query(default="global")):
    try:
        data = prefs_get_auto_refresh(uid) or {"enabled": False, "interval_ms": 30000}
        return AutoRefreshPref(uid=uid, enabled=bool(data.get("enabled", False)), interval_ms=int(data.get("interval_ms", 30000)))
    except Exception as e:
        logger.error(f"Error al obtener preferencia auto-refresh: {e}")
        raise HTTPException(status_code=500, detail="No se pudo obtener preferencia")

@app.post("/prefs/auto-refresh", response_model=AutoRefreshPref)
async def set_auto_refresh(payload: AutoRefreshPayload):
    try:
        uid = payload.uid or "global"
        data = prefs_set_auto_refresh(uid, payload.enabled, payload.interval_ms)
        return AutoRefreshPref(uid=uid, enabled=bool(data.get("enabled", False)), interval_ms=int(data.get("interval_ms", 30000)))
    except Exception as e:
        logger.error(f"Error al guardar preferencia auto-refresh: {e}")
        raise HTTPException(status_code=500, detail="No se pudo guardar preferencia")

# -----------------------------
# Exportadores Avanzados 
# -----------------------------

@app.post("/export/excel-completo")
async def export_excel_completo(background_tasks: BackgroundTasks, run_async: bool = False):
    """
    Exporta TODAS las facturas en formato Excel completo con múltiples hojas.
    Incluye detalles completos: productos, empresas, clientes, datos técnicos, etc.
    """
    try:
        if run_async:
            background_tasks.add_task(_export_completo_task)
            return {
                "success": True,
                "message": "Exportación completa iniciada en segundo plano",
                "export_type": "excel_completo"
            }
        else:
            return await _export_completo_task()
    except Exception as e:
        logger.error(f"Error en export completo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en export completo: {str(e)}")

@app.post("/export/mongodb")
async def export_to_mongodb(background_tasks: BackgroundTasks, run_async: bool = False):
    """
    Exporta TODAS las facturas a MongoDB en formato documental optimizado.
    Ideal para análisis avanzado, reporting y consultas complejas.
    """
    try:
        if run_async:
            background_tasks.add_task(_export_mongodb_task)
            return {
                "success": True,
                "message": "Exportación a MongoDB iniciada en segundo plano",
                "export_type": "mongodb"
            }
        else:
            return await _export_mongodb_task()
    except Exception as e:
        logger.error(f"Error en export MongoDB: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en export MongoDB: {str(e)}")

@app.get("/export/excel-completo/list")
async def list_excel_completo_files():
    """
    Lista archivos Excel completos disponibles.
    """
    try:
        exporter = ExcelExporterCompleto()
        files = exporter.get_available_excel_files()
        return {
            "success": True,
            "export_type": "excel_completo",
            "files": files,
            "total_count": len(files)
        }
    except Exception as e:
        logger.error(f"Error listando archivos completos: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error listando archivos: {str(e)}")

@app.get("/export/excel-completo/{year_month}")
async def download_excel_completo(year_month: str):
    """
    Descarga archivo Excel completo de un mes específico.
    """
    try:
        # Validar formato
        try:
            datetime.strptime(year_month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de mes incorrecto. Use YYYY-MM")

        exporter = ExcelExporterCompleto()
        excel_path = exporter.get_excel_by_month(year_month)
        
        if not excel_path:
            raise HTTPException(status_code=404, detail=f"Archivo Excel completo no encontrado para {year_month}")

        filename = f"facturas_completas_{year_month}.xlsx"
        
        response = FileResponse(
            path=excel_path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error descargando Excel completo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error descargando archivo: {str(e)}")

@app.post("/export/excel-completo/{year_month}")
async def export_excel_completo_month(year_month: str, background_tasks: BackgroundTasks, run_async: bool = False):
    """
    Exporta facturas de un mes específico desde MongoDB al formato Excel completo.
    Args:
        year_month: Mes en formato YYYY-MM (ej: 2025-01)
        run_async: Si true, ejecuta en segundo plano
    """
    try:
        # Validar formato de fecha
        try:
            datetime.strptime(year_month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de mes inválido. Use YYYY-MM")
        
        if run_async:
            background_tasks.add_task(_export_completo_month_task, year_month)
            return {
                "success": True,
                "message": f"Exportación completa del mes {year_month} iniciada en segundo plano",
                "export_type": "excel_completo",
                "year_month": year_month
            }
        else:
            return await _export_completo_month_task(year_month)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en export completo por mes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en export completo: {str(e)}")

@app.get("/export/mongodb/stats")
async def mongodb_export_stats():
    """
    Obtiene estadísticas de la base de datos MongoDB.
    """
    try:
        exporter = MongoDBExporter()
        try:
            stats = exporter.get_statistics()
            return {
                "success": True,
                "export_type": "mongodb",
                "database_stats": stats
            }
        finally:
            exporter.close_connections()
    except Exception as e:
        logger.error(f"Error obteniendo stats MongoDB: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo estadísticas: {str(e)}")

@app.post("/export/process-and-export")
async def process_and_export_all(
    background_tasks: BackgroundTasks,
    export_types: List[str] = Query(default=["ascont"], description="Tipos de export: ascont, completo, mongodb"),
    run_async: bool = False
):
    """
    Procesa emails Y exporta en los formatos especificados.
    
    Args:
        export_types: Lista de formatos de export ("ascont", "completo", "mongodb")
        run_async: Si ejecutar en segundo plano
    """
    valid_types = {"ascont", "completo", "mongodb"}
    invalid_types = set(export_types) - valid_types
    if invalid_types:
        raise HTTPException(status_code=400, detail=f"Tipos de export inválidos: {invalid_types}")
    
    try:
        if run_async:
            background_tasks.add_task(_process_and_export_task, export_types)
            return {
                "success": True,
                "message": f"Procesamiento y exportación iniciados en segundo plano",
                "export_types": export_types
            }
        else:
            return await _process_and_export_task(export_types)
    except Exception as e:
        logger.error(f"Error en process-and-export: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en procesamiento: {str(e)}")

# Funciones auxiliares para tareas en segundo plano

async def _export_completo_task():
    """Tarea para exportar Excel completo"""
    try:
        from app.config.export_config import get_excel_completo_config
        from app.modules.mongo_query_service import get_mongo_query_service
        
        config = get_excel_completo_config()
        current_month = datetime.now().strftime("%Y-%m")
        
        # Verificar si debe obtener datos desde MongoDB
        if config.get("from_mongodb", True):
            logger.info("🔍 Obteniendo facturas desde MongoDB para exportación completa")
            mongo_service = get_mongo_query_service()
            
            # Obtener facturas del mes actual desde MongoDB
            mongo_docs = mongo_service.get_invoices_by_month(current_month)
            
            if not mongo_docs:
                return {
                    "success": False,
                    "message": f"No hay facturas en MongoDB para el mes {current_month}",
                    "export_type": "excel_completo"
                }
            
            # Convertir documentos MongoDB a objetos InvoiceData
            invoices = []
            for doc in mongo_docs:
                invoice = _mongo_doc_to_invoice_data(doc)
                if invoice:
                    invoices.append(invoice)
                    
            logger.info("📄 Convertidas %d facturas desde MongoDB", len(invoices))
        else:
            # Usar facturas en memoria (modo legacy)
            invoices = getattr(invoice_sync, '_last_processed_invoices', [])
        
        if not invoices:
            return {
                "success": False,
                "message": "No hay facturas disponibles para exportar.",
                "export_type": "excel_completo"
            }
        
        exporter = ExcelExporterCompleto()
        excel_path = exporter.export_invoices(invoices)
        
        if excel_path:
            return {
                "success": True,
                "message": f"Excel completo generado exitosamente: {excel_path}",
                "export_type": "excel_completo",
                "file_path": excel_path,
                "invoice_count": len(invoices)
            }
        else:
            return {
                "success": False,
                "message": "Error generando archivo Excel completo",
                "export_type": "excel_completo"
            }
            
    except Exception as e:
        logger.error(f"Error en export completo task: {e}")
        return {
            "success": False,
            "message": f"Error en exportación completa: {str(e)}",
            "export_type": "excel_completo"
        }

async def _export_mongodb_task():
    """Tarea para exportar a MongoDB"""
    try:
        # Obtener facturas para exportar
        invoices = getattr(invoice_sync, '_last_processed_invoices', [])
        
        if not invoices:
            return {
                "success": False,
                "message": "No hay facturas disponibles para exportar a MongoDB. Procese emails primero.",
                "export_type": "mongodb"
            }
        
        exporter = MongoDBExporter()
        try:
            result = exporter.export_invoices(invoices)
            
            return {
                "success": True,
                "message": f"Exportación a MongoDB completada: {result['inserted']} insertados, {result['updated']} actualizados",
                "export_type": "mongodb",
                "mongo_result": result,
                "invoice_count": len(invoices)
            }
        finally:
            exporter.close_connections()
            
    except Exception as e:
        logger.error(f"Error en export MongoDB task: {e}")
        return {
            "success": False,
            "message": f"Error en exportación MongoDB: {str(e)}",
            "export_type": "mongodb"
        }

async def _process_and_export_task(export_types: List[str]):
    """Tarea combinada: procesar emails y exportar en múltiples formatos"""
    try:
        # 1. Procesar emails primero
        logger.info(f"🔄 Iniciando procesamiento de emails...")
        process_result = invoice_sync.process_emails()
        
        if not process_result.success:
            return {
                "success": False,
                "message": f"Error en procesamiento de emails: {process_result.message}",
                "process_result": process_result,
                "exports": []
            }
        
        invoices = process_result.invoices or []
        if not invoices:
            return {
                "success": False,
                "message": "No se encontraron facturas para exportar",
                "process_result": process_result,
                "exports": []
            }
        
        # Guardar facturas para otros exportadores
        invoice_sync._last_processed_invoices = invoices
        
        # 2. Exportar en formatos solicitados
        export_results = []
        
        for export_type in export_types:
            try:
                if export_type == "ascont":
                    # Ya se hizo en process_emails()
                    export_results.append({
                        "type": "ascont",
                        "success": True,
                        "message": "Export ASCONT incluido en procesamiento",
                        "files": process_result.excel_files or []
                    })
                    
                elif export_type == "completo":
                    logger.info(f"📊 Exportando Excel completo...")
                    exporter = ExcelExporterCompleto()
                    excel_path = exporter.export_invoices(invoices)
                    
                    export_results.append({
                        "type": "completo",
                        "success": bool(excel_path),
                        "message": f"Excel completo: {excel_path}" if excel_path else "Error en Excel completo",
                        "file_path": excel_path if excel_path else None
                    })
                    
                elif export_type == "mongodb":
                    logger.info(f"💾 Exportando a MongoDB...")
                    exporter = MongoDBExporter()
                    try:
                        mongo_result = exporter.export_invoices(invoices)
                        export_results.append({
                            "type": "mongodb",
                            "success": mongo_result['inserted'] + mongo_result['updated'] > 0,
                            "message": f"MongoDB: {mongo_result['inserted']} insertados, {mongo_result['updated']} actualizados",
                            "mongo_stats": mongo_result
                        })
                    finally:
                        exporter.close_connections()
                        
            except Exception as e:
                logger.error(f"Error en export {export_type}: {e}")
                export_results.append({
                    "type": export_type,
                    "success": False,
                    "message": f"Error en {export_type}: {str(e)}"
                })
        
        # 3. Resultado final
        successful_exports = sum(1 for r in export_results if r["success"])
        
        return {
            "success": successful_exports > 0,
            "message": f"Procesamiento completado: {len(invoices)} facturas, {successful_exports}/{len(export_types)} exports exitosos",
            "process_result": process_result,
            "exports": export_results,
            "invoice_count": len(invoices)
        }
        
    except Exception as e:
        logger.error(f"Error en process-and-export task: {e}")
        return {
            "success": False,
            "message": f"Error en procesamiento combinado: {str(e)}",
            "exports": []
        }

async def _export_completo_month_task(year_month: str):
    """Tarea para exportar Excel completo de un mes específico desde MongoDB"""
    try:
        from app.modules.mongo_query_service import get_mongo_query_service
        
        logger.info(f"🔍 Exportando facturas del mes {year_month} desde MongoDB")
        mongo_service = get_mongo_query_service()
        
        # Obtener facturas del mes específico desde MongoDB
        mongo_docs = mongo_service.get_invoices_by_month(year_month)
        
        if not mongo_docs:
            return {
                "success": False,
                "message": f"No hay facturas en MongoDB para el mes {year_month}",
                "export_type": "excel_completo",
                "year_month": year_month
            }
        
        # Convertir documentos MongoDB a objetos InvoiceData
        invoices = []
        for doc in mongo_docs:
            invoice = _mongo_doc_to_invoice_data(doc)
            if invoice:
                invoices.append(invoice)
                
        logger.info(f"📄 Convertidas {len(invoices)} facturas de {year_month}")
        
        # Exportar usando el exportador completo
        exporter = ExcelExporterCompleto()
        excel_path = exporter.export_invoices(invoices)
        
        if excel_path:
            return {
                "success": True,
                "message": f"Excel completo del mes {year_month} generado exitosamente: {excel_path}",
                "export_type": "excel_completo",
                "year_month": year_month,
                "file_path": excel_path,
                "invoice_count": len(invoices)
            }
        else:
            return {
                "success": False,
                "message": f"Error generando archivo Excel completo para {year_month}",
                "export_type": "excel_completo",
                "year_month": year_month
            }
            
    except Exception as e:
        logger.error(f"Error en export completo month task para {year_month}: {e}")
        return {
            "success": False,
            "message": f"Error en exportación del mes {year_month}: {str(e)}",
            "export_type": "excel_completo",
            "year_month": year_month
        }

# -----------------------------
# Consultas MongoDB y Exports por Fecha
# -----------------------------

@app.get("/invoices/months")
async def get_available_months():
    """
    Obtiene lista de meses disponibles con estadísticas básicas desde MongoDB.
    """
    try:
        query_service = get_mongo_query_service()
        months = query_service.get_available_months()
        
        return {
            "success": True,
            "months": months,
            "total_months": len(months)
        }
    except Exception as e:
        logger.error(f"Error obteniendo meses disponibles: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo meses: {str(e)}")

@app.get("/invoices/month/{year_month}")
async def get_invoices_by_month(year_month: str):
    """
    Obtiene todas las facturas de un mes específico desde MongoDB.
    
    Args:
        year_month: Mes en formato YYYY-MM
    """
    try:
        # Validar formato
        try:
            datetime.strptime(year_month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de mes incorrecto. Use YYYY-MM")
        
        query_service = get_mongo_query_service()
        invoices = query_service.get_invoices_by_month(year_month)
        
        return {
            "success": True,
            "year_month": year_month,
            "invoices": invoices,
            "count": len(invoices)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo facturas del mes {year_month}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo facturas: {str(e)}")

@app.get("/invoices/month/{year_month}/stats")
async def get_month_statistics(year_month: str):
    """
    Obtiene estadísticas detalladas de un mes específico desde MongoDB.
    """
    try:
        # Validar formato
        try:
            datetime.strptime(year_month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de mes incorrecto. Use YYYY-MM")
        
        query_service = get_mongo_query_service()
        stats = query_service.get_month_statistics(year_month)
        
        return {
            "success": True,
            "statistics": stats
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas del mes {year_month}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo estadísticas: {str(e)}")

@app.post("/invoices/search")
async def search_invoices(
    query: str = Query(default="", description="Texto libre para buscar"),
    start_date: Optional[str] = Query(default=None, description="Fecha inicio YYYY-MM-DD"),
    end_date: Optional[str] = Query(default=None, description="Fecha fin YYYY-MM-DD"),
    provider_ruc: Optional[str] = Query(default=None, description="RUC del proveedor"),
    client_ruc: Optional[str] = Query(default=None, description="RUC del cliente"),
    min_amount: Optional[float] = Query(default=None, description="Monto mínimo"),
    max_amount: Optional[float] = Query(default=None, description="Monto máximo"),
    limit: int = Query(default=100, description="Límite de resultados")
):
    """
    Búsqueda avanzada de facturas en MongoDB con múltiples filtros.
    """
    try:
        query_service = get_mongo_query_service()
        results = query_service.search_invoices(
            query=query,
            start_date=start_date,
            end_date=end_date,
            provider_ruc=provider_ruc,
            client_ruc=client_ruc,
            min_amount=min_amount,
            max_amount=max_amount,
            limit=limit
        )
        
        return {
            "success": True,
            "results": results,
            "count": len(results),
            "limit": limit
        }
    except Exception as e:
        logger.error(f"Error en búsqueda de facturas: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en búsqueda: {str(e)}")

@app.get("/invoices/recent-activity")
async def get_recent_activity(days: int = Query(default=7, description="Días hacia atrás")):
    """
    Obtiene actividad reciente del sistema desde MongoDB.
    """
    try:
        query_service = get_mongo_query_service()
        activity = query_service.get_recent_activity(days)
        
        return {
            "success": True,
            "activity": activity
        }
    except Exception as e:
        logger.error(f"Error obteniendo actividad reciente: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo actividad: {str(e)}")

@app.get("/export/excel-from-mongodb/{year_month}")
async def export_excel_from_mongodb(year_month: str, 
                                   export_type: str = Query(default="completo", 
                                                          description="Tipo de export: ascont, completo")):
    """
    Exporta Excel de un mes específico consultando directamente desde MongoDB.
    
    Args:
        year_month: Mes en formato YYYY-MM
        export_type: Tipo de export (ascont o completo)
    """
    try:
        # Validar formato
        try:
            datetime.strptime(year_month, "%Y-%m")
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de mes incorrecto. Use YYYY-MM")
        
        if export_type not in ["ascont", "completo"]:
            raise HTTPException(status_code=400, detail="Tipo de export debe ser 'ascont' o 'completo'")
        
        # Obtener facturas desde MongoDB
        query_service = get_mongo_query_service()
        mongo_invoices = query_service.get_invoices_by_month(year_month)
        
        if not mongo_invoices:
            raise HTTPException(status_code=404, detail=f"No se encontraron facturas para {year_month}")
        
        # Convertir documentos MongoDB a InvoiceData (simplificado para el export)
        invoices = []
        for doc in mongo_invoices:
            # Crear InvoiceData básico desde documento MongoDB
            invoice_data = _mongo_doc_to_invoice_data(doc)
            invoices.append(invoice_data)
        
        # Exportar según tipo
        if export_type == "completo":
            exporter = ExcelExporterCompleto()
            excel_path = exporter.export_invoices(invoices)
            filename = f"facturas_completas_{year_month}.xlsx"
        else:  # ascont
            from app.modules.excel_exporter import ExcelExporterASCONT
            exporter = ExcelExporterASCONT()
            excel_path = exporter.export_invoices(invoices)
            filename = f"facturas_ascont_{year_month}.xlsx"
        
        if not excel_path or not os.path.exists(excel_path):
            raise HTTPException(status_code=500, detail="Error generando archivo Excel")
        
        response = FileResponse(
            path=excel_path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exportando Excel desde MongoDB: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error en export: {str(e)}")

def _mongo_doc_to_invoice_data(doc: Dict[str, Any]) -> InvoiceData:
    """
    Convierte documento MongoDB a InvoiceData para compatibilidad con exportadores existentes.
    """
    try:
        # Extraer datos principales
        factura = doc.get("factura", {})
        emisor = doc.get("emisor", {})
        receptor = doc.get("receptor", {})
        montos = doc.get("montos", {})
        productos = doc.get("productos", [])
        
        # Convertir fecha
        fecha = None
        if factura.get("fecha"):
            try:
                fecha = datetime.fromisoformat(factura["fecha"].replace("Z", "+00:00"))
            except:
                pass
        
        # Crear InvoiceData
        invoice = InvoiceData(
            numero_factura=factura.get("numero", ""),
            fecha=fecha,
            ruc_emisor=emisor.get("ruc", ""),
            nombre_emisor=emisor.get("nombre", ""),
            ruc_cliente=receptor.get("ruc", ""),
            nombre_cliente=receptor.get("nombre", ""),
            email_cliente=receptor.get("email", ""),
            monto_total=montos.get("monto_total", 0),
            subtotal_exentas=montos.get("subtotal_exentas", 0),
            subtotal_5=montos.get("subtotal_5", 0),
            subtotal_10=montos.get("subtotal_10", 0),
            iva_5=montos.get("iva_5", 0),
            iva_10=montos.get("iva_10", 0),
            iva=montos.get("total_iva", 0)
        )
        
        # Agregar campos adicionales si están disponibles
        if "datos_tecnicos" in doc:
            datos_tec = doc["datos_tecnicos"]
            invoice.cdc = datos_tec.get("cdc", "")
            invoice.timbrado = datos_tec.get("timbrado", "")
        
        # Agregar metadata
        if "metadata" in doc:
            metadata = doc["metadata"]
            invoice.email_origen = metadata.get("email_origen", "")
            invoice.mes_proceso = doc.get("indices", {}).get("year_month", "")
        
        return invoice
        
    except Exception as e:
        logger.error(f"Error convirtiendo documento MongoDB: {e}")
        # Retornar InvoiceData mínimo en caso de error
        return InvoiceData(
            numero_factura=doc.get("factura_id", "ERROR"),
            fecha=datetime.now(),
            ruc_emisor="",
            nombre_emisor="Error en conversión",
            ruc_cliente="",
            nombre_cliente="",
            email_cliente="",
            monto_total=0
        )
