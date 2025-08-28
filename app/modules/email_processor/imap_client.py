import imaplib
import email
import logging
from typing import List, Tuple, Optional
from email.header import decode_header

logger = logging.getLogger(__name__)

class IMAPClient:
    """
    Envoltura mínima sobre imaplib para centralizar conexión, búsqueda, fetch y flags.
    Maneja select(), login/logout y errores comunes.
    """
    def __init__(self, host: str, port: int, username: str, password: str, mailbox: str = "INBOX"):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mailbox = mailbox
        self.conn: Optional[imaplib.IMAP4_SSL] = None

    def connect(self) -> bool:
        try:
            logger.info(f"host {self.host}")
            logger.info(f"port {self.port}")
            logger.info(f"username {self.username}")
            self.conn = imaplib.IMAP4_SSL(self.host, self.port)
            self.conn.login(self.username, self.password)
            self.conn.select(self.mailbox)
            logger.info(f"Conexión exitosa al correo {self.username}")
            return True
        except Exception as e:
            logger.error(f"Error al conectar al correo: {str(e)}")
            self.conn = None
            return False

    def close(self):
        if not self.conn:
            return
        try:
            self.conn.close()
            self.conn.logout()
            logger.info("Desconexión exitosa del servidor de correo")
        except Exception as e:
            logger.error(f"Error al desconectar del servidor de correo: {str(e)}")

    def search(self, *criteria) -> List[str]:
        if not self.conn:
            return []
        try:
            status, messages = self.conn.search(None, *criteria)
            if status != "OK":
                logger.error(f"Error en la búsqueda de correos: {status}")
                return []
            ids = [eid.decode() for eid in messages[0].split()]
            return ids
        except Exception as e:
            logger.error(f"Error al buscar correos: {str(e)}")
            return []

    def fetch_message(self, email_id: str) -> Optional[email.message.Message]:
        if not self.conn:
            return None
        try:
            status, data = self.conn.fetch(email_id, "(RFC822)")
            if status != "OK":
                logger.error(f"❌ Error al obtener el correo {email_id}: {status}")
                return None
            return email.message_from_bytes(data[0][1])
        except Exception as e:
            logger.error(f"❌ Error al hacer fetch del correo {email_id}: {str(e)}")
            return None

    def mark_seen(self, email_id: str) -> bool:
        if not self.conn:
            return False
        try:
            self.conn.store(email_id, '+FLAGS', '\\Seen')
            logger.info(f"Correo {email_id} marcado como leído")
            return True
        except Exception as e:
            logger.error(f"Error al marcar el correo {email_id} como leído: {str(e)}")
            return False


def decode_mime_header(header: str) -> str:
    """Decodifica cualquier encabezado MIME de forma segura."""
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