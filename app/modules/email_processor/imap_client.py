import imaplib
import email
import logging
import socket
import time
from typing import List, Optional, Set
from email.header import decode_header
from email.message import Message
import os

logger = logging.getLogger(__name__)

class IMAPClient:
    """
    Envoltura mínima: conecta, busca por asunto, fetch por UID y marca como leído por UID.
    Pensado para cPanel y Gmail. (Asumiendo términos SIN acentos en .env)
    """
    def __init__(self, host: str, port: int, username: str, password: str, mailbox: str = "INBOX"):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mailbox = mailbox
        self.conn: Optional[imaplib.IMAP4_SSL] = None
        self.is_gmail: bool = False

    def connect(self) -> bool:
        """Conecta con retry automático y timeouts robustos."""
        max_retries = 3
        retry_delay = 2  # segundos
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Intento de conexión {attempt + 1}/{max_retries} - host: {self.host}, port: {self.port}, username: {self.username}")

                self.is_gmail = "imap.gmail.com" in (self.host or "").lower()
                
                # Crear conexión con timeout de socket más agresivo
                try:
                    self.conn = imaplib.IMAP4_SSL(self.host, self.port)
                except (socket.timeout, socket.gaierror, socket.error, OSError) as e:
                    logger.warning(f"Error de conexión de red (intento {attempt + 1}): {e}")
                    if attempt == max_retries - 1:
                        logger.error(f"❌ Fallo de conexión después de {max_retries} intentos")
                        return False
                    time.sleep(retry_delay * (attempt + 1))  # Backoff exponencial
                    continue
                
                # Configurar timeout de socket para operaciones IMAP
                if hasattr(self.conn, 'sock') and self.conn.sock:
                    self.conn.sock.settimeout(30.0)  # 30 segundos timeout
                
                # Login con manejo de errores específicos
                try:
                    self.conn.login(self.username, self.password)
                except (socket.timeout, socket.error, imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
                    logger.warning(f"Error de autenticación IMAP (intento {attempt + 1}): {e}")
                    try:
                        self.conn.close()
                        self.conn.logout()
                    except:
                        pass
                    self.conn = None
                    if attempt == max_retries - 1:
                        logger.error(f"❌ Error de autenticación después de {max_retries} intentos")
                        return False
                    time.sleep(retry_delay * (attempt + 1))
                    continue

                # Seleccionar mailbox
                try:
                    typ, _ = self.conn.select(self.mailbox)
                    if typ != "OK":
                        raise RuntimeError(f"No se pudo seleccionar mailbox: {self.mailbox}")
                except (socket.timeout, socket.error, imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
                    logger.warning(f"Error seleccionando mailbox (intento {attempt + 1}): {e}")
                    try:
                        self.conn.close()
                        self.conn.logout()
                    except:
                        pass
                    self.conn = None
                    if attempt == max_retries - 1:
                        logger.error(f"❌ Error seleccionando mailbox después de {max_retries} intentos")
                        return False
                    time.sleep(retry_delay * (attempt + 1))
                    continue

                logger.info(f"✅ Conexión exitosa al correo {self.username} | is_gmail={self.is_gmail}")
                return True
                
            except Exception as e:
                logger.error(f"❌ Error inesperado en conexión (intento {attempt + 1}): {e}")
                self.conn = None
                if attempt == max_retries - 1:
                    return False
                time.sleep(retry_delay * (attempt + 1))
        
        return False

    def close(self):
        """Cierra la conexión de forma segura."""
        if not self.conn:
            return
        try:
            # Configurar timeout corto para cierre
            if hasattr(self.conn, 'sock') and self.conn.sock:
                self.conn.sock.settimeout(5.0)  # 5 segundos para cierre
            
            try:
                self.conn.close()
            except (socket.timeout, socket.error, imaplib.IMAP4.abort, imaplib.IMAP4.error):
                # Ignorar errores de cierre
                pass
            
            try:
                self.conn.logout()
                logger.info("✅ Desconexión exitosa del servidor de correo")
            except (socket.timeout, socket.error, imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
                logger.warning(f"⚠️ Error menor al desconectar del servidor de correo: {str(e)}")
                
        except Exception as e:
            logger.error(f"❌ Error inesperado al desconectar del servidor de correo: {str(e)}")
        finally:
            self.conn = None

    def search(self, subject_terms: List[str]) -> List[str]:
        """
        Devuelve UIDs de correos que coincidan con cualquiera de los términos de asunto.
        Respeta EMAIL_SEARCH_CRITERIA: 'ALL' = todos, cualquier otro valor = solo no leídos.
        Funciona igual para Gmail y servidores IMAP comunes. Sin X-GM-RAW.
        """
        if not self.conn and not self.connect():
            return []

        unread_only = (os.getenv("EMAIL_SEARCH_CRITERIA", "UNSEEN").upper() != "ALL")
        flag_args = ['UNSEEN'] if unread_only else ['ALL']
        terms = [t.strip() for t in (subject_terms or []) if t and t.strip()]

        def _decode_ids(data) -> List[str]:
            if not data:
                return []
            first = data[0]
            payload = first.decode('utf-8', errors='ignore').strip() if isinstance(first, (bytes, bytearray)) else str(first).strip()
            return payload.split() if payload else []

        uids: Set[str] = set()

        # Sin términos: traemos todo según flag
        if not terms:
            try:
                typ, data = self.conn.uid('SEARCH', *flag_args)
                if typ == 'OK':
                    uids |= set(_decode_ids(data))
                else:
                    logger.error(f"UID SEARCH {' '.join(flag_args)} falló: {typ}")
            except Exception as e:
                logger.error(f"UID SEARCH error sin términos: {e}")
            return sorted(uids, key=lambda x: int(x))

        # Con términos: una búsqueda por término → unión
        for term in terms:
            args = flag_args + ['SUBJECT', f'"{term}"']
            try:
                logger.debug(f"IMAP UID SEARCH args: {args}")  # para auditar exactamente qué se envía
                
                # Aplicar timeout específico para la búsqueda
                old_timeout = None
                if hasattr(self.conn, 'sock') and self.conn.sock:
                    old_timeout = self.conn.sock.gettimeout()
                    self.conn.sock.settimeout(15.0)  # 15 segundos para búsqueda
                
                try:
                    typ, data = self.conn.uid('SEARCH', *args)
                    if typ == 'OK':
                        uids |= set(_decode_ids(data))
                    else:
                        logger.error(f"UID SEARCH para term '{term}' falló: {typ}")
                except (socket.timeout, socket.error) as e:
                    logger.error(f"Timeout/error de red en UID SEARCH para term '{term}': {e}")
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
                    logger.error(f"Error IMAP en UID SEARCH para term '{term}': {e}")
                finally:
                    # Restaurar timeout original
                    if old_timeout is not None and hasattr(self.conn, 'sock') and self.conn.sock:
                        try:
                            self.conn.sock.settimeout(old_timeout)
                        except:
                            pass
                    
            except socket.timeout:
                logger.error(f"Timeout en UID SEARCH para term '{term}'")
            except (socket.error, imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
                logger.error(f"Error IMAP/red en UID SEARCH para term '{term}': {e}")
            except Exception as e:
                logger.error(f"Error inesperado en UID SEARCH para term '{term}': {e}")

        return sorted(uids, key=lambda x: int(x))

    def fetch_message(self, email_uid: str) -> Optional[Message]:
        if not self.conn:
            return None
        try:
            # Aplicar timeout específico para fetch
            old_timeout = None
            if hasattr(self.conn, 'sock') and self.conn.sock:
                old_timeout = self.conn.sock.gettimeout()
                self.conn.sock.settimeout(20.0)  # 20 segundos para fetch
            
            try:
                status, data = self.conn.uid('FETCH', email_uid, '(RFC822)')
                # data esperado: [(b'<uid> (RFC822 {<len>}', b'<raw>'), b')']
                if status != 'OK' or not data:
                    logger.error(f"❌ Error al obtener el correo UID {email_uid}: {status}")
                    return None
                # Busca el tuple con el contenido real
                for item in data:
                    if isinstance(item, tuple) and len(item) >= 2:
                        return email.message_from_bytes(item[1])
                        
                logger.error(f"❌ Formato inesperado en FETCH UID {email_uid}: {data!r}")
                return None
                
            except (socket.timeout, socket.error) as e:
                logger.error(f"Timeout/error de red en FETCH UID {email_uid}: {e}")
                return None
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
                logger.error(f"Error IMAP en FETCH UID {email_uid}: {e}")
                return None
            finally:
                # Restaurar timeout original
                if old_timeout is not None and hasattr(self.conn, 'sock') and self.conn.sock:
                    try:
                        self.conn.sock.settimeout(old_timeout)
                    except:
                        pass
                
        except Exception as e:
            logger.error(f"❌ Error inesperado al hacer FETCH UID {email_uid}: {e}")
            return None

    def mark_seen(self, email_uid: str) -> bool:
        if not self.conn:
            return False
        try:
            # Aplicar timeout específico para mark_seen
            old_timeout = None
            if hasattr(self.conn, 'sock') and self.conn.sock:
                old_timeout = self.conn.sock.gettimeout()
                self.conn.sock.settimeout(10.0)  # 10 segundos para mark_seen
            
            try:
                # ✅ Usar UID STORE
                status, _ = self.conn.uid('STORE', email_uid, '+FLAGS', '(\\Seen)')
                ok = status == 'OK'
                if ok:
                    logger.info(f"✅ Correo UID {email_uid} marcado como leído")
                else:
                    logger.error(f"❌ Error al marcar como leído UID {email_uid}: {status}")
                return ok
                
            except (socket.timeout, socket.error) as e:
                logger.error(f"Timeout/error de red al marcar como leído UID {email_uid}: {e}")
                return False
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
                logger.error(f"Error IMAP al marcar como leído UID {email_uid}: {e}")
                return False
            finally:
                # Restaurar timeout original
                if old_timeout is not None and hasattr(self.conn, 'sock') and self.conn.sock:
                    try:
                        self.conn.sock.settimeout(old_timeout)
                    except:
                        pass
                
        except Exception as e:
            logger.error(f"❌ Error inesperado al marcar el correo UID {email_uid} como leído: {str(e)}")
            return False


def decode_mime_header(header: str) -> str:
    if not header:
        return ""
    try:
        parts = []
        for part, enc in decode_header(header):
            if isinstance(part, bytes):
                parts.append(part.decode(enc or "utf-8", errors="replace"))
            else:
                parts.append(str(part))
        return "".join(parts)
    except Exception as e:
        logger.warning(f"Error al decodificar encabezado '{header}': {str(e)}")
        return header