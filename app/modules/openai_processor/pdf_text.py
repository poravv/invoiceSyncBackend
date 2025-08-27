from __future__ import annotations
from typing import List
import logging

logger = logging.getLogger(__name__)

# libs opcionales
try:
    import pdfplumber
except Exception:  # pragma: no cover
    pdfplumber = None  # type: ignore

try:
    from pdfminer.high_level import extract_text as extract_text_pdfminer
except Exception:  # pragma: no cover
    extract_text_pdfminer = None  # type: ignore

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None  # type: ignore

try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore

def extract_text_with_fallbacks(pdf_path: str, try_ocr_first_page: bool = True) -> str:
    """
    Pipeline de extracción: pdfplumber → pdfminer → fitz (get_text) → OCR (1ra página).
    Devuelve el primer texto no vacío encontrado.
    """
    # 1) pdfplumber
    text = _extract_with_pdfplumber(pdf_path)
    if text:
        return text

    # 2) pdfminer
    text = _extract_with_pdfminer(pdf_path)
    if text:
        return text

    # 3) fitz.get_text
    text = _extract_with_fitz_text(pdf_path)
    if text:
        return text

    # 4) OCR primera página (si se pidió)
    if try_ocr_first_page:
        text = ocr_first_page(pdf_path)
        if text:
            return text

    return ""

def has_extractable_text_or_ocr(pdf_path: str) -> bool:
    """
    True si hay texto extraíble por PyPDF2, o por OCR de la primera página.
    """
    # PyPDF2 check
    try:
        if PdfReader:
            reader = PdfReader(pdf_path)
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    return True
    except Exception:
        pass

    # OCR primera página
    t = ocr_first_page(pdf_path)
    return bool(t.strip())

# --- helpers por backend ---

def _extract_with_pdfplumber(pdf_path: str) -> str:
    buf: List[str] = []
    if not pdfplumber:
        return ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    buf.append(text)
                # tablas: si necesitas, puedes reactivar extracción de tablas aquí.
        return "\n".join(buf).strip()
    except Exception as e:
        logger.warning("pdfplumber falló: %s", e)
        return ""

def _extract_with_pdfminer(pdf_path: str) -> str:
    if not extract_text_pdfminer:
        return ""
    try:
        t = extract_text_pdfminer(pdf_path).strip()
        return t
    except Exception as e:
        logger.warning("pdfminer falló: %s", e)
        return ""

def _extract_with_fitz_text(pdf_path: str) -> str:
    if not fitz:
        return ""
    try:
        with fitz.open(pdf_path) as doc:
            parts = [page.get_text() or "" for page in doc]
        return "".join(parts).strip()
    except Exception as e:
        logger.warning("fitz.get_text falló: %s", e)
        return ""

def ocr_first_page(pdf_path: str, lang: str = "spa") -> str:
    """
    OCR de la primera página vía PyMuPDF + Tesseract.
    Requiere: fitz, Pillow, pytesseract instalados + binario tesseract en el sistema.
    """
    try:
        import io
        import pytesseract
        from PIL import Image
        if not fitz:
            return ""
        with fitz.open(pdf_path) as doc:
            if len(doc) == 0:
                return ""
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))
            t = pytesseract.image_to_string(img, lang=lang).strip()
            return t
    except Exception as e:
        logger.warning("OCR falló: %s", e)
        return ""