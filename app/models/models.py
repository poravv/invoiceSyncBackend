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

class InvoiceDataASCONT(BaseModel):
    """Modelo adaptado al formato ASCONT."""
    # Campos básicos requeridos por ASCONT
    fecha: Optional[datetime] = None
    tipo_documento: str = Field(default="FC")  # FC = Factura Contado, CR = Crédito
    numero_documento: Optional[str] = None  # Número completo de factura
    ruc_proveedor: Optional[str] = None  # RUC del emisor
    razon_social_proveedor: Optional[str] = None  # Nombre del emisor
    condicion_compra: str = Field(default="CONTADO")  # CONTADO/CREDITO
    
    # Importes (formato ASCONT)
    gravado_10: float = Field(default=0.0)  # Subtotal gravado al 10%
    iva_10: float = Field(default=0.0)  # IVA 10%
    gravado_5: float = Field(default=0.0)  # Subtotal gravado al 5%
    iva_5: float = Field(default=0.0)  # IVA 5%
    exento: float = Field(default=0.0)  # Monto exento
    total_factura: float = Field(default=0.0)  # Total de la factura
    
    # Campos adicionales de control
    timbrado: Optional[str] = None
    cdc: Optional[str] = None
    moneda: str = Field(default="PYG")
    
    # Campos técnicos
    email_origen: Optional[str] = None
    procesado_en: datetime = Field(default_factory=datetime.now)
    mes_proceso: str = Field(default="")  # YYYY-MM para agrupación
    
    # Campos legacy para compatibilidad
    ruc_emisor: Optional[str] = None
    nombre_emisor: Optional[str] = None
    numero_factura: Optional[str] = None
    monto_total: float = Field(default=0.0)
    iva: float = Field(default=0.0)
    pdf_path: Optional[str] = None
    ruc_cliente: Optional[str] = None
    nombre_cliente: Optional[str] = None
    email_cliente: Optional[str] = None
    condicion_venta: Optional[str] = None
    subtotal_exentas: float = Field(default=0.0)
    subtotal_5: float = Field(default=0.0)
    subtotal_10: float = Field(default=0.0)
    actividad_economica: Optional[str] = None
    
    # Datos estructurados
    empresa: Optional[EmpresaData] = None
    timbrado_data: Optional[TimbradoData] = None
    factura_data: Optional[FacturaData] = None
    productos: List[ProductoFactura] = Field(default_factory=list)
    totales: Optional[TotalesData] = None
    cliente: Optional[ClienteData] = None

    def __init__(self, **data):
        super().__init__(**data)
        # Auto-mapear campos legacy a formato ASCONT
        self._map_legacy_fields()
        # Calcular mes de proceso
        if self.fecha:
            self.mes_proceso = self.fecha.strftime("%Y-%m")
        else:
            self.mes_proceso = datetime.now().strftime("%Y-%m")

    def _map_legacy_fields(self):
        """Mapea campos legacy al formato ASCONT."""
        # Mapear RUC y nombre del proveedor
        if not self.ruc_proveedor and self.ruc_emisor:
            self.ruc_proveedor = self.ruc_emisor
        if not self.razon_social_proveedor and self.nombre_emisor:
            self.razon_social_proveedor = self.nombre_emisor
        
        # Mapear número de documento
        if not self.numero_documento and self.numero_factura:
            self.numero_documento = self.numero_factura
        
        # Mapear condición de compra
        if self.condicion_venta:
            self.condicion_compra = self.condicion_venta.upper()
        
        # Mapear tipo de documento basado en condición
        if self.condicion_compra == "CREDITO":
            self.tipo_documento = "CR"
        
        # Mapear importes
        if not self.gravado_10 and self.subtotal_10:
            self.gravado_10 = self.subtotal_10
        if not self.gravado_5 and self.subtotal_5:
            self.gravado_5 = self.subtotal_5
        if not self.exento and self.subtotal_exentas:
            self.exento = self.subtotal_exentas
        if not self.total_factura and self.monto_total:
            self.total_factura = self.monto_total
        
        # Calcular IVAs si no están presentes
        if not self.iva_10 and self.gravado_10:
            self.iva_10 = self.gravado_10 * 0.10
        if not self.iva_5 and self.gravado_5:
            self.iva_5 = self.gravado_5 * 0.05

    @classmethod
    def from_dict(cls, data: dict, email_metadata: dict = None):
        try:
            # Convertir al nuevo modelo
            invoice_data = cls(
                fecha=try_parse_date(data.get("fecha")),
                numero_documento=data.get("numero_factura"),
                ruc_proveedor=data.get("ruc_emisor"),
                razon_social_proveedor=data.get("nombre_emisor"),
                condicion_compra=data.get("condicion_venta", "CONTADO").upper(),
                gravado_10=float(data.get("subtotal_10", 0)),
                iva_10=float(data.get("iva_10", 0)),
                gravado_5=float(data.get("subtotal_5", 0)),
                iva_5=float(data.get("iva_5", 0)),
                exento=float(data.get("subtotal_exentas", 0)),
                total_factura=float(data.get("monto_total", 0)),
                timbrado=data.get("timbrado"),
                cdc=data.get("cdc"),
                moneda=data.get("moneda", "PYG"),
                
                # Campos legacy para compatibilidad
                ruc_emisor=data.get("ruc_emisor"),
                nombre_emisor=data.get("nombre_emisor"),
                numero_factura=data.get("numero_factura"),
                monto_total=float(data.get("monto_total", 0)),
                iva=float(data.get("iva", 0)),
                ruc_cliente=data.get("ruc_cliente"),
                nombre_cliente=data.get("nombre_cliente"),
                email_cliente=data.get("email_cliente"),
                condicion_venta=data.get("condicion_venta"),
                subtotal_exentas=float(data.get("subtotal_exentas", 0)),
                subtotal_5=float(data.get("subtotal_5", 0)),
                subtotal_10=float(data.get("subtotal_10", 0)),
                actividad_economica=data.get("actividad_economica"),
                
                empresa=EmpresaData(**data["empresa"]) if data.get("empresa") else None,
                timbrado_data=TimbradoData(**data["timbrado_data"]) if data.get("timbrado_data") else None,
                factura_data=FacturaData(**data["factura_data"]) if data.get("factura_data") else None,
                productos=[ProductoFactura(**p) for p in data.get("productos", [])],
                totales=TotalesData(**data["totales"]) if data.get("totales") else None,
                cliente=ClienteData(**data["cliente"]) if data.get("cliente") else None,
                email_origen=email_metadata.get("sender") if email_metadata else None,
            )
            
            return invoice_data
            
        except Exception as e:
            print(f"[InvoiceDataASCONT.from_dict] Error: {e}")
            return None

# Mantener InvoiceData para compatibilidad hacia atrás
InvoiceData = InvoiceDataASCONT

class MultiEmailConfig(BaseModel):
    """Configuración para múltiples correos."""
    name: str
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool = True
    search_criteria: str = "UNSEEN"
    search_terms: Optional[List[str]] = None  # Si es None, usa EMAIL_SEARCH_TERMS global
    provider: str = "other"  # "gmail", "outlook", "other"
    enabled: bool = True

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
    excel_files: List[str] = []  # Lista de archivos Excel generados

class JobStatus(BaseModel):
    """Estado del job programado."""
    running: bool
    next_run: Optional[str] = None
    interval_minutes: int
    last_run: Optional[str] = None
    last_result: Optional[ProcessResult] = None

class ExcelFileInfo(BaseModel):
    """Información de archivo Excel mensual."""
    filename: str
    year_month: str  # YYYY-MM - Cambiado de 'month' a 'year_month' para coincidir con frontend
    display_name: str  # Nombre amigable para mostrar
    path: str
    size: int
    last_modified: datetime
    invoice_count: int

class ExcelFileList(BaseModel):
    """Lista de archivos Excel con metadatos."""
    files: List[ExcelFileInfo]
    total_count: int
