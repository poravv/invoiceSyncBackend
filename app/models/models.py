from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app.utils.date_utils import try_parse_date

class ProductoFactura(BaseModel):
    """Modelo para los productos/servicios en la factura."""
    articulo: str = ""
    cantidad: float = 0
    precio_unitario: float = 0
    total: float = 0

class EmpresaData(BaseModel):
    nombre: Optional[str] = ""
    ruc: Optional[str] = ""
    direccion: Optional[str] = ""
    telefono: Optional[str] = ""
    actividad_economica: Optional[str] = ""

class TimbradoData(BaseModel):
    nro: Optional[str] = ""
    fecha_inicio_vigencia: Optional[str] = ""
    valido_hasta: Optional[str] = ""

class FacturaData(BaseModel):
    contado_nro: Optional[str] = ""
    fecha: Optional[str] = ""
    caja_nro: Optional[str] = ""
    cdc: Optional[str] = ""
    condicion_venta: Optional[str] = ""

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
    email: Optional[str] = ""

class InvoiceData(BaseModel):
    fecha: Optional[datetime] = None
    ruc_emisor: Optional[str] = None
    nombre_emisor: Optional[str] = None
    numero_factura: Optional[str] = None
    monto_total: float = Field(default=0.0)
    iva: float = Field(default=0.0)
    pdf_path: Optional[str] = None
    email_origen: Optional[str] = None
    procesado_en: datetime = Field(default_factory=datetime.now)

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

    empresa: Optional[EmpresaData] = None
    timbrado_data: Optional[TimbradoData] = None
    factura_data: Optional[FacturaData] = None
    productos: List[ProductoFactura] = Field(default_factory=list)
    totales: Optional[TotalesData] = None
    cliente: Optional[ClienteData] = None

    @classmethod
    def from_dict(cls, data: dict, email_metadata: dict = None):
        try:
            return cls(
                fecha=try_parse_date(data.get("fecha")),
                ruc_emisor=data.get("ruc_emisor"),
                nombre_emisor=data.get("nombre_emisor"),
                numero_factura=data.get("numero_factura"),
                monto_total=data.get("monto_total", 0),
                iva=data.get("iva", 0),
                timbrado=data.get("timbrado"),
                cdc=data.get("cdc"),
                ruc_cliente=data.get("ruc_cliente"),
                nombre_cliente=data.get("nombre_cliente"),
                email_cliente=data.get("email_cliente"),
                condicion_venta=data.get("condicion_venta"),
                moneda=data.get("moneda", "PYG"),
                subtotal_exentas=data.get("subtotal_exentas", 0),
                subtotal_5=data.get("subtotal_5", 0),
                subtotal_10=data.get("subtotal_10", 0),
                actividad_economica=data.get("actividad_economica"),
                empresa=EmpresaData(**data["empresa"]) if data.get("empresa") else None,
                timbrado_data=TimbradoData(**data["timbrado_data"]) if data.get("timbrado_data") else None,
                factura_data=FacturaData(**data["factura_data"]) if data.get("factura_data") else None,
                productos=[ProductoFactura(**p) for p in data.get("productos", [])],
                totales=TotalesData(**data["totales"]) if data.get("totales") else None,
                cliente=ClienteData(**data["cliente"]) if data.get("cliente") else None,
                email_origen=email_metadata.get("sender") if email_metadata else None,
            )
        except Exception as e:
            print(f"[InvoiceData.from_dict] Error: {e}")
            return None
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
