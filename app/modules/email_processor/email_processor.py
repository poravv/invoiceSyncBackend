import os
import re
import time
import threading
import logging
import schedule
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime

from app.config.settings import settings
from app.models.models import EmailConfig, MultiEmailConfig, InvoiceData, ProcessResult
from app.modules.openai_processor.openai_processor import OpenAIProcessor
from app.modules.excel_exporter.excel_exporter import ExcelExporter

from .imap_client import IMAPClient, decode_mime_header
from .link_extractor import extract_links_from_message
from .downloader import download_pdf_from_url
from .storage import save_binary, sanitize_filename, ensure_dirs

from .dedup import deduplicate_invoices

logger = logging.getLogger(__name__)


# =========================
#  MultiEmailProcessor
# =========================
class MultiEmailProcessor:
    """
    Orquestador multi-cuenta. Mantiene la funcionalidad anterior pero delega
    en EmailProcessor para cada cuenta. Incluye job programado.
    """
    def __init__(self, email_configs: List[MultiEmailConfig] = None):
        if email_configs is None:
            configs_data = settings.get_all_email_configs()
            self.email_configs = [MultiEmailConfig(**cfg) for cfg in configs_data if cfg.get("enabled", True)]
        else:
            self.email_configs = email_configs

        self.openai_processor = OpenAIProcessor()
        self.excel_exporter = ExcelExporter()

        ensure_dirs()

        self._job_running = False
        self._job_thread: Optional[threading.Thread] = None

        logger.info(f"MultiEmailProcessor inicializado con {len(self.email_configs)} cuentas de correo")

    def process_all(self) -> Dict[str, Any]:
        """Método breve para un endpoint /run-once (compat)."""
        res = self.process_all_emails()
        return res.dict() if hasattr(res, "dict") else {
            "success": res.success, "message": res.message, "invoice_count": res.invoice_count
        }

    def _remove_duplicate_invoices(self, invoices: List[InvoiceData]) -> List[InvoiceData]:
        return deduplicate_invoices(invoices)

    def process_all_emails(self) -> ProcessResult:
        all_invoices: List[InvoiceData] = []
        success_count = 0
        errors: List[str] = []
        excel_files: List[str] = []

        logger.info(f"Iniciando procesamiento de {len(self.email_configs)} cuentas de correo")

        for idx, cfg in enumerate(self.email_configs):
            logger.info(f"Procesando cuenta {idx + 1}/{len(self.email_configs)}: {cfg.username}")
            try:
                single = EmailProcessor(EmailConfig(
                    host=cfg.host, port=cfg.port, username=cfg.username, password=cfg.password,
                    search_criteria=cfg.search_criteria, search_terms=cfg.search_terms or settings.EMAIL_SEARCH_TERMS
                ))
                r = single.process_emails()
                if r.success:
                    success_count += 1
                    all_invoices.extend(r.invoices)
                    logger.info(f"Cuenta {cfg.username}: {r.invoice_count} facturas procesadas")
                else:
                    errors.append(f"Error en {cfg.username}: {r.message}")
                    logger.error(f"Error en cuenta {cfg.username}: {r.message}")
            except Exception as e:
                errors.append(f"Error en {cfg.username}: {str(e)}")
                logger.error(f"Error al procesar cuenta {cfg.username}: {str(e)}")

        if all_invoices:
            unique = self._remove_duplicate_invoices(all_invoices)
            logger.info(f"Facturas únicas después de eliminar duplicados: {len(unique)} (originales: {len(all_invoices)})")
            excel_path = self.excel_exporter.export_invoices(unique)
            if excel_path:
                excel_files.append(excel_path)
            all_invoices = unique

        if success_count == len(self.email_configs):
            message = f"Procesamiento exitoso de {len(self.email_configs)} cuentas. {len(all_invoices)} facturas encontradas."
        elif success_count > 0:
            message = f"Procesamiento parcial: {success_count}/{len(self.email_configs)} cuentas exitosas. {len(all_invoices)} facturas encontradas."
        else:
            message = f"Fallo en todas las cuentas. Errores: {'; '.join(errors)}"

        if excel_files:
            message += f" Archivos Excel generados: {len(excel_files)}"

        return ProcessResult(
            success=success_count > 0,
            message=message,
            invoice_count=len(all_invoices),
            invoices=all_invoices,
            excel_files=excel_files
        )

    # Scheduling para multi-cuenta
    def start(self):
        if self._job_running:
            logger.warning("El scheduler ya está en ejecución")
            return
        interval = settings.JOB_INTERVAL_MINUTES
        logger.info(f"Iniciando scheduler cada {interval} minutos")
        schedule.every(interval).minutes.do(self._run_job)
        self._job_running = True
        self._job_thread = threading.Thread(target=self._loop, daemon=True)
        self._job_thread.start()

    def stop(self):
        if not self._job_running:
            logger.warning("El scheduler no está en ejecución")
            return
        logger.info("Deteniendo scheduler")
        self._job_running = False
        schedule.clear()
        if self._job_thread and self._job_thread.is_alive():
            self._job_thread.join(timeout=2)

    def _loop(self):
        while self._job_running:
            schedule.run_pending()
            time.sleep(1)

    def _run_job(self):
        logger.info("Ejecutando job programado para procesar múltiples correos")
        res = self.process_all_emails()
        (logger.info if res.success else logger.error)(res.message)
        return res


