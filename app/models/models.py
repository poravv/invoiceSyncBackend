# app/models/models.py

from __future__ import annotations
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

from app.utils.date_utils import try_parse_date

# -----------------------
# Utilidades
# -----------------------
def safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, list):
            for item in value:
                try:
                    return float(str(item).replace(',', '').replace(' ', ''))
                except Exception:
                    continue
            return default
        s = str(value).strip()
        if not s:
            return default
        # normaliza: quita espacios y separadores de miles
        s = s.replace(' ', '')
        if s.count('.') > 1:
            # deja solo el último punto como decimal
            head, dot, tail = s.rpartition('.')
            head = head.replace('.', '')
            s = head + '.' + tail
        s = s.replace(',', '')
        return float(s)
    except Exception:
        return default

# -----------------------
# Submodelos
# -----------------------
class ProductoFactura(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    articulo: Optional[str] = ""
    cantidad: Optional[float] = 0.0
    precio_unitario: Optional[float] = 0.0
    total: Optional[float] = 0.0
    iva: Optional[int] = 0

class EmpresaData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nombre: Optional[str] = ""
    ruc: Optional[str] = ""
    direccion: Optional[str] = ""
    telefono: Optional[str] = ""
    actividad_economica: Optional[str] = ""

class TimbradoData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nro: Optional[str] = ""
    fecha_inicio_vigencia: Optional[str] = ""
    valido_hasta: Optional[str] = ""

class FacturaData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    contado_nro: Optional[str] = ""
    fecha: Optional[str] = ""
    caja_nro: Optional[str] = ""
    cdc: Optional[str] = ""
    condicion_venta: Optional[str] = ""

class TotalesData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    cantidad_articulos: Optional[int] = 0
    subtotal: Optional[float] = 0.0
    total_a_pagar: Optional[float] = 0.0
    iva_0: Optional[float] = Field(0.0, alias="iva_0%")
    iva_5: Optional[float] = Field(0.0, alias="iva_5%")
    iva_10: Optional[float] = Field(0.0, alias="iva_10%")
    total_iva: Optional[float] = 0.0

class ClienteData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nombre: Optional[str] = ""
    ruc: Optional[str] = ""
    email: Optional[str] = ""

# -----------------------
# Modelo principal (ASCONT)
# -----------------------
class InvoiceDataASCONT(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    fecha: Optional[datetime] = None
    tipo_documento: Optional[str] = "CO"  # CO contado, CR crédito
    numero_documento: Optional[str] = ""
    ruc_proveedor: Optional[str] = ""
    razon_social_proveedor: Optional[str] = ""
    condicion_compra: Optional[str] = "CONTADO"  # CONTADO | CREDITO

    gravado_10: Optional[float] = 0.0
    iva_10: Optional[float] = 0.0
    gravado_5: Optional[float] = 0.0
    iva_5: Optional[float] = 0.0
    exento: Optional[float] = 0.0
    total_factura: Optional[float] = 0.0

    timbrado: Optional[str] = ""
    cdc: Optional[str] = ""
    moneda: Optional[str] = "GS"   # "GS" si es PYG, "USD" u otra tal cual en factura
    tipo_cambio: Optional[float] = 1.0

    descripcion_factura: Optional[str] = ""
    detalle_articulos: Optional[str] = ""
    email_origen: Optional[str] = ""

    procesado_en: Optional[datetime] = Field(default_factory=datetime.now)
    mes_proceso: Optional[str] = ""

    # campos “legacy” que siguen llegando desde el parser OpenAI
    ruc_emisor: Optional[str] = ""
    nombre_emisor: Optional[str] = ""
    numero_factura: Optional[str] = ""
    monto_total: Optional[float] = 0.0
    iva: Optional[float] = 0.0
    pdf_path: Optional[str] = ""

    ruc_cliente: Optional[str] = ""
    nombre_cliente: Optional[str] = ""
    email_cliente: Optional[str] = ""
    condicion_venta: Optional[str] = ""  # CONTADO | CREDITO

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

    @classmethod
    def from_dict(cls, data: dict, email_metadata: dict = None):

        fecha_parsed = try_parse_date(data.get("fecha"))

        condicion_venta = (data.get("condicion_venta") or "CONTADO").upper()
        condicion_compra = condicion_venta
        tipo_documento = "CR" if "CREDITO" in condicion_venta else "CO"

        moneda = (data.get("moneda") or "GS").upper()
        if moneda == "PYG":
            moneda = "GS"

        # Fallbacks seguros
        td = data.get("timbrado_data") or {}
        fd = data.get("factura_data") or {}

        timbrado = data.get("timbrado") or td.get("nro") or ""
        cdc = data.get("cdc") or fd.get("cdc") or ""

        numero_doc = data.get("numero_factura") or fd.get("contado_nro") or ""
        
        fecha_parsed = try_parse_date(data.get("fecha"))

        condicion_venta = (data.get("condicion_venta") or "CONTADO").upper()
        condicion_compra = condicion_venta  # mismo valor
        tipo_documento = "CR" if "CREDITO" in condicion_venta else "CO"

        moneda = (data.get("moneda") or "GS").upper()
        if moneda == "PYG":
            moneda = "GS"

        return cls(
            fecha=fecha_parsed,
            tipo_documento=tipo_documento,
            numero_documento=numero_doc,
            ruc_proveedor=data.get("ruc_emisor"),
            razon_social_proveedor=data.get("nombre_emisor"),
            condicion_compra=condicion_compra,

            gravado_10=safe_float(data.get("subtotal_10")),
            iva_10=safe_float(data.get("iva_10")),
            gravado_5=safe_float(data.get("subtotal_5")),
            iva_5=safe_float(data.get("iva_5")),
            exento=safe_float(data.get("subtotal_exentas")),
            total_factura=safe_float(data.get("monto_total")),

            timbrado=timbrado,
            cdc=cdc,
            moneda=moneda,
            tipo_cambio=safe_float(data.get("tipo_cambio", 0.0)),

            descripcion_factura=data.get("descripcion_factura", ""),

            ruc_emisor=data.get("ruc_emisor"),
            nombre_emisor=data.get("nombre_emisor"),
            numero_factura=data.get("numero_factura"),
            monto_total=safe_float(data.get("monto_total")),
            iva=safe_float(data.get("iva")),

            ruc_cliente=data.get("ruc_cliente"),
            nombre_cliente=data.get("nombre_cliente"),
            email_cliente=data.get("email_cliente"),
            condicion_venta=condicion_venta,

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

            email_origen=(email_metadata or {}).get("sender"),
            mes_proceso=fecha_parsed.strftime("%Y-%m") if fecha_parsed else datetime.now().strftime("%Y-%m"),
        )

# Alias para compatibilidad (¡esto arregla tu ImportError!)
InvoiceData = InvoiceDataASCONT

# -----------------------
# Configs y resultados
# -----------------------
class MultiEmailConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    host: str
    port: int
    username: str
    password: str
    search_criteria: str = "UNSEEN"
    search_terms: List[str] = Field(default_factory=lambda: [
        "factura", "facturacion", "factura electronica", "comprobante",
        "Documento Electronico", "Documento electronico",
        "documento electrónico", "documento electronico",
        "DOCUMENTO ELECTRONICO", "DOCUMENTO ELECTRÓNICO"
    ])

class ProcessResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    success: bool
    message: str
    invoice_count: int = 0
    invoices: List[InvoiceData] = Field(default_factory=list)  # <- evita lista mutable global
    excel_files: List[str] = Field(default_factory=list)       # <- idem

class JobStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    running: bool
    next_run: Optional[str] = None
    interval_minutes: int
    last_run: Optional[str] = None
    last_result: Optional[ProcessResult] = None

class ExcelFileInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    filename: str
    year_month: str
    display_name: str
    path: str
    size: int
    last_modified: datetime
    invoice_count: int

class ExcelFileList(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    files: List[ExcelFileInfo]
    total_count: int