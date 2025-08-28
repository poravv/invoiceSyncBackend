import logging
from typing import List, Set
from app.models.models import InvoiceData

logger = logging.getLogger(__name__)

def deduplicate_invoices(invoices: List[InvoiceData]) -> List[InvoiceData]:
    """
    Deduplicación estable por CDC (y fallback por combinación de campos).
    Mantiene la última factura en caso de colisión.
    """
    seen: Set[str] = set()
    unique: List[InvoiceData] = []
    for inv in invoices:
        if not inv:
            continue
        cdc = getattr(inv, "cdc", "") or ""
        if cdc:
            key = cdc
        else:
            key = f"{getattr(inv,'numero_factura','')}-{getattr(inv,'ruc_emisor','')}-{getattr(inv,'monto_total',0)}"
        if key and key not in seen:
            seen.add(key)
            unique.append(inv)
        else:
            logger.warning(f"Factura duplicada omitida: {getattr(inv,'numero_factura','N/A')} - CDC: {cdc}")
    return unique