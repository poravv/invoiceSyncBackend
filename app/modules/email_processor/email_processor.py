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
from app.models.models import EmailConfig, MultiEmailConfig, InvoiceData, ProcessResult
from app.modules.openai_processor.openai_processor import OpenAIProcessor
from app.modules.excel_exporter.excel_exporter import ExcelExporter

logger = logging.getLogger(__name__)

class MultiEmailProcessor:
    def __init__(self, email_configs: List[MultiEmailConfig] = None):
        """
        Inicializa el procesador de múltiples correos.
        
        Args:
            email_configs: Lista de configuraciones de correo. Si no se proporciona,
                         se utilizan los valores de las variables de entorno.
        """
        if email_configs is None:
            # Cargar configuraciones desde settings
            configs_data = settings.get_all_email_configs()
            self.email_configs = [MultiEmailConfig(**config) for config in configs_data if config.get('enabled', True)]
        else:
            self.email_configs = email_configs
        
        self.openai_processor = OpenAIProcessor()
        self.excel_exporter = ExcelExporter()
        
        # Crear directorios necesarios
        os.makedirs(settings.TEMP_PDF_DIR, exist_ok=True)
        os.makedirs(settings.EXCEL_OUTPUT_DIR, exist_ok=True)
        
        # Control para job programado
        self._job_running = False
        self._job_thread = None
        
        logger.info(f"MultiEmailProcessor inicializado con {len(self.email_configs)} cuentas de correo")
    
    def _remove_duplicate_invoices(self, invoices: List[InvoiceData]) -> List[InvoiceData]:
        """
        Elimina facturas duplicadas basándose en el CDC.
        
        Args:
            invoices: Lista de facturas que puede contener duplicados
            
        Returns:
            Lista de facturas sin duplicados
        """
        seen_cdcs = set()
        unique_invoices = []
        
        for invoice in invoices:
            # Usar CDC como identificador único
            cdc = getattr(invoice, 'cdc', '') or ''
            
            # Si no tiene CDC, usar una combinación de campos como identificador
            if not cdc:
                identifier = f"{getattr(invoice, 'numero_factura', '')}-{getattr(invoice, 'ruc_emisor', '')}-{getattr(invoice, 'monto_total', 0)}"
            else:
                identifier = cdc
            
            if identifier and identifier not in seen_cdcs:
                seen_cdcs.add(identifier)
                unique_invoices.append(invoice)
                logger.debug(f"Factura única agregada: {getattr(invoice, 'numero_factura', 'N/A')} - CDC: {cdc}")
            else:
                logger.warning(f"Factura duplicada omitida: {getattr(invoice, 'numero_factura', 'N/A')} - CDC: {cdc}")
        
        return unique_invoices
    
    def process_all_emails(self) -> ProcessResult:
        """
        Procesa correos de todas las cuentas configuradas.
        
        Returns:
            ProcessResult: Resultado consolidado del procesamiento.
        """
        all_invoices = []
        success_count = 0
        error_messages = []
        excel_files = []
        
        logger.info(f"Iniciando procesamiento de {len(self.email_configs)} cuentas de correo")
        
        for i, email_config in enumerate(self.email_configs):
            logger.info(f"Procesando cuenta {i+1}/{len(self.email_configs)}: {email_config.username}")
            
            try:
                # Crear procesador individual para esta cuenta
                single_processor = EmailProcessor(EmailConfig(
                    host=email_config.host,
                    port=email_config.port,
                    username=email_config.username,
                    password=email_config.password,
                    search_criteria=email_config.search_criteria,
                    search_terms=email_config.search_terms if email_config.search_terms else settings.EMAIL_SEARCH_TERMS
                ))
                
                # Procesar correos de esta cuenta
                result = single_processor.process_emails()
                
                if result.success:
                    success_count += 1
                    all_invoices.extend(result.invoices)
                    logger.info(f"Cuenta {email_config.username}: {result.invoice_count} facturas procesadas")
                else:
                    error_messages.append(f"Error en {email_config.username}: {result.message}")
                    logger.error(f"Error en cuenta {email_config.username}: {result.message}")
                
            except Exception as e:
                error_messages.append(f"Error en {email_config.username}: {str(e)}")
                logger.error(f"Error al procesar cuenta {email_config.username}: {str(e)}")
        
        # Eliminar duplicados por CDC antes de exportar
        if all_invoices:
            unique_invoices = self._remove_duplicate_invoices(all_invoices)
            logger.info(f"Facturas únicas después de eliminar duplicados: {len(unique_invoices)} (originales: {len(all_invoices)})")
            
            excel_path = self.excel_exporter.export_invoices(unique_invoices)
            if excel_path:
                excel_files.append(excel_path)
            
            # Actualizar all_invoices para el mensaje de resultado
            all_invoices = unique_invoices
        
        # Crear mensaje de resultado
        if success_count == len(self.email_configs):
            message = f"Procesamiento exitoso de {len(self.email_configs)} cuentas. {len(all_invoices)} facturas encontradas."
        elif success_count > 0:
            message = f"Procesamiento parcial: {success_count}/{len(self.email_configs)} cuentas exitosas. {len(all_invoices)} facturas encontradas."
        else:
            message = f"Fallo en todas las cuentas. Errores: {'; '.join(error_messages)}"
        
        if excel_files:
            message += f" Archivos Excel generados: {len(excel_files)}"
        
        return ProcessResult(
            success=success_count > 0,
            message=message,
            invoice_count=len(all_invoices),
            invoices=all_invoices,
            excel_files=excel_files
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
        logger.info("Ejecutando job programado para procesar múltiples correos")
        result = self.process_all_emails()
        
        if result.success:
            logger.info(result.message)
        else:
            logger.error(result.message)
        
        return result

class EmailProcessor:
    def __init__(self, config: EmailConfig = None):
        """
        Inicializa el procesador de correos para una sola cuenta.
        
        Args:
            config: Configuración para la conexión al correo. Si no se proporciona,
                  se utiliza la primera configuración disponible.
        """
        if config is None:
            # Usar la primera configuración disponible como fallback
            configs_data = settings.get_all_email_configs()
            if configs_data:
                first_config = configs_data[0]
                self.config = EmailConfig(
                    host=first_config['host'],
                    port=first_config['port'],
                    username=first_config['username'],
                    password=first_config['password'],
                    search_criteria=first_config.get('search_criteria', 'UNSEEN'),
                    search_terms=first_config.get('search_terms', settings.EMAIL_SEARCH_TERMS)
                )
            else:
                # Fallback a configuración legacy
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
        os.makedirs(settings.EXCEL_OUTPUT_DIR, exist_ok=True)
        
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
        Obtiene el contenido de un correo específico (asunto, fecha, adjuntos, enlaces).
        """
        if not self.conn:
            if not self.connect():
                return {}, []

        try:
            status, data = self.conn.fetch(email_id, "(RFC822)")
            if status != "OK":
                logger.error(f"❌ Error al obtener el correo {email_id}: {status}")
                return {}, []

            message = email.message_from_bytes(data[0][1])

            subject = self._decode_email_header(message.get("Subject", ""))
            sender = self._decode_email_header(message.get("From", ""))
            date_str = message.get("Date", "")

            date = None
            if date_str:
                try:
                    date = email.utils.parsedate_to_datetime(date_str)
                except Exception as e:
                    logger.warning(f"⚠️ Error al parsear fecha '{date_str}': {str(e)}")

            metadata = {
                "subject": subject,
                "sender": sender,
                "date": date,
                "message_id": email_id
            }

            attachments = []
            links = self._extract_links_from_email(message)

            for part in message.walk():
                if part.get_content_maintype() == "multipart":
                    continue

                filename = part.get_filename()
                if not filename:
                    continue

                # Limpiar nombre y obtener tipo MIME
                filename = self._decode_email_header(filename).strip()
                content_type = part.get_content_type().lower()
                content = part.get_payload(decode=True)

                # Detección de XML y PDF (robusta)
                is_pdf = filename.endswith(".pdf") or content_type in ["application/pdf"]
                is_xml = filename.endswith(".xml") or content_type in [
                    "text/xml",
                    "application/xml",
                    "application/x-iso20022+xml",
                    "application/x-invoice+xml"
                ]

                if is_pdf or is_xml:
                    logger.info(f"📎 Adjunto detectado: {filename} ({content_type})")
                    attachments.append({
                        "filename": filename,
                        "content": content,
                        "content_type": content_type
                    })

            metadata["links"] = links

            logger.info(f"📬 Correo {email_id} - Asunto: '{subject}' - Adjuntos: {len(attachments)} - Enlaces: {len(links)}")

            return metadata, attachments

        except Exception as e:
            logger.error(f"❌ Error al procesar el correo {email_id}: {str(e)}")
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
        # Para mi yo del futuro, verificar las URLS de las compañías que envian sus facturas por este metodo.
        #siga_pattern = r'https?://facte\.siga\.com\.py/[^\s<>"]*'
        
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
                    # siga_links = re.findall(siga_pattern, content)
                    # links.extend(siga_links)
                    
                    # Si es contenido HTML, buscar enlaces adicionales usando BeautifulSoup
                    if content_type == "text/html":
                        try:
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(content, 'html.parser')
                            
                            # Palabras clave para identificar enlaces de facturas
                            factura_keywords = [
                                'visualizar documento', 'ver factura', 'descargar factura', 
                                'factura electronica', 'visualizar',
                                'descargar xml', 'ver documento',
                                'pdf', 'imprimir', 'download', 'print','VISUALIZAR DOCUMENTO',
                                'factura electrónica', 'generar pdf', 'exportar pdf', 'ver pdf'
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
    
    def save_binary_file(self, content: bytes, filename: str) -> str:
        """
        Guarda cualquier archivo binario (PDF, XML, etc.) con un nombre único garantizado.

        Args:
            content: Contenido binario.
            filename: Nombre original del archivo.

        Returns:
            str: Ruta absoluta del archivo guardado, o "" en caso de error.
        """
        try:
            import uuid

            # Detectar extensión
            _, ext = os.path.splitext(filename)
            ext = ext.lower() or ".bin"  # Default por seguridad

            # Carpeta base (única para todo, o podés separarla según tipo si querés)
            output_dir = settings.TEMP_PDF_DIR
            os.makedirs(output_dir, exist_ok=True)

            # Limpiar el nombre del archivo
            safe_filename = self._sanitize_filename(filename)

            # Nombre único
            unique_filename = self._generate_unique_filename(safe_filename)
            file_path = os.path.join(output_dir, unique_filename)

            # Verificación extra de unicidad
            if os.path.exists(file_path):
                name, ext = os.path.splitext(unique_filename)
                unique_filename = f"{name}_{uuid.uuid4().hex[:8]}{ext}"
                file_path = os.path.join(output_dir, unique_filename)

            # Guardar archivo
            with open(file_path, "wb") as f:
                f.write(content)

            logger.info(f"🗂 Archivo guardado: {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"❌ Error al guardar archivo {filename}: {str(e)}")
            return ""
    
    def _sanitize_filename(self, filename: str) -> str:
        """
        Limpia el nombre del archivo eliminando caracteres problemáticos.
        
        Args:
            filename: Nombre original del archivo
            
        Returns:
            str: Nombre limpio y seguro
        """
        # Remover caracteres no permitidos en nombres de archivo
        safe_filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        
        # Remover caracteres de control y espacios extras
        safe_filename = re.sub(r'[\x00-\x1f\x7f-\x9f]', '_', safe_filename)
        safe_filename = re.sub(r'\s+', '_', safe_filename.strip())
        
        # Limitar longitud (reservar espacio para timestamp y UUID)
        name, ext = os.path.splitext(safe_filename)
        if len(name) > 100:  # Limitar a 100 caracteres el nombre base
            name = name[:100]
        
        # Asegurar extensión .pdf
        if not ext.lower().endswith('.pdf'):
            ext = '.pdf'
            
        return f"{name}{ext}"
    
    def _generate_unique_filename(self, clean_filename: str) -> str:
        """
        Genera un nombre de archivo único garantizado.
        
        Args:
            clean_filename: Nombre de archivo ya limpio
            
        Returns:
            str: Nombre único con timestamp y UUID
        """
        import uuid
        
        # Timestamp con microsegundos para máxima precisión
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]  # Quitar últimos 3 dígitos de microsegundos
        
        # UUID corto para garantizar unicidad absoluta
        unique_id = uuid.uuid4().hex[:8]
        
        # Separar nombre y extensión
        name, ext = os.path.splitext(clean_filename)
        
        # Construir nombre único: timestamp_uuid_nombre_original.pdf
        unique_filename = f"{timestamp}_{unique_id}_{name}{ext}"
        
        return unique_filename
    
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
            
            # Verificar si tenemos contenido
            content_bytes = response.content
            if not content_bytes:
                logger.warning("Respuesta vacía, no hay contenido para procesar")
                return ""
            
            # Verificar si el contenido es realmente un PDF analizando los primeros bytes
            is_pdf_content = content_bytes.startswith(b'%PDF-')
            is_xml_content = content_bytes.startswith(b'<?xml')
            
            logger.info(f"Análisis de contenido - PDF: {is_pdf_content}, XML: {is_xml_content}, Tamaño: {len(content_bytes)} bytes")
            
            # Si es un PDF directo (detectado por Content-Type o por contenido)
            if (content_type.startswith("application/pdf") or 
                is_pdf_content or 
                (content_type.startswith("application/octet-stream") and is_pdf_content)):
                logger.info("PDF directo detectado, guardando...")
                filename = self._generate_filename_from_url(url, "pdf")
                return self.save_binary_file(content_bytes, filename)
            
            # Si es XML (para facturas electrónicas)
            elif (content_type.startswith("application/xml") or 
                  content_type.startswith("text/xml") or
                  is_xml_content or
                  (content_type.startswith("application/octet-stream") and 
                   url.lower().find('xml') > -1)):
                logger.info("Archivo XML detectado, saltando (no es PDF)...")
                return ""
            
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
        
        # Análisis dinámico de cualquier URL
        import re
        from urllib.parse import urlparse, parse_qs
        
        try:
            # Parsear la URL para extraer parámetros
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            
            # Buscar parámetros comunes de facturas de forma dinámica
            ruc = None
            cdc = None
            numero_factura = None
            
            # Buscar información útil en parámetros de query
            for param_name, values in query_params.items():
                param_lower = param_name.lower()
                if values and values[0]:  # Asegurar que hay valor
                    if 'ruc' in param_lower:
                        ruc = values[0]
                    elif any(keyword in param_lower for keyword in ['cdc', 'codigo', 'code', 'document', 'doc']):
                        cdc = values[0][:12]  # Limitar longitud
                    elif any(keyword in param_lower for keyword in ['factura', 'invoice', 'numero', 'number', 'num']):
                        numero_factura = values[0][:10]  # Limitar longitud
            
            # También buscar en la URL completa con regex (backup)
            if not ruc:
                ruc_match = re.search(r'ruc[=:]([^&\s]+)', url, re.IGNORECASE)
                if ruc_match:
                    ruc = ruc_match.group(1)
            
            if not cdc:
                cdc_match = re.search(r'(?:cdc|codigo|code|document)[=:]([^&\s]+)', url, re.IGNORECASE)
                if cdc_match:
                    cdc = cdc_match.group(1)[:12]
            
            # Construir nombre basado en información disponible
            parts = []
            
            if ruc:
                # Limpiar RUC de caracteres especiales
                ruc_clean = re.sub(r'[^\w\-]', '', ruc)
                parts.append(f"ruc_{ruc_clean}")
            
            if cdc:
                # Limpiar CDC de caracteres especiales
                cdc_clean = re.sub(r'[^\w\-]', '', cdc)
                parts.append(f"cdc_{cdc_clean}")
            
            if numero_factura:
                # Limpiar número de factura
                num_clean = re.sub(r'[^\w\-]', '', numero_factura)
                parts.append(f"num_{num_clean}")
            
            # Si tenemos información útil, usarla
            if parts:
                identifier = "_".join(parts)
                return f"factura_{identifier}_{timestamp}.{extension}"
            
        except Exception as e:
            logger.warning(f"Error al parsear URL para nombre de archivo: {str(e)}")
        
        # Fallback universal: usar dominio + hash de la URL
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc.replace('.', '_').replace(':', '_')
            # Usar solo los primeros caracteres del dominio para mantener nombre corto
            domain = domain[:20] if domain else "unknown"
            # Limpiar caracteres especiales del dominio
            domain = re.sub(r'[^\w\-_]', '', domain)
        except:
            domain = "unknown"
        
        # Crear hash corto de la URL para garantizar unicidad
        import hashlib
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        
        # Fallback: nombre con dominio y hash
        return f"factura_{domain}_{url_hash}_{timestamp}.{extension}"

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
                'descargar', 'pdf', 'imprimir', 'download', 'print','VISUALIZAR DOCUMENTO','visualizar documento',
                'ver factura', 'descargar factura', 'factura electronica','ver documento',
                'factura electrónica', 'visualizar','generar pdf', 'exportar pdf', 'ver pdf'
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
                                return self.save_binary_file(pdf_response.content, filename)
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
            invoices=[],
            excel_files=[]
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
                    metadata, attachments = self.get_email_content(email_id)
                    if not metadata:
                        logger.warning(f"⚠️ No se pudo obtener metadatos del correo {email_id}")
                        continue

                    email_meta_for_ai = {
                        "sender": metadata.get("sender", ""),
                        "subject": metadata.get("subject", ""),
                        "date": metadata.get("date")
                    }

                    xml_path = None
                    pdf_path = None
                    factura_procesada = False  # 👈 bandera para saber si se procesó alguna factura

                    # 🔍 Procesar adjuntos (XML tiene prioridad)
                    for attachment in attachments:
                        filename = attachment.get("filename", "").lower()
                        content_type = attachment.get("content_type", "").lower()
                        content = attachment.get("content")

                        is_pdf = filename.endswith(".pdf") or content_type == "application/pdf"
                        is_xml = (
                            filename.endswith(".xml") or
                            content_type in [
                                "text/xml", "application/xml",
                                "application/x-iso20022+xml", "application/x-invoice+xml"
                            ]
                        )

                        if is_xml:
                            xml_path = self.save_binary_file(content, filename)
                            logger.info(f"📄 XML adjunto detectado: {filename}")
                        elif is_pdf:
                            pdf_path = self.save_binary_file(content, filename)
                            logger.info(f"📄 PDF adjunto detectado: {filename}")

                    # ✅ Procesar XML si existe
                    if xml_path:
                        logger.info("📄 Procesando XML adjunto como fuente principal")
                        invoice_data = self.openai_processor.extract_invoice_data_from_xml(xml_path)
                        if invoice_data:
                            result.invoices.append(invoice_data)
                            result.invoice_count += 1
                            factura_procesada = True

                    # 📄 Si no hay XML válido, procesar PDF
                    elif pdf_path:
                        logger.info("📄 Procesando PDF porque no se encontró XML")
                        invoice_data = self.openai_processor.extract_invoice_data(pdf_path, email_meta_for_ai)
                        if invoice_data:
                            result.invoices.append(invoice_data)
                            result.invoice_count += 1
                            factura_procesada = True

                    # 🔗 Procesar enlaces si no hubo XML
                    if not factura_procesada and metadata.get("links"):
                        logger.info(f"🔗 Procesando {len(metadata['links'])} enlaces encontrados")
                        for link in metadata["links"]:
                            logger.info(f"🔗 Intentando procesar enlace: {link}")
                            downloaded_path = self.download_pdf_from_url(link)

                            if not downloaded_path:
                                logger.warning(f"❌ No se pudo descargar desde el enlace: {link}")
                                continue

                            lower_path = downloaded_path.lower()
                            invoice_data = None

                            if lower_path.endswith(".xml") and "factura" in lower_path:
                                logger.info("📄 XML detectado desde enlace, procesando como factura electrónica")
                                invoice_data = self.openai_processor.extract_invoice_data_from_xml(downloaded_path)
                            elif lower_path.endswith(".pdf"):
                                logger.info("📄 PDF detectado desde enlace, procesando con OpenAI")
                                invoice_data = self.openai_processor.extract_invoice_data(downloaded_path, email_meta_for_ai)
                            else:
                                logger.warning(f"⚠️ Tipo de archivo no reconocido: {downloaded_path}")
                                continue

                            if invoice_data:
                                result.invoices.append(invoice_data)
                                result.invoice_count += 1
                                factura_procesada = True

                    # ✅ Marcar como leído solo si hubo procesamiento exitoso
                    if factura_procesada:
                        self.mark_as_read(email_id)
                    else:
                        logger.warning(f"⚠️ Ninguna factura procesada del correo {email_id}, no se marcará como leído.")

                except Exception as e:
                    logger.error(f"❌ Error al procesar el correo {email_id}: {str(e)}")
                    continue
            
            # Exportar a Excel si hay facturas
            if result.invoices:
                excel_path = self.excel_exporter.export_invoices(result.invoices)
                if excel_path:
                    result.excel_files = [excel_path]
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
