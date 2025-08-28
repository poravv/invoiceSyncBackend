import logging
import requests
from typing import Optional
from urllib.parse import urljoin
from app.modules.email_processor.storage import save_binary, filename_from_url

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

def download_pdf_from_url(url: str) -> str:
    """
    Descarga un PDF directo o intenta resolver páginas HTML con enlaces a PDF.
    Devuelve la ruta del archivo o "".
    """
    try:
        logger.info(f"Intentando descargar desde: {url}")
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            logger.error(f"Error HTTP {r.status_code} al acceder a {url}")
            return ""

        ctype = (r.headers.get("Content-Type") or "").lower()
        content = r.content or b""
        is_pdf = content.startswith(b"%PDF-")

        if ctype.startswith("application/pdf") or is_pdf:
            logger.info("PDF directo detectado, guardando...")
            name = filename_from_url(url, "pdf")
            return save_binary(content, name, force_pdf=True)

        if ctype.startswith("application/xml") or ctype.startswith("text/xml") or content.startswith(b"<?xml"):
            logger.info("Contenido XML detectado, se omite (no es PDF).")
            return ""

        if ctype.startswith("text/html"):
            logger.info("Página HTML detectada, buscando enlaces PDF...")
            return _extract_pdf_from_html(r.text, url)

        logger.warning(f"Tipo de contenido no soportado: {ctype}")
        return ""
    except Exception as e:
        logger.error(f"Error al descargar PDF desde {url}: {e}")
        return ""

def _extract_pdf_from_html(html: str, base_url: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").lower().strip()
        if href.lower().endswith(".pdf") or "pdf" in href.lower() or any(
            k in text for k in (
                "descargar", "pdf", "imprimir", "download", "print", "visualizar",
                "ver factura", "descargar factura", "factura electronica", "factura electrónica",
                "ver documento", "generar pdf", "exportar pdf", "ver pdf", "visualizar documento"
            )
        ):
            candidates.append(urljoin(base_url, href))

    for url in candidates:
        try:
            rr = requests.get(url, headers=BROWSER_HEADERS, timeout=30, allow_redirects=True)
            if rr.status_code == 200 and (rr.headers.get("Content-Type","").lower().startswith("application/pdf") or rr.content.startswith(b"%PDF-")):
                logger.info(f"PDF encontrado y descargado desde: {url}")
                name = filename_from_url(url, "pdf")
                return save_binary(rr.content, name, force_pdf=True)
        except Exception as e:
            logger.debug(f"Error al intentar descargar {url}: {e}")

    logger.warning(f"No se encontró enlace PDF descargable en la página: {base_url}")
    return ""