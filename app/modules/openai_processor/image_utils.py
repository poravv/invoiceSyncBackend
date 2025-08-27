from __future__ import annotations
import base64
import logging

logger = logging.getLogger(__name__)

def pdf_to_base64_first_page(pdf_path: str) -> str:
    """
    Convierte la primera página del PDF a JPEG base64 (300dpi aprox).
    Requiere PyMuPDF.
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
        img_bytes = pix.tobytes("jpeg")
        doc.close()
        return base64.b64encode(img_bytes).decode("utf-8")
    except Exception as e:
        logger.error("Error convirtiendo PDF a imagen: %s", e)
        raise

def ocr_from_base64_image(base64_image: str, lang: str = "spa") -> str:
    """
    OCR sobre imagen base64 (JPEG/PNG).
    Requiere Pillow + pytesseract + tesseract binario.
    """
    try:
        import io
        import pytesseract
        from PIL import Image

        image_bytes = base64.b64decode(base64_image)
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang=lang)
        return text.strip()
    except Exception as e:
        logger.error("Error en OCR base64: %s", e)
        return ""