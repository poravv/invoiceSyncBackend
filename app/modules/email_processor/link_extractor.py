import re
import logging
from typing import List
from bs4 import BeautifulSoup  # requirements ya lo incluye
logger = logging.getLogger(__name__)

PDF_URL_REGEX = r'https?://[^\s<>"]+\.pdf'
FACTURA_KEYWORDS = [
    'visualizar documento', 'ver factura', 'descargar factura', 'factura electronica',
    'factura electrónica', 'visualizar', 'descargar xml', 'ver documento',
    'pdf', 'imprimir', 'download', 'print', 'VISUALIZAR DOCUMENTO', 'generar pdf', 'exportar pdf', 'ver pdf'
]

def extract_links_from_message(message) -> List[str]:
    """
    Extrae posibles enlaces de factura (PDF/visor) del email completo.
    Combina heurística de regex + parsing HTML.
    """
    links: List[str] = []

    for part in message.walk():
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue

        try:
            raw = part.get_payload(decode=True)
            content = raw.decode(part.get_content_charset() or "utf-8", errors="replace")

            # 1) Enlaces .pdf por regex simple
            links.extend(re.findall(PDF_URL_REGEX, content))

            # 2) En contenido HTML, buscar <a> semánticos
            if content_type == "text/html":
                soup = BeautifulSoup(content, "html.parser")
                for a in soup.find_all("a", href=True):
                    text = (a.get_text() or "").lower().strip()
                    href = a["href"]
                    if any(k in text for k in FACTURA_KEYWORDS) or href.lower().endswith(".pdf") or "pdf" in href.lower():
                        links.append(href)
        except Exception as e:
            logger.warning(f"Error extrayendo enlaces: {e}")

    unique_links = sorted(set(links))
    if unique_links:
        logger.info(f"Enlaces encontrados: {unique_links}")
    return unique_links