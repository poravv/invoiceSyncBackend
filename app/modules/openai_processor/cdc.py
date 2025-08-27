from __future__ import annotations
import re
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

def validate_and_enhance_with_cdc(invoice: Any) -> Any:
    """
    Ajusta la fecha de la factura usando el CDC (si está presente y válido).
    - Toma fecha AAAAMMDD de posiciones 11-18 (0-based: [10:18]).
    - Rango lógico: año >= 2020 y <= hoy.
    """
    try:
        cdc = (getattr(invoice, "cdc", "") or "").replace(" ", "")
        if not cdc or len(cdc) != 44 or not cdc.isdigit():
            return invoice

        fecha_raw = cdc[10:18]
        if not re.match(r"20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", fecha_raw):
            return invoice

        fecha_cdc = datetime.strptime(fecha_raw, "%Y%m%d").date()
        if fecha_cdc.year < 2020 or fecha_cdc > datetime.today().date():
            return invoice

        if getattr(invoice, "fecha", None):
            if invoice.fecha > fecha_cdc:
                invoice.fecha = fecha_cdc
        else:
            invoice.fecha = fecha_cdc
        return invoice
    except Exception as e:
        logger.warning("CDC validation error: %s", e)
        return invoice