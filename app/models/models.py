from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class ProductoFactura(BaseModel):
    """Modelo para los productos/servicios en la factura."""
    articulo: str = ""
    cantidad: float = 0
    precio_unitario: float = 0
    total: float = 0

class EmpresaData(BaseModel):
    """Datos de la empresa emisora."""
    nombre: str = ""
    ruc: str = ""
    direccion: str = ""
    telefono: str = ""
    actividad_economica: str = ""

class TimbradoData(BaseModel):
    """Datos del timbrado."""
    nro: str = ""
    fecha_inicio_vigencia: str = ""
    valido_hasta: str = ""

class FacturaData(BaseModel):
    """Datos específicos de la factura."""
    contado_nro: str = ""
    fecha: str = ""
    caja_nro: str = ""
    cdc: str = ""
    condicion_venta: str = ""

class TotalesData(BaseModel):
    """Totales de la factura."""
    cantidad_articulos: int = 0
    subtotal: float = 0
    total_a_pagar: float = 0
    iva_0: float = Field(0, alias="iva_0%")
    iva_5: float = Field(0, alias="iva_5%")
    iva_10: float = Field(0, alias="iva_10%")
    total_iva: float = 0

class ClienteData(BaseModel):
    """Datos del cliente."""
    nombre: str = ""
    ruc: str = ""
    email: str = ""

class InvoiceData(BaseModel):
    """Modelo completo para los datos extraídos de una factura."""
    fecha: Optional[datetime] = None
    ruc_emisor: Optional[str] = None
    nombre_emisor: Optional[str] = None
    numero_factura: Optional[str] = None
    monto_total: float = Field(default=0.0)
    iva: float = Field(default=0.0)
    pdf_path: Optional[str] = None
    email_origen: Optional[str] = None
    procesado_en: datetime = Field(default_factory=datetime.now)
    
    # Campos adicionales para facturas paraguayas
    timbrado: Optional[str] = None
    cdc: Optional[str] = None
    ruc_cliente: Optional[str] = None
    nombre_cliente: Optional[str] = None
    email_cliente: Optional[str] = None
    condicion_venta: Optional[str] = None
    moneda: str = Field(default="PYG")
    subtotal_exentas: float = Field(default=0.0)
    subtotal_5: float = Field(default=0.0)
    subtotal_10: float = Field(default=0.0)
    actividad_economica: Optional[str] = None
    
    # Nuevos campos para datos estructurados
    empresa: Optional[EmpresaData] = None
    timbrado_data: Optional[TimbradoData] = None
    factura_data: Optional[FacturaData] = None
    productos: List[ProductoFactura] = Field(default_factory=list)
    totales: Optional[TotalesData] = None
    cliente: Optional[ClienteData] = None
    
    class Config:
        schema_extra = {
            "example": {
                "fecha": "2023-09-15T00:00:00",
                "ruc_emisor": "80014066-4",
                "nombre_emisor": "Empresa ABC S.A.",
                "numero_factura": "F001-12345",
                "monto_total": 1180.0,
                "iva": 180.0,
                "pdf_path": "data/pdfs/factura_001.pdf",
                "email_origen": "facturacion@empresa.com",
                "procesado_en": "2023-09-16T10:30:45",
                "timbrado": "12345678",
                "cdc": "01234567890123456789012345678901234567890123",
                "ruc_cliente": "5379057-0",
                "nombre_cliente": "Cliente XYZ S.A.",
                "email_cliente": "cliente@xyz.com",
                "condicion_venta": "Contado",
                "moneda": "PYG",
                "subtotal_exentas": 0.0,
                "subtotal_5": 100.0,
                "subtotal_10": 1000.0,
                "actividad_economica": "Servicios Informáticos"
            }
        }

class EmailConfig(BaseModel):
    """Configuración para la conexión al correo."""
    host: str
    port: int
    username: str
    password: str
    search_criteria: str = "UNSEEN"
    search_terms: List[str] = ["factura", "facturacion", "factura electronica", "comprobante","Documento Electronico","Documento electronico","documento electrónico", "documento electronico","DOCUMENTO ELECTRONICO", "DOCUMENTO ELECTRÓNICO"]

class ProcessResult(BaseModel):
    """Resultado del procesamiento de facturas."""
    success: bool
    message: str
    invoice_count: int = 0
    invoices: List[InvoiceData] = []

class JobStatus(BaseModel):
    """Estado del job programado."""
    running: bool
    next_run: Optional[str] = None
    interval_minutes: int
    last_run: Optional[str] = None
    last_result: Optional[ProcessResult] = None
