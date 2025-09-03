import logging
import requests
import socket
from typing import Optional
from urllib.parse import urljoin
from requests.exceptions import RequestException, Timeout, ConnectionError, HTTPError
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
    max_retries = 2
    timeout = 15  # Reducido de 30 a 15 segundos
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Intentando descargar desde: {url} (intento {attempt + 1}/{max_retries})")
            
            # Configurar session con timeouts
            session = requests.Session()
            session.headers.update(BROWSER_HEADERS)
            
            r = session.get(
                url, 
                timeout=(5, timeout),  # (connect_timeout, read_timeout)
                allow_redirects=True,
                stream=False  # No usar stream para archivos pequeños
            )
            
            if r.status_code != 200:
                logger.error(f"Error HTTP {r.status_code} al acceder a {url}")
                if attempt == max_retries - 1:
                    return ""
                continue

            ctype = (r.headers.get("Content-Type") or "").lower()
            content = r.content or b""
            is_pdf = content.startswith(b"%PDF-")

            if ctype.startswith("application/pdf") or is_pdf:
                logger.info("✅ PDF directo detectado, guardando...")
                name = filename_from_url(url, "pdf")
                return save_binary(content, name, force_pdf=True)

            if ctype.startswith("application/xml") or ctype.startswith("text/xml") or content.startswith(b"<?xml"):
                logger.info("📄 Contenido XML detectado, guardando...")
                name = filename_from_url(url, "xml")
                return save_binary(content, name, force_pdf=False)

            if ctype.startswith("text/html"):
                logger.info("🌐 Página HTML detectada, buscando enlaces PDF...")
                return _extract_pdf_from_html(r.text, url)

            logger.warning(f"⚠️ Tipo de contenido no soportado: {ctype}")
            return ""
            
        except (Timeout, socket.timeout) as e:
            logger.warning(f"⏱️ Timeout al descargar desde {url} (intento {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                logger.error(f"❌ Timeout definitivo al descargar desde {url}")
                return ""
            
        except (ConnectionError, socket.error) as e:
            logger.warning(f"🔌 Error de conexión al descargar desde {url} (intento {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                logger.error(f"❌ Error de conexión definitivo al descargar desde {url}")
                return ""
            
        except HTTPError as e:
            logger.error(f"❌ Error HTTP al descargar desde {url}: {e}")
            return ""  # No reintentar errores HTTP
            
        except RequestException as e:
            logger.warning(f"📡 Error de request al descargar desde {url} (intento {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                logger.error(f"❌ Error de request definitivo al descargar desde {url}")
                return ""
            
        except Exception as e:
            logger.error(f"❌ Error inesperado al descargar desde {url}: {e}")
            return ""  # No reintentar errores inesperados
    
    return ""

def _extract_pdf_from_html(html: str, base_url: str) -> str:
    """Extrae PDFs de páginas HTML con timeouts robustos."""
    try:
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

        # Limitar número de candidatos para evitar cuelgues
        candidates = candidates[:5]  # Máximo 5 intentos
        
        for i, url in enumerate(candidates):
            try:
                logger.info(f"🔗 Probando candidato {i+1}/{len(candidates)}: {url}")
                
                # Configurar session con timeouts agresivos
                session = requests.Session()
                session.headers.update(BROWSER_HEADERS)
                
                rr = session.get(
                    url, 
                    timeout=(3, 10),  # timeouts más agresivos para candidatos
                    allow_redirects=True,
                    stream=False
                )
                
                if rr.status_code == 200:
                    ctype = (rr.headers.get("Content-Type", "")).lower()
                    content = rr.content
                    
                    # Verificar si es PDF
                    if (ctype.startswith("application/pdf") or content.startswith(b"%PDF-")):
                        logger.info(f"✅ PDF encontrado y descargado desde: {url}")
                        name = filename_from_url(url, "pdf")
                        return save_binary(content, name, force_pdf=True)
                    
                    # Verificar si es XML
                    elif (ctype.startswith("application/xml") or ctype.startswith("text/xml") or 
                          content.startswith(b"<?xml")):
                        logger.info(f"📄 XML encontrado y descargado desde: {url}")
                        name = filename_from_url(url, "xml")
                        return save_binary(content, name, force_pdf=False)
                    
                    else:
                        logger.debug(f"❌ Candidato {url} no es PDF ni XML (tipo: {ctype})")
                        
            except (Timeout, socket.timeout) as e:
                logger.warning(f"⏱️ Timeout al probar candidato {url}: {e}")
                continue
                
            except (ConnectionError, socket.error) as e:
                logger.warning(f"🔌 Error de conexión al probar candidato {url}: {e}")
                continue
                
            except Exception as e:
                logger.debug(f"❌ Error al intentar descargar candidato {url}: {e}")
                continue

        logger.warning(f"⚠️ No se encontró enlace PDF/XML descargable en la página: {base_url}")
        return ""
        
    except Exception as e:
        logger.error(f"❌ Error inesperado al extraer PDF de HTML: {e}")
        return ""