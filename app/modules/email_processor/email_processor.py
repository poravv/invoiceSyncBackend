import imaplib
import email
import os
import logging
import time
import schedule
import threading
from email.header import decode_header
from typing import List, Tuple, Optional, Dict, Any
import re
from datetime import datetime

from app.config.settings import settings
from app.models.models import EmailConfig, InvoiceData, ProcessResult
from app.modules.openai_processor.openai_processor import OpenAIProcessor
from app.modules.excel_exporter.excel_exporter import ExcelExporter

logger = logging.getLogger(__name__)

class EmailProcessor:
    def __init__(self, config: EmailConfig = None):
        """
        Inicializa el procesador de correos.
        
        Args:
            config: Configuración para la conexión al correo. Si no se proporciona,
                  se utilizan los valores de las variables de entorno.
        """
        if config is None:
            self.config = EmailConfig(
                host=settings.EMAIL_HOST,
                port=settings.EMAIL_PORT,
                username=settings.EMAIL_USERNAME,
                password=settings.EMAIL_PASSWORD,
                search_criteria=settings.EMAIL_SEARCH_CRITERIA,
                search_terms=settings.EMAIL_SEARCH_TERMS
            )
        else:
            self.config = config
        
        self.conn = None
        self.openai_processor = OpenAIProcessor()
        self.excel_exporter = ExcelExporter()
        
        # Crear directorios necesarios
        os.makedirs(settings.TEMP_PDF_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(settings.EXCEL_OUTPUT_PATH), exist_ok=True)
        
        # Control para job programado
        self._job_running = False
        self._job_thread = None
    
    def connect(self) -> bool:
        """
        Establece la conexión con el servidor de correo.
        
        Returns:
            bool: True si la conexión fue exitosa, False en caso contrario.
        """
        try:
            # Crear conexión
            logger.info(f"host {self.config.host}")
            logger.info(f"port {self.config.port}")
            logger.info(f"username {self.config.username}")
            self.conn = imaplib.IMAP4_SSL(self.config.host, self.config.port)
            
            # Iniciar sesión
            self.conn.login(self.config.username, self.config.password)
            
            # Seleccionar bandeja de entrada
            self.conn.select("INBOX")
            
            logger.info(f"Conexión exitosa al correo {self.config.username}")
            return True
            
        except Exception as e:
            logger.error(f"Error al conectar al correo: {str(e)}")
            return False
    
    def disconnect(self):
        """Cierra la conexión con el servidor de correo."""
        if self.conn:
            try:
                self.conn.close()
                self.conn.logout()
                logger.info("Desconexión exitosa del servidor de correo")
            except Exception as e:
                logger.error(f"Error al desconectar del servidor de correo: {str(e)}")
    
    def search_emails(self) -> List[str]:
        """
        Busca correos según los criterios configurados.
        
        Returns:
            List[str]: Lista de IDs de correos encontrados.
        """
        if not self.conn:
            if not self.connect():
                return []

        try:
            base_criteria = self.config.search_criteria.split() if self.config.search_criteria else []

            if self.config.search_terms:
                terms = self.config.search_terms

                if len(terms) == 1:
                    # Caso simple: un solo término
                    search_query = base_criteria + ["SUBJECT", f'"{terms[0]}"']
                    logger.debug(f"IMAP search query: {search_query}")
                    status, messages = self.conn.search(None, *search_query)
                else:
                    # Si hay múltiples términos, hacemos búsquedas separadas y combinamos los resultados
                    email_ids_set = set()
                    for term in terms:
                        term_query = base_criteria + ["SUBJECT", f'"{term}"']
                        logger.debug(f"IMAP search query (término '{term}'): {term_query}")
                        status, messages = self.conn.search(None, *term_query)

                        if status == "OK":
                            ids = messages[0].split()
                            email_ids_set.update(ids)
                        else:
                            logger.warning(f"No se pudo obtener resultados para término '{term}': {status}")

                    email_ids = list(email_ids_set)
                    logger.info(f"Se encontraron {len(email_ids)} correos combinando términos: {terms}")
                    return [eid.decode() for eid in email_ids]

            else:
                # Si no hay términos definidos, usar solo los criterios base
                logger.debug(f"IMAP search query: {base_criteria}")
                status, messages = self.conn.search(None, *base_criteria)

            if status != "OK":
                logger.error(f"Error en la búsqueda de correos: {status}")
                return []

            email_ids = messages[0].split()
            logger.info(f"Se encontraron {len(email_ids)} correos que coinciden con los criterios")
            return [eid.decode() for eid in email_ids]

        except Exception as e:
            logger.error(f"Error al buscar correos: {str(e)}")
            return []


    
    def get_email_content(self, email_id: str) -> Tuple[dict, list]:
        """
        Obtiene el contenido de un correo específico.
        
        Args:
            email_id: ID del correo a obtener.
            
        Returns:
            Tuple: (metadata, attachments)
                - metadata: Diccionario con asunto, remitente, fecha
                - attachments: Lista de adjuntos con nombre y contenido
        """
        if not self.conn:
            if not self.connect():
                return {}, []
        
        try:
            # Obtener el mensaje completo
            status, data = self.conn.fetch(email_id, "(RFC822)")
            
            if status != "OK":
                logger.error(f"Error al obtener el correo {email_id}: {status}")
                return {}, []
            
            # Analizar el mensaje
            message = email.message_from_bytes(data[0][1])
            
            # Extraer metadata
            subject = self._decode_email_header(message.get("Subject", ""))
            sender = self._decode_email_header(message.get("From", ""))
            date_str = message.get("Date", "")
            
            # Convertir fecha a formato datetime
            date = None
            if date_str:
                try:
                    date = email.utils.parsedate_to_datetime(date_str)
                except Exception as e:
                    logger.warning(f"Error al parsear la fecha '{date_str}': {str(e)}")
            
            metadata = {
                "subject": subject,
                "sender": sender,
                "date": date,
                "message_id": email_id
            }
            
            # Buscar adjuntos y enlaces
            attachments = []
            links = self._extract_links_from_email(message)
            
            # Procesar adjuntos
            for part in message.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                
                filename = part.get_filename()
                if filename:
                    # Decodificar nombre de archivo si es necesario
                    filename = self._decode_email_header(filename)
                    
                    # Verificar si es un PDF
                    if filename.lower().endswith(".pdf"):
                        content = part.get_payload(decode=True)
                        attachments.append({
                            "filename": filename,
                            "content": content,
                            "content_type": part.get_content_type()
                        })
            
            logger.info(f"Correo {email_id} procesado: {subject} - {len(attachments)} adjuntos, {len(links)} enlaces")
            
            # Incluir los enlaces encontrados en los metadatos
            metadata["links"] = links
            
            return metadata, attachments
            
        except Exception as e:
            logger.error(f"Error al procesar el correo {email_id}: {str(e)}")
            return {}, []
    
    def _decode_email_header(self, header: str) -> str:
        """
        Decodifica encabezados de correo que pueden estar codificados.
        
        Args:
            header: Encabezado a decodificar.
            
        Returns:
            str: Encabezado decodificado.
        """
        if not header:
            return ""
        
        try:
            decoded_parts = []
            for part, encoding in decode_header(header):
                if isinstance(part, bytes):
                    if encoding:
                        decoded_part = part.decode(encoding)
                    else:
                        decoded_part = part.decode('utf-8', errors='replace')
                else:
                    decoded_part = part
                
                decoded_parts.append(str(decoded_part))
            
            return "".join(decoded_parts)
            
        except Exception as e:
            logger.warning(f"Error al decodificar encabezado '{header}': {str(e)}")
            return header
    
    def _extract_links_from_email(self, message) -> List[str]:
        """
        Extrae enlaces de un mensaje de correo, buscando PDFs directos y facturas electrónicas.
        
        Args:
            message: Mensaje de correo electrónico.
            
        Returns:
            List[str]: Lista de enlaces encontrados.
        """
        links = []
        
        # Patrones para buscar diferentes tipos de enlaces
        pdf_url_pattern = r'https?://[^\s<>"]+\.pdf'
        siga_pattern = r'https?://facte\.siga\.com\.py/[^\s<>"]*'
        
        # Buscar en partes HTML y de texto
        for part in message.walk():
            content_type = part.get_content_type()
            
            if content_type == "text/plain" or content_type == "text/html":
                try:
                    # Obtener el contenido y decodificarlo
                    content = part.get_payload(decode=True)
                    charset = part.get_content_charset()
                    
                    if charset:
                        content = content.decode(charset, errors='replace')
                    else:
                        content = content.decode('utf-8', errors='replace')
                    
                    # Buscar enlaces a PDFs directos
                    pdf_links = re.findall(pdf_url_pattern, content)
                    links.extend(pdf_links)
                    
                    # Buscar enlaces de facturas electrónicas SIGA
                    siga_links = re.findall(siga_pattern, content)
                    links.extend(siga_links)
                    
                    # Si es contenido HTML, buscar enlaces adicionales usando BeautifulSoup
                    if content_type == "text/html":
                        try:
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(content, 'html.parser')
                            
                            # Palabras clave para identificar enlaces de facturas
                            factura_keywords = [
                                'visualizar documento', 'ver factura', 'descargar factura', 
                                'factura electronica', 'factura electrónica', 'visualizar',
                                'descargar xml', 'ver documento'
                            ]
                            
                            # Buscar enlaces <a> con texto relacionado a facturas
                            for a_tag in soup.find_all('a', href=True):
                                link_text = a_tag.get_text().lower().strip()
                                href = a_tag['href']
                                
                                # Verificar si el texto del enlace contiene palabras clave de factura
                                if any(keyword in link_text for keyword in factura_keywords):
                                    # Asegurarse de que sea una URL completa
                                    if href.startswith('http'):
                                        links.append(href)
                                        logger.info(f"Encontrado enlace de factura: {href} (texto: '{link_text}')")
                                    
                        except ImportError:
                            logger.warning("BeautifulSoup no está disponible. Solo se buscarán patrones de texto.")
                        except Exception as e:
                            logger.warning(f"Error al procesar HTML con BeautifulSoup: {str(e)}")
                    
                except Exception as e:
                    logger.warning(f"Error al extraer enlaces: {str(e)}")
        
        # Eliminar duplicados
        unique_links = list(set(links))
        if unique_links:
            logger.info(f"Enlaces encontrados: {unique_links}")
        
        return unique_links
    
    def save_pdf_from_binary(self, content: bytes, filename: str) -> str:
        """
        Guarda el contenido binario de un PDF en un archivo.
        
        Args:
            content: Contenido binario del PDF.
            filename: Nombre del archivo.
            
        Returns:
            str: Ruta al archivo guardado o cadena vacía en caso de error.
        """
        try:
            # Crear el directorio si no existe
            os.makedirs(settings.TEMP_PDF_DIR, exist_ok=True)
            
            # Generar un nombre único para evitar colisiones
            safe_filename = re.sub(r'[^\w\-_\. ]', '_', filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            unique_filename = f"{timestamp}_{safe_filename}"
            
            # Ruta completa del archivo
            file_path = os.path.join(settings.TEMP_PDF_DIR, unique_filename)
            
            # Guardar el archivo
            with open(file_path, "wb") as f:
                f.write(content)
            
            logger.info(f"PDF guardado: {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error al guardar PDF {filename}: {str(e)}")
            return ""
    
    def download_pdf_from_url(self, url: str) -> str:
        """
        Descarga un PDF desde una URL, manejando tanto PDFs directos como sistemas de facturación.
        
        Args:
            url: URL del PDF o página de factura.
            
        Returns:
            str: Ruta al archivo descargado o cadena vacía en caso de error.
        """
        try:
            import requests
            from urllib.parse import urlparse
            
            logger.info(f"Intentando descargar desde: {url}")
            
            # Headers para simular un navegador real
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            # Realizar la solicitud HTTP
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            
            if response.status_code != 200:
                logger.error(f"Error al acceder a {url}: Código {response.status_code}")
                return ""
            
            content_type = response.headers.get("Content-Type", "").lower()
            logger.info(f"Tipo de contenido recibido: {content_type}")
            
            # Si es un PDF directo
            if content_type.startswith("application/pdf"):
                logger.info("PDF directo detectado, guardando...")
                filename = self._generate_filename_from_url(url, "pdf")
                return self.save_pdf_from_binary(response.content, filename)
            
            # Si es HTML (página de factura), buscar enlaces de descarga de PDF
            elif content_type.startswith("text/html"):
                logger.info("Página HTML detectada, buscando enlaces de descarga PDF...")
                return self._extract_pdf_from_html_page(response.text, url, headers)
            
            else:
                logger.warning(f"Tipo de contenido no soportado: {content_type}")
                return ""
            
        except Exception as e:
            logger.error(f"Error al descargar PDF desde {url}: {str(e)}")
            return ""

    def _generate_filename_from_url(self, url: str, extension: str) -> str:
        """
        Genera un nombre de archivo único basado en la URL.
        
        Args:
            url: URL del archivo.
            extension: Extensión del archivo.
            
        Returns:
            str: Nombre de archivo único.
        """
        timestamp = int(time.time())
        
        # Intentar extraer información útil de la URL
        if "facte.siga.com.py" in url:
            # Extraer RUC y CDC si están en la URL de SIGA
            import re
            ruc_match = re.search(r'ruc=([^&]+)', url)
            cdc_match = re.search(r'cdc=([^&]+)', url)
            
            if ruc_match and cdc_match:
                ruc = ruc_match.group(1)
                cdc = cdc_match.group(1)[:10]  # Primeros 10 caracteres del CDC
                return f"factura_siga_{ruc}_{cdc}_{timestamp}.{extension}"
        
        return f"factura_{timestamp}.{extension}"

    def _extract_pdf_from_html_page(self, html_content: str, base_url: str, headers: dict) -> str:
        """
        Extrae PDF de una página HTML de factura electrónica.
        
        Args:
            html_content: Contenido HTML de la página.
            base_url: URL base para resolver enlaces relativos.
            headers: Headers HTTP para las solicitudes.
            
        Returns:
            str: Ruta al PDF descargado o cadena vacía si no se encuentra.
        """
        try:
            from bs4 import BeautifulSoup
            from urllib.parse import urljoin
            import requests
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Buscar enlaces de descarga de PDF en la página
            pdf_keywords = [
                'descargar', 'pdf', 'imprimir', 'download', 'print',
                'generar pdf', 'exportar pdf', 'ver pdf'
            ]
            
            logger.info("Buscando enlaces de descarga PDF en la página HTML...")
            
            # Buscar enlaces <a> con texto o atributos que indiquen descarga de PDF
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                link_text = a_tag.get_text().lower().strip()
                
                # Verificar si el enlace contiene palabras clave de PDF
                is_pdf_link = (
                    any(keyword in link_text for keyword in pdf_keywords) or
                    href.lower().endswith('.pdf') or
                    'pdf' in href.lower()
                )
                
                if is_pdf_link:
                    full_url = urljoin(base_url, href)
                    logger.info(f"Encontrado posible enlace PDF: {full_url} (texto: '{link_text}')")
                    
                    # Intentar descargar este enlace como PDF
                    try:
                        pdf_response = requests.get(full_url, headers=headers, timeout=30, allow_redirects=True)
                        
                        if pdf_response.status_code == 200:
                            response_content_type = pdf_response.headers.get("Content-Type", "").lower()
                            
                            if response_content_type.startswith("application/pdf"):
                                logger.info(f"PDF encontrado y descargado desde: {full_url}")
                                filename = self._generate_filename_from_url(full_url, "pdf")
                                return self.save_pdf_from_binary(pdf_response.content, filename)
                            else:
                                logger.debug(f"El enlace no devolvió un PDF: {response_content_type}")
                        else:
                            logger.debug(f"Error al acceder al enlace: {pdf_response.status_code}")
                            
                    except Exception as e:
                        logger.debug(f"Error al intentar descargar desde {full_url}: {str(e)}")
                        continue
            
            # Si no encontramos enlaces específicos, buscar formularios o scripts que puedan generar PDFs
            logger.info("No se encontraron enlaces directos, buscando formularios...")
            
            for form in soup.find_all('form'):
                action = form.get('action', '')
                if 'pdf' in action.lower() or 'print' in action.lower():
                    logger.info(f"Encontrado formulario que puede generar PDF: {action}")
                    # Aquí podrías implementar lógica para enviar el formulario si es necesario
            
            logger.warning(f"No se encontró enlace de descarga PDF en la página: {base_url}")
            return ""
            
        except ImportError:
            logger.error("BeautifulSoup no está disponible. No se puede procesar páginas HTML.")
            return ""
        except Exception as e:
            logger.error(f"Error al extraer PDF de página HTML: {str(e)}")
            return ""
    
    def mark_as_read(self, email_id: str) -> bool:
        """
        Marca un correo como leído.
        
        Args:
            email_id: ID del correo a marcar.
            
        Returns:
            bool: True si se marcó correctamente, False en caso contrario.
        """
        if not self.conn:
            if not self.connect():
                return False
        
        try:
            self.conn.store(email_id, '+FLAGS', '\\Seen')
            logger.info(f"Correo {email_id} marcado como leído")
            return True
        except Exception as e:
            logger.error(f"Error al marcar el correo {email_id} como leído: {str(e)}")
            return False
    
    def process_emails(self) -> ProcessResult:
        """
        Procesa correos electrónicos para extraer facturas.
        
        Returns:
            ProcessResult: Resultado del procesamiento.
        """
        # Resultado por defecto
        result = ProcessResult(
            success=True,
            message="Procesamiento completado",
            invoice_count=0,
            invoices=[]
        )
        
        try:
            # Conectar al servidor de correo
            if not self.connect():
                return ProcessResult(
                    success=False,
                    message="Error al conectar al servidor de correo"
                )
            
            # Buscar correos con facturas
            email_ids = self.search_emails()
            
            if not email_ids:
                self.disconnect()
                return ProcessResult(
                    success=True,
                    message="No se encontraron correos con facturas",
                    invoice_count=0
                )
            
            logger.info(f"Procesando {len(email_ids)} correos")
            
            # Procesar cada correo
            for email_id in email_ids:
                try:
                    # Obtener contenido del correo
                    metadata, attachments = self.get_email_content(email_id)
                    
                    if not metadata:
                        logger.warning(f"No se pudo obtener metadatos del correo {email_id}")
                        continue
                    
                    # Procesar PDFs adjuntos y enlaces
                    processed_pdfs = []
                    
                    # 1. Procesar adjuntos directos
                    for attachment in attachments:
                        if attachment.get("filename", "").lower().endswith(".pdf"):
                            # Guardar el PDF
                            pdf_path = self.save_pdf_from_binary(
                                attachment["content"],
                                attachment["filename"]
                            )
                            
                            if pdf_path:
                                processed_pdfs.append({
                                    "path": pdf_path,
                                    "source": "attachment"
                                })
                    
                    # 2. Procesar enlaces a PDFs y facturas electrónicas
                    if "links" in metadata and metadata["links"]:
                        logger.info(f"Procesando {len(metadata['links'])} enlaces encontrados")
                        
                        for link in metadata["links"]:
                            logger.info(f"Intentando procesar enlace: {link}")
                            
                            # Intentar descargar desde cualquier enlace (no solo los que terminan en .pdf)
                            pdf_path = self.download_pdf_from_url(link)
                            
                            if pdf_path:
                                logger.info(f"PDF descargado exitosamente desde: {link}")
                                processed_pdfs.append({
                                    "path": pdf_path,
                                    "source": "link",
                                    "original_url": link
                                })
                            else:
                                logger.warning(f"No se pudo descargar PDF desde: {link}")
                    
                    # Procesar cada PDF encontrado con OpenAI
                    for pdf_info in processed_pdfs:
                        pdf_path = pdf_info["path"]
                        
                        # Preparar metadatos para el procesador de OpenAI
                        email_meta_for_ai = {
                            "sender": metadata.get("sender", ""),
                            "subject": metadata.get("subject", ""),
                            "date": metadata.get("date")
                        }
                        
                        # Extraer datos con OpenAI
                        invoice_data = self.openai_processor.extract_invoice_data(pdf_path, email_meta_for_ai)
                        
                        # Agregar a la lista de facturas procesadas
                        result.invoices.append(invoice_data)
                        result.invoice_count += 1
                    
                    # Marcar correo como leído
                    self.mark_as_read(email_id)
                    
                except Exception as e:
                    logger.error(f"Error al procesar el correo {email_id}: {str(e)}")
                    continue
            
            # Exportar a Excel si hay facturas
            if result.invoices:
                excel_path = self.excel_exporter.export_invoices(result.invoices)
                if excel_path:
                    result.message = f"Se procesaron {result.invoice_count} facturas. Archivo Excel: {excel_path}"
                else:
                    result.message = f"Se procesaron {result.invoice_count} facturas, pero hubo un error al exportar a Excel"
            
            # Desconectar del servidor de correo
            self.disconnect()
            
            return result
            
        except Exception as e:
            logger.error(f"Error general en el procesamiento: {str(e)}")
            self.disconnect()
            return ProcessResult(
                success=False,
                message=f"Error en el procesamiento: {str(e)}"
            )
    
    def start_scheduled_job(self):
        """
        Inicia el trabajo programado para ejecutarse periódicamente.
        """
        if self._job_running:
            logger.warning("El job ya está en ejecución")
            return
        
        interval_minutes = settings.JOB_INTERVAL_MINUTES
        logger.info(f"Iniciando job programado para ejecutarse cada {interval_minutes} minutos")
        
        # Programar la tarea
        schedule.every(interval_minutes).minutes.do(self._run_job)
        
        # Iniciar el thread para el scheduler
        self._job_running = True
        self._job_thread = threading.Thread(target=self._schedule_loop)
        self._job_thread.daemon = True
        self._job_thread.start()
    
    def stop_scheduled_job(self):
        """
        Detiene el trabajo programado.
        """
        if not self._job_running:
            logger.warning("El job no está en ejecución")
            return
        
        logger.info("Deteniendo job programado")
        self._job_running = False
        
        # Esperar a que el thread termine
        if self._job_thread and self._job_thread.is_alive():
            self._job_thread.join(timeout=2)
        
        # Limpiar todas las tareas programadas
        schedule.clear()
    
    def _schedule_loop(self):
        """
        Bucle para ejecutar las tareas programadas.
        """
        while self._job_running:
            schedule.run_pending()
            time.sleep(1)
    
    def _run_job(self):
        """
        Ejecuta el trabajo programado.
        """
        logger.info("Ejecutando job programado para procesar correos")
        result = self.process_emails()
        
        if result.success:
            logger.info(result.message)
        else:
            logger.error(result.message)
        
        return result