# =========================
#  EmailProcessor (single)
# =========================
class EmailProcessor:
    """
    Procesador para una sola cuenta.
    Separa responsabilidades: IMAP, parseo, guardado adjuntos, links, descarga y envío a OpenAI/Excel.
    """
    def __init__(self, config: EmailConfig = None):
        if config is None:
            data = settings.get_all_email_configs() or []
            if data:
                first = data[0]
                self.config = EmailConfig(
                    host=first["host"], port=first["port"], username=first["username"], password=first["password"],
                    search_criteria=first.get("search_criteria", "UNSEEN"),
                    search_terms=first.get("search_terms", settings.EMAIL_SEARCH_TERMS)
                )
            else:
                # Fallback legacy
                self.config = EmailConfig(
                    host=settings.EMAIL_HOST, port=settings.EMAIL_PORT,
                    username=settings.EMAIL_USERNAME, password=settings.EMAIL_PASSWORD,
                    search_criteria=settings.EMAIL_SEARCH_CRITERIA,
                    search_terms=settings.EMAIL_SEARCH_TERMS
                )
        else:
            self.config = config

        self.client = IMAPClient(
            host=self.config.host, port=self.config.port,
            username=self.config.username, password=self.config.password, mailbox="INBOX"
        )
        self.openai_processor = OpenAIProcessor()
        self.excel_exporter = ExcelExporter()

        ensure_dirs()

    # --------- IMAP high-level ---------
    def connect(self) -> bool:
        return self.client.connect()

    def disconnect(self):
        self.client.close()

    # --------- Search logic ---------
    def search_emails(self) -> List[str]:
        if not self.client.conn:
            if not self.connect():
                return []
        base = self.config.search_criteria.split() if self.config.search_criteria else []
        terms = self.config.search_terms or []
        if not terms:
            return self.client.search(*base)

        if len(terms) == 1:
            query = base + ["SUBJECT", f'"{terms[0]}"']
            return self.client.search(*query)

        # múltiples términos → OR manual: uniones de resultados por término
        found = set()
        for t in terms:
            q = base + ["SUBJECT", f'"{t}"']
            ids = self.client.search(*q)
            found.update(ids)
        ids_list = list(found)
        logger.info(f"Se encontraron {len(ids_list)} correos combinando términos: {terms}")
        return ids_list

    # --------- Fetch + parse ---------
    def get_email_content(self, email_id: str) -> Tuple[dict, list]:
        """
        Extrae subject/sender/date + adjuntos PDF/XML y links candidatos.
        """
        if not self.client.conn and not self.connect():
            return {}, []
        message = self.client.fetch_message(email_id)
        if not message:
            return {}, []

        subject = decode_mime_header(message.get("Subject", ""))
        sender = decode_mime_header(message.get("From", ""))
        date_str = message.get("Date", "")

        dt = None
        if date_str:
            import email as pyemail
            try:
                dt = pyemail.utils.parsedate_to_datetime(date_str)
            except Exception as e:
                logger.warning(f"⚠️ Error al parsear fecha '{date_str}': {e}")

        meta = {"subject": subject, "sender": sender, "date": dt, "message_id": email_id}
        attachments = []
        links = extract_links_from_message(message)

        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = part.get_filename()
            if not filename:
                continue
            filename = decode_mime_header(filename).strip()
            ctype = (part.get_content_type() or "").lower()
            content = part.get_payload(decode=True)

            is_pdf = filename.lower().endswith(".pdf") or ctype == "application/pdf"
            is_xml = filename.lower().endswith(".xml") or ctype in (
                "text/xml", "application/xml", "application/x-iso20022+xml", "application/x-invoice+xml"
            )
            if is_pdf or is_xml:
                logger.info(f"📎 Adjunto detectado: {filename} ({ctype})")
                attachments.append({
                    "filename": filename,
                    "content": content,
                    "content_type": ctype
                })

        meta["links"] = links
        logger.info(f"📬 Correo {email_id} - Asunto: '{subject}' - Adjuntos: {len(attachments)} - Enlaces: {len(links)}")
        return meta, attachments

    # --------- Core processing ---------
    def process_emails(self) -> ProcessResult:
        result = ProcessResult(success=True, message="Procesamiento completado", invoice_count=0, invoices=[], excel_files=[])
        try:
            if not self.connect():
                return ProcessResult(success=False, message="Error al conectar al servidor de correo")

            email_ids = self.search_emails()
            if not email_ids:
                self.disconnect()
                return ProcessResult(success=True, message="No se encontraron correos con facturas", invoice_count=0)

            logger.info(f"Procesando {len(email_ids)} correos")

            for eid in email_ids:
                try:
                    metadata, attachments = self.get_email_content(eid)
                    if not metadata:
                        logger.warning(f"⚠️ No se pudo obtener metadatos del correo {eid}")
                        continue

                    email_meta_for_ai = {
                        "sender": metadata.get("sender", ""),
                        "subject": metadata.get("subject", ""),
                        "date": metadata.get("date")
                    }

                    xml_path = None
                    pdf_path = None
                    processed = False

                    # Adjuntos: XML prioridad, luego PDF
                    for att in attachments:
                        fname = (att.get("filename") or "").lower()
                        ctype = (att.get("content_type") or "").lower()
                        content = att.get("content") or b""
                        is_pdf = fname.endswith(".pdf") or ctype == "application/pdf"
                        is_xml = fname.endswith(".xml") or ctype in (
                            "text/xml", "application/xml", "application/x-iso20022+xml", "application/x-invoice+xml"
                        )
                        if is_xml:
                            xml_path = save_binary(content, fname)  # no forzamos .pdf
                            logger.info(f"📄 XML adjunto detectado: {fname}")
                        elif is_pdf:
                            pdf_path = save_binary(content, fname, force_pdf=True)
                            logger.info(f"📄 PDF adjunto detectado: {fname}")

                    # XML primero
                    if xml_path:
                        logger.info("📄 Procesando XML adjunto como fuente principal")
                        inv = self.openai_processor.extract_invoice_data_from_xml(xml_path, email_meta_for_ai)
                        if inv:
                            result.invoices.append(inv)
                            result.invoice_count += 1
                            processed = True

                    # PDF si no hubo XML válido
                    elif pdf_path:
                        logger.info("📄 Procesando PDF porque no se encontró XML")
                        inv = self.openai_processor.extract_invoice_data(pdf_path, email_meta_for_ai)
                        if inv:
                            result.invoices.append(inv)
                            result.invoice_count += 1
                            processed = True

                    # Enlaces si nada funcionó
                    if not processed and metadata.get("links"):
                        logger.info(f"🔗 Procesando {len(metadata['links'])} enlaces encontrados")
                        for link in metadata["links"]:
                            logger.info(f"🔗 Intentando procesar enlace: {link}")
                            downloaded_path = download_pdf_from_url(link)
                            if not downloaded_path:
                                logger.warning(f"❌ No se pudo descargar desde el enlace: {link}")
                                continue

                            low = downloaded_path.lower()
                            inv = None
                            if low.endswith(".xml") and "factura" in low:
                                logger.info("📄 XML detectado desde enlace, procesando como factura electrónica")
                                inv = self.openai_processor.extract_invoice_data_from_xml(downloaded_path)
                            elif low.endswith(".pdf"):
                                logger.info("📄 PDF detectado desde enlace, procesando con OpenAI")
                                inv = self.openai_processor.extract_invoice_data(downloaded_path, email_meta_for_ai)
                            else:
                                logger.warning(f"⚠️ Tipo de archivo no reconocido: {downloaded_path}")
                                continue

                            if inv:
                                result.invoices.append(inv)
                                result.invoice_count += 1
                                processed = True

                    # Marcar leído si hubo procesamiento OK
                    if processed:
                        self.client.mark_seen(eid)
                    else:
                        logger.warning(f"⚠️ Ninguna factura procesada del correo {eid}, no se marcará como leído.")

                except Exception as e:
                    logger.error(f"❌ Error al procesar el correo {eid}: {str(e)}")
                    continue

            if result.invoices:
                excel_path = self.excel_exporter.export_invoices(result.invoices)
                if excel_path:
                    result.excel_files = [excel_path]
                    result.message = f"Se procesaron {result.invoice_count} facturas. Archivo Excel: {excel_path}"
                else:
                    result.message = f"Se procesaron {result.invoice_count} facturas, pero hubo un error al exportar a Excel"

            self.disconnect()
            return result

        except Exception as e:
            logger.error(f"Error general en el procesamiento: {str(e)}")
            self.disconnect()
            return ProcessResult(success=False, message=f"Error en el procesamiento: {str(e)}")

    # ------------- (Opcional) scheduler single -------------
    def start_scheduled_job(self):
        """
        Conservamos esta API por compatibilidad, pero recomendamos usar MultiEmailProcessor.start().
        """
        if getattr(self, "_job_running", False):
            logger.warning("El job ya está en ejecución")
            return
        interval = settings.JOB_INTERVAL_MINUTES
        logger.info(f"Iniciando job programado para ejecutarse cada {interval} minutos")
        schedule.every(interval).minutes.do(self._run_job)
        self._job_running = True
        self._job_thread = threading.Thread(target=self._schedule_loop, daemon=True)
        self._job_thread.start()

    def stop_scheduled_job(self):
        if not getattr(self, "_job_running", False):
            logger.warning("El job no está en ejecución")
            return
        logger.info("Deteniendo job programado")
        self._job_running = False
        schedule.clear()
        if getattr(self, "_job_thread", None) and self._job_thread.is_alive():
            self._job_thread.join(timeout=2)

    def _schedule_loop(self):
        while getattr(self, "_job_running", False):
            schedule.run_pending()
            time.sleep(1)

    def _run_job(self):
        logger.info("Ejecutando job programado para procesar correos")
        res = self.process_emails()
        (logger.info if res.success else logger.error)(res.message)
        return res