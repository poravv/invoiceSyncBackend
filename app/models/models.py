from pydantic import BaseModel, Field
from typing import Optional, List, Union
from datetime import datetime
from datetime import date
from app.utils.date_utils import try_parse_date

def safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, list):
            for item in value:
                try:
                    if isinstance(item, (int, float)):
                        return float(item)
                    elif isinstance(item, str) and item.strip():
                        cleaned = item.replace(',', '').replace('.', '', item.count('.') - 1 if '.' in item else 0)
                        return float(cleaned)
                except (ValueError, TypeError):
                    continue
            return default
        if isinstance(value, str):
            if not value.strip():
                return default
            try:
                cleaned = value.replace(',', '').replace(' ', '')
                if cleaned.count('.') > 1:
                    parts = cleaned.rsplit('.', 1)
                    cleaned = parts[0].replace('.', '') + '.' + parts[1]
                return float(cleaned)
            except (ValueError, TypeError):
                return default
        if isinstance(value, (int, float)):
            return float(value)
        return safe_float(str(value), default)
    except Exception:
        return default

class ProductoFactura(BaseModel):
    articulo: Optional[str] = ""
    cantidad: Optional[float] = 0.0
    precio_unitario: Optional[float] = 0.0
    total: Optional[float] = 0.0
    iva: Optional[int] = 0

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
    cantidad_articulos: Optional[int] = 0
    subtotal: Optional[float] = 0.0
    total_a_pagar: Optional[float] = 0.0
    iva_0: Optional[float] = Field(0.0, alias="iva_0%")
    iva_5: Optional[float] = Field(0.0, alias="iva_5%")
    iva_10: Optional[float] = Field(0.0, alias="iva_10%")
    total_iva: Optional[float] = 0.0

class ClienteData(BaseModel):
    nombre: Optional[str] = ""
    ruc: Optional[str] = ""
    email: Optional[str] = ""

class InvoiceDataASCONT(BaseModel):
    fecha: Optional[datetime] = None
    tipo_documento: Optional[str] = "FC"
    numero_documento: Optional[str] = ""
    ruc_proveedor: Optional[str] = ""
    razon_social_proveedor: Optional[str] = ""
    condicion_compra: Optional[str] = "CONTADO"
    gravado_10: Optional[float] = 0.0
    iva_10: Optional[float] = 0.0
    gravado_5: Optional[float] = 0.0
    iva_5: Optional[float] = 0.0
    exento: Optional[float] = 0.0
    total_factura: Optional[float] = 0.0
    timbrado: Optional[str] = ""
    cdc: Optional[str] = ""
    moneda: Optional[str] = "GS"
    tipo_cambio: Optional[float] = 1.0
    descripcion_factura: Optional[str] = ""
    detalle_articulos: Optional[str] = ""
    email_origen: Optional[str] = ""
    procesado_en: Optional[datetime] = Field(default_factory=datetime.now)
    mes_proceso: Optional[str] = ""
    ruc_emisor: Optional[str] = ""
    nombre_emisor: Optional[str] = ""
    numero_factura: Optional[str] = ""
    monto_total: Optional[float] = 0.0
    iva: Optional[float] = 0.0
    pdf_path: Optional[str] = ""
    ruc_cliente: Optional[str] = ""
    nombre_cliente: Optional[str] = ""
    email_cliente: Optional[str] = ""
    condicion_venta: Optional[str] = ""
    subtotal_exentas: Optional[float] = 0.0
    subtotal_5: Optional[float] = 0.0
    subtotal_10: Optional[float] = 0.0
    actividad_economica: Optional[str] = ""
    empresa: Optional[EmpresaData] = None
    timbrado_data: Optional[TimbradoData] = None
    factura_data: Optional[FacturaData] = None
    productos: List[ProductoFactura] = Field(default_factory=list)
    totales: Optional[TotalesData] = None
    cliente: Optional[ClienteData] = None
    observacion: Optional[str] = ""

    # def __init__(self, **data):
    #     super().__init__(**data)
    #     self._map_legacy_fields()
    #     self.mes_proceso = self.fecha.strftime("%Y-%m") if self.fecha else datetime.now().strftime("%Y-%m")
        

    # def _map_legacy_fields(self):
    #     if not self.ruc_proveedor and self.ruc_emisor:
    #         self.ruc_proveedor = self.ruc_emisor
    #     if not self.razon_social_proveedor and self.nombre_emisor:
    #         self.razon_social_proveedor = self.nombre_emisor
    #     if not self.numero_documento and self.numero_factura:
    #         self.numero_documento = self.numero_factura
    #     if self.condicion_venta:
    #         self.condicion_compra = self.condicion_venta.upper()
    #     self.tipo_documento = "CR" if self.condicion_compra == "CREDITO" else "CO"
    #     if not self.gravado_10 and self.subtotal_10:
    #         self.gravado_10 = self.subtotal_10
    #     if not self.gravado_5 and self.subtotal_5:
    #         self.gravado_5 = self.subtotal_5
    #     if not self.exento and self.subtotal_exentas:
    #         self.exento = self.subtotal_exentas
    #     if not self.total_factura and self.monto_total:
    #         self.total_factura = self.monto_total
    #     if not self.iva_10 and self.gravado_10:
    #         self.iva_10 = self.gravado_10 * 0.10
    #     if not self.iva_5 and self.gravado_5:
    #         self.iva_5 = self.gravado_5 * 0.05
    #     if self.moneda == "PYG":
    #         self.moneda = "GS"
    #     if self.productos:
    #         articulos = [str(p.articulo) for p in self.productos if p.articulo]
    #         self.detalle_articulos = ", ".join(articulos)

    @classmethod
    def from_dict(cls, data: dict, email_metadata: dict = None):
        fecha_parsed = try_parse_date(data.get("fecha"))
        return cls(
            fecha=fecha_parsed,
            numero_documento=data.get("numero_factura"),
            ruc_proveedor=data.get("ruc_emisor"),
            razon_social_proveedor=data.get("nombre_emisor"),
            condicion_compra=(data.get("condicion_venta") or "CONTADO").upper(),
            gravado_10=safe_float(data.get("subtotal_10")),
            iva_10=safe_float(data.get("iva_10")),
            gravado_5=safe_float(data.get("subtotal_5")),
            iva_5=safe_float(data.get("iva_5")),
            exento=safe_float(data.get("subtotal_exentas")),
            total_factura=safe_float(data.get("monto_total")),
            timbrado=data.get("timbrado"),
            cdc=data.get("cdc"),
            moneda=data.get("moneda", "GS"),
            tipo_cambio=safe_float(data.get("tipo_cambio", 1.0)),
            descripcion_factura=data.get("descripcion_factura", ""),
            ruc_emisor=data.get("ruc_emisor"),
            nombre_emisor=data.get("nombre_emisor"),
            numero_factura=data.get("numero_factura"),
            monto_total=safe_float(data.get("monto_total")),
            iva=safe_float(data.get("iva")),
            ruc_cliente=data.get("ruc_cliente"),
            nombre_cliente=data.get("nombre_cliente"),
            email_cliente=data.get("email_cliente"),
            condicion_venta=data.get("condicion_venta"),
            subtotal_exentas=safe_float(data.get("subtotal_exentas")),
            subtotal_5=safe_float(data.get("subtotal_5")),
            subtotal_10=safe_float(data.get("subtotal_10")),
            actividad_economica=data.get("actividad_economica"),
            empresa=EmpresaData(**data["empresa"]) if data.get("empresa") else None,
            timbrado_data=TimbradoData(**data["timbrado_data"]) if data.get("timbrado_data") else None,
            factura_data=FacturaData(**data["factura_data"]) if data.get("factura_data") else None,
            productos=[ProductoFactura(**p) for p in data.get("productos", [])],
            totales=TotalesData(**data["totales"]) if data.get("totales") else None,
            cliente=ClienteData(**data["cliente"]) if data.get("cliente") else None,
            email_origen=email_metadata.get("sender") if email_metadata else None,
        )

InvoiceData = InvoiceDataASCONT

class MultiEmailConfig(BaseModel):
    name: str
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool = True
    search_criteria: str = "UNSEEN"
    search_terms: Optional[List[str]] = None
    provider: str = "other"
    enabled: bool = True

class EmailConfig(BaseModel):
    host: str
    port: int
    username: str
    password: str
    search_criteria: str = "UNSEEN"
    search_terms: List[str] = [
        "factura", "facturacion", "factura electronica", "comprobante",
        "Documento Electronico", "Documento electronico",
        "documento electrónico", "documento electronico",
        "DOCUMENTO ELECTRONICO", "DOCUMENTO ELECTRÓNICO"
    ]

class ProcessResult(BaseModel):
    success: bool
    message: str
    invoice_count: int = 0
    invoices: List[InvoiceData] = []
    excel_files: List[str] = []

class JobStatus(BaseModel):
    running: bool
    next_run: Optional[str] = None
    interval_minutes: int
    last_run: Optional[str] = None
    last_result: Optional[ProcessResult] = None

class ExcelFileInfo(BaseModel):
    filename: str
    year_month: str
    display_name: str
    path: str
    size: int
    last_modified: datetime
    invoice_count: int

class ExcelFileList(BaseModel):
    files: List[ExcelFileInfo]
    total_count: int