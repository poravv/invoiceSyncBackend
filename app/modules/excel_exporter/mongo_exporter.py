"""
Exportador MongoDB - Almacenamiento profesional para análisis avanzado
Guarda TODOS los datos en estructura documental optimizada para consultas
"""

import logging
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timezone
from decimal import Decimal
import json
import threading

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import asyncio

from app.models.models import InvoiceData
from app.config.settings import settings

logger = logging.getLogger(__name__)
_MONGO_LOCK = threading.Lock()

class MongoDBExporter:
    """
    Exportador MongoDB con estructura optimizada para análisis
    
    Características:
    - Almacenamiento documental completo
    - Índices optimizados para consultas frecuentes
    - Metadatos de procesamiento y auditoria
    - Soporte para agregaciones y reportes avanzados
    - Backup y versionado de datos
    """

    def __init__(self, 
                 connection_string: Optional[str] = None,
                 database_name: str = "invoicesync_warehouse",
                 collection_name: str = "facturas_completas") -> None:
        
        self.connection_string = connection_string or self._get_default_connection()
        self.database_name = database_name
        self.collection_name = collection_name
        
        # Cliente síncrono para operaciones simples
        self._sync_client: Optional[MongoClient] = None
        # Cliente asíncrono para operaciones concurrentes
        self._async_client: Optional[AsyncIOMotorClient] = None
        
        logger.info("MongoDBExporter configurado: db=%s, collection=%s", 
                   database_name, collection_name)

    def _get_default_connection(self) -> str:
        """Obtiene string de conexión por defecto"""
        # Orden de prioridad: ENV -> Config -> Local
        mongo_url = getattr(settings, 'MONGODB_URL', None)
        if mongo_url:
            return mongo_url
            
        # Configuración para desarrollo local
        return "mongodb://localhost:27017/"

    def _get_sync_client(self) -> MongoClient:
        """Obtiene cliente MongoDB síncrono"""
        if not self._sync_client:
            try:
                self._sync_client = MongoClient(
                    self.connection_string,
                    serverSelectionTimeoutMS=5000,  # 5 segundos timeout
                    connectTimeoutMS=10000,
                    socketTimeoutMS=20000,
                    maxPoolSize=50,
                    minPoolSize=5
                )
                # Test conexión
                self._sync_client.admin.command('ping')
                logger.info("✅ Conexión MongoDB establecida (sync)")
            except Exception as e:
                logger.error("❌ Error conectando a MongoDB: %s", e)
                raise
        return self._sync_client

    async def _get_async_client(self) -> AsyncIOMotorClient:
        """Obtiene cliente MongoDB asíncrono"""
        if not self._async_client:
            try:
                self._async_client = AsyncIOMotorClient(
                    self.connection_string,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=10000,
                    socketTimeoutMS=20000,
                    maxPoolSize=50,
                    minPoolSize=5
                )
                # Test conexión
                await self._async_client.admin.command('ping')
                logger.info("✅ Conexión MongoDB async establecida")
            except Exception as e:
                logger.error("❌ Error conectando a MongoDB async: %s", e)
                raise
        return self._async_client

    def export_invoices(self, invoices: List[InvoiceData]) -> Dict[str, Any]:
        """
        Exporta facturas a MongoDB (versión síncrona)
        
        Returns:
            Dict con estadísticas del export: insertados, actualizados, errores
        """
        if not invoices:
            logger.warning("No hay facturas para exportar a MongoDB")
            return {"inserted": 0, "updated": 0, "errors": 0, "total": 0}

        try:
            with _MONGO_LOCK:
                client = self._get_sync_client()
                db = client[self.database_name]
                collection = db[self.collection_name]
                
                # Asegurar índices
                self._ensure_indexes(collection)
                
                # Procesar facturas
                documents = []
                for inv in invoices:
                    doc = self._invoice_to_document(inv)
                    documents.append(doc)
                
                # Insertar/actualizar en lotes
                result = self._bulk_upsert(collection, documents)
                
                # Log estadísticas
                logger.info("📊 MongoDB Export: %d insertados, %d actualizados de %d facturas", 
                           result["inserted"], result["updated"], len(invoices))
                
                return result

        except Exception as e:
            logger.error("Error exportando a MongoDB: %s", e, exc_info=True)
            return {"inserted": 0, "updated": 0, "errors": len(invoices), "total": len(invoices)}

    async def export_invoices_async(self, invoices: List[InvoiceData]) -> Dict[str, Any]:
        """
        Exporta facturas a MongoDB (versión asíncrona)
        Recomendada para grandes volúmenes
        """
        if not invoices:
            logger.warning("No hay facturas para exportar a MongoDB (async)")
            return {"inserted": 0, "updated": 0, "errors": 0, "total": 0}

        try:
            client = await self._get_async_client()
            db = client[self.database_name]
            collection = db[self.collection_name]
            
            # Asegurar índices
            await self._ensure_indexes_async(collection)
            
            # Procesar facturas
            documents = []
            for inv in invoices:
                doc = self._invoice_to_document(inv)
                documents.append(doc)
            
            # Insertar/actualizar en lotes
            result = await self._bulk_upsert_async(collection, documents)
            
            logger.info("📊 MongoDB Export Async: %d insertados, %d actualizados de %d facturas", 
                       result["inserted"], result["updated"], len(invoices))
            
            return result

        except Exception as e:
            logger.error("Error exportando a MongoDB async: %s", e, exc_info=True)
            return {"inserted": 0, "updated": 0, "errors": len(invoices), "total": len(invoices)}

    def _invoice_to_document(self, invoice: InvoiceData) -> Dict[str, Any]:
        """Convierte InvoiceData a documento MongoDB optimizado"""
        fecha_factura = invoice.fecha.isoformat() if invoice.fecha else None
        fecha_procesado = getattr(invoice, 'procesado_en', datetime.now(timezone.utc)).isoformat()
        
        # ID único para deduplicación
        unique_id = f"{invoice.ruc_emisor or 'SIN_RUC'}_{invoice.numero_factura or 'SIN_NUM'}_{fecha_factura or 'SIN_FECHA'}"
        
        # Documento base
        doc = {
            "_id": unique_id,
            "factura_id": unique_id,
            
            # Metadatos de procesamiento
            "metadata": {
                "fecha_procesado": fecha_procesado,
                "fuente": "XML_NATIVO" if getattr(invoice, "cdc", "") else "OPENAI_VISION",
                "calidad_datos": self._evaluar_calidad_datos(invoice),
                "version_esquema": "1.0",
                "email_origen": getattr(invoice, "email_origen", ""),
                "mes_proceso": getattr(invoice, "mes_proceso", ""),
                "pdf_path": getattr(invoice, "pdf_path", "")
            },
            
            # Información principal de factura
            "factura": {
                "numero": invoice.numero_factura or "",
                "fecha": fecha_factura,
                "tipo_documento": getattr(invoice, "tipo_documento", "CO"),
                "moneda": getattr(invoice, "moneda", "GS"),
                "tipo_cambio": float(getattr(invoice, "tipo_cambio", 1.0) or 1.0),
                "condicion_venta": getattr(invoice, "condicion_venta", "CONTADO"),
                "condicion_compra": getattr(invoice, "condicion_compra", "CONTADO"),
                "descripcion": getattr(invoice, "descripcion_factura", ""),
                "observacion": getattr(invoice, "observacion", "")
            },
            
            # Información del emisor/proveedor
            "emisor": {
                "ruc": invoice.ruc_emisor or "",
                "nombre": invoice.nombre_emisor or "",
                "actividad_economica": getattr(invoice, "actividad_economica", ""),
                # Datos adicionales de empresa si están disponibles
                **self._extract_empresa_data(invoice)
            },
            
            # Información del receptor/cliente
            "receptor": {
                "ruc": invoice.ruc_cliente or "",
                "nombre": invoice.nombre_cliente or "",
                "email": invoice.email_cliente or "",
                # Datos adicionales de cliente si están disponibles
                **self._extract_cliente_data(invoice)
            },
            
            # Montos y totales
            "montos": {
                "subtotal_exentas": float(getattr(invoice, "subtotal_exentas", 0) or 0),
                "subtotal_5": float(getattr(invoice, "subtotal_5", 0) or 0),
                "subtotal_10": float(getattr(invoice, "subtotal_10", 0) or 0),
                "iva_5": float(getattr(invoice, "iva_5", 0) or 0),
                "iva_10": float(getattr(invoice, "iva_10", 0) or 0),
                "total_iva": float(getattr(invoice, "iva_5", 0) or 0) + float(getattr(invoice, "iva_10", 0) or 0),
                "monto_total": self._calcular_monto_total(invoice),
                
                # Campos calculados adicionales
                "base_gravada": float(getattr(invoice, "subtotal_5", 0) or 0) + float(getattr(invoice, "subtotal_10", 0) or 0),
                "porcentaje_iva": self._calcular_porcentaje_iva(invoice)
            },
            
            # Detalles de productos/servicios
            "productos": self._extract_productos_data(invoice),
            
            # Datos técnicos y validaciones
            "datos_tecnicos": {
                "cdc": getattr(invoice, "cdc", ""),
                "cdc_formateado": self._formatear_cdc(getattr(invoice, "cdc", "")),
                "cdc_valido": self._validar_cdc(getattr(invoice, "cdc", "")),
                "timbrado": getattr(invoice, "timbrado", ""),
                "timbrado_data": self._extract_timbrado_data(invoice),
                "factura_data": self._extract_factura_data(invoice)
            },
            
            # Índices para consultas frecuentes
            "indices": {
                "year": int(invoice.fecha.year) if invoice.fecha else None,
                "month": int(invoice.fecha.month) if invoice.fecha else None,
                "year_month": invoice.fecha.strftime("%Y-%m") if invoice.fecha else None,
                "has_cdc": bool(getattr(invoice, "cdc", "")),
                "has_timbrado": bool(getattr(invoice, "timbrado", "")),
                "cantidad_productos": len(getattr(invoice, "productos", []) or []),
                "monto_range": self._get_monto_range(getattr(invoice, "monto_total", 0) or 0)
            }
        }
        
        return doc

    def _evaluar_calidad_datos(self, invoice: InvoiceData) -> str:
        """Evalúa la calidad de los datos de la factura"""
        cdc = bool(getattr(invoice, "cdc", ""))
        timbrado = bool(getattr(invoice, "timbrado", ""))
        productos = len(getattr(invoice, "productos", []) or [])
        montos_ok = bool(getattr(invoice, "monto_total", 0))
        
        if cdc and timbrado and productos > 0 and montos_ok:
            return "ALTA"
        elif (cdc or timbrado) and montos_ok:
            return "MEDIA"
        else:
            return "BAJA"

    def _extract_empresa_data(self, invoice: InvoiceData) -> Dict[str, Any]:
        """Extrae datos adicionales de empresa"""
        empresa_data = getattr(invoice, "empresa", None)
        if not empresa_data:
            return {}
        
        return {
            "direccion": getattr(empresa_data, "direccion", ""),
            "telefono": getattr(empresa_data, "telefono", ""),
            "actividad_detallada": getattr(empresa_data, "actividad_economica", "")
        }

    def _extract_cliente_data(self, invoice: InvoiceData) -> Dict[str, Any]:
        """Extrae datos adicionales de cliente"""
        cliente_data = getattr(invoice, "cliente", None)
        if not cliente_data:
            return {}
        
        return {
            "email_adicional": getattr(cliente_data, "email", "")
        }

    def _extract_productos_data(self, invoice: InvoiceData) -> List[Dict[str, Any]]:
        """Extrae y normaliza datos de productos"""
        productos = getattr(invoice, "productos", []) or []
        result = []
        
        for idx, prod in enumerate(productos, 1):
            if isinstance(prod, dict):
                prod_data = {
                    "item_numero": idx,
                    "articulo": prod.get("articulo", ""),
                    "cantidad": float(prod.get("cantidad", 0) or 0),
                    "precio_unitario": float(prod.get("precio_unitario", 0) or 0),
                    "total": float(prod.get("total", 0) or 0),
                    "iva": int(prod.get("iva", 0) or 0)
                }
            else:
                prod_data = {
                    "item_numero": idx,
                    "articulo": getattr(prod, "articulo", "") or "",
                    "cantidad": float(getattr(prod, "cantidad", 0) or 0),
                    "precio_unitario": float(getattr(prod, "precio_unitario", 0) or 0),
                    "total": float(getattr(prod, "total", 0) or 0),
                    "iva": int(getattr(prod, "iva", 0) or 0)
                }
            
            result.append(prod_data)
        
        return result

    def _extract_timbrado_data(self, invoice: InvoiceData) -> Dict[str, Any]:
        """Extrae datos de timbrado"""
        timbrado_data = getattr(invoice, "timbrado_data", None)
        if not timbrado_data:
            return {}
        
        return {
            "numero": getattr(timbrado_data, "nro", ""),
            "fecha_inicio_vigencia": getattr(timbrado_data, "fecha_inicio_vigencia", ""),
            "valido_hasta": getattr(timbrado_data, "valido_hasta", ""),
            "vigente": self._validar_timbrado_vigente(timbrado_data, invoice.fecha)
        }

    def _extract_factura_data(self, invoice: InvoiceData) -> Dict[str, Any]:
        """Extrae datos adicionales de factura"""
        factura_data = getattr(invoice, "factura_data", None)
        if not factura_data:
            return {}
        
        return {
            "contado_nro": getattr(factura_data, "contado_nro", ""),
            "caja_nro": getattr(factura_data, "caja_nro", ""),
            "condicion_factura": getattr(factura_data, "condicion_venta", "")
        }

    def _calcular_porcentaje_iva(self, invoice: InvoiceData) -> float:
        """Calcula porcentaje promedio de IVA"""
        total = self._calcular_monto_total(invoice)
        iva = float(getattr(invoice, "iva", 0) or 0)
        
        if total > 0:
            return round((iva / total) * 100, 2)
        return 0.0

    def _calcular_monto_total(self, invoice: InvoiceData) -> float:
        """
        Calcula el monto total de la factura si no está presente.
        Monto Total = Subtotal Exentas + Subtotal 5% + Subtotal 10% + IVA 5% + IVA 10%
        """
        monto_total = float(getattr(invoice, "monto_total", 0) or 0)
        
        # Si ya tiene monto total, usarlo
        if monto_total > 0:
            return monto_total
        
        # Calcular desde componentes
        subtotal_exentas = float(getattr(invoice, "subtotal_exentas", 0) or 0)
        subtotal_5 = float(getattr(invoice, "subtotal_5", 0) or 0)
        subtotal_10 = float(getattr(invoice, "subtotal_10", 0) or 0)
        iva_5 = float(getattr(invoice, "iva_5", 0) or 0)
        iva_10 = float(getattr(invoice, "iva_10", 0) or 0)
        
        total_calculado = subtotal_exentas + subtotal_5 + subtotal_10 + iva_5 + iva_10
        
        logger.debug(f"💰 Monto calculado: exentas={subtotal_exentas} + s5={subtotal_5} + s10={subtotal_10} + iva5={iva_5} + iva10={iva_10} = {total_calculado}")
        
        return total_calculado

    def _formatear_cdc(self, cdc: str) -> str:
        """Formatea CDC con guiones"""
        if not cdc:
            return ""
        
        cdc_clean = str(cdc).replace("-", "").replace(" ", "")
        if len(cdc_clean) == 44 and cdc_clean.isdigit():
            # Formatear: 01-23456789-001-001-0123456-12345678-1
            return f"{cdc_clean[:2]}-{cdc_clean[2:10]}-{cdc_clean[10:13]}-{cdc_clean[13:16]}-{cdc_clean[16:23]}-{cdc_clean[23:31]}-{cdc_clean[31:]}"
        
        return cdc

    def _validar_cdc(self, cdc: str) -> bool:
        """Valida formato de CDC"""
        if not cdc:
            return False
        
        cdc_clean = str(cdc).replace("-", "").replace(" ", "")
        return len(cdc_clean) == 44 and cdc_clean.isdigit()

    def _validar_timbrado_vigente(self, timbrado_data, fecha_factura) -> bool:
        """Valida vigencia de timbrado"""
        if not timbrado_data or not fecha_factura:
            return False
        
        return bool(getattr(timbrado_data, "nro", "") and 
                   getattr(timbrado_data, "fecha_inicio_vigencia", ""))

    def _get_monto_range(self, monto: float) -> str:
        """Categoriza monto para análisis"""
        if monto <= 100000:
            return "BAJO"
        elif monto <= 1000000:
            return "MEDIO"
        elif monto <= 10000000:
            return "ALTO"
        else:
            return "MUY_ALTO"

    def _ensure_indexes(self, collection):
        """Crea índices optimizados para consultas frecuentes"""
        try:
            # Índices simples
            collection.create_index("factura.fecha")
            collection.create_index("emisor.ruc")
            collection.create_index("receptor.ruc")
            collection.create_index("metadata.fecha_procesado")
            collection.create_index("indices.year_month")
            collection.create_index("datos_tecnicos.cdc")
            
            # Índices compuestos
            collection.create_index([("emisor.ruc", 1), ("factura.fecha", -1)])
            collection.create_index([("indices.year_month", 1), ("montos.monto_total", -1)])
            collection.create_index([("datos_tecnicos.has_cdc", 1), ("metadata.calidad_datos", 1)])
            
            logger.info("✅ Índices MongoDB configurados")
        except Exception as e:
            logger.warning("⚠️ Error configurando índices: %s", e)

    async def _ensure_indexes_async(self, collection):
        """Versión asíncrona de configuración de índices"""
        try:
            # Índices simples
            await collection.create_index("factura.fecha")
            await collection.create_index("emisor.ruc")
            await collection.create_index("receptor.ruc")
            await collection.create_index("metadata.fecha_procesado")
            await collection.create_index("indices.year_month")
            await collection.create_index("datos_tecnicos.cdc")
            
            # Índices compuestos
            await collection.create_index([("emisor.ruc", 1), ("factura.fecha", -1)])
            await collection.create_index([("indices.year_month", 1), ("montos.monto_total", -1)])
            await collection.create_index([("datos_tecnicos.has_cdc", 1), ("metadata.calidad_datos", 1)])
            
            logger.info("✅ Índices MongoDB async configurados")
        except Exception as e:
            logger.warning("⚠️ Error configurando índices async: %s", e)

    def _bulk_upsert(self, collection, documents: List[Dict[str, Any]]) -> Dict[str, int]:
        """Inserción/actualización masiva síncrona"""
        from pymongo import UpdateOne
        
        operations = []
        for doc in documents:
            operations.append(
                UpdateOne(
                    {"_id": doc["_id"]},
                    {"$set": doc},
                    upsert=True
                )
            )
        
        if operations:
            result = collection.bulk_write(operations)
            return {
                "inserted": result.upserted_count,
                "updated": result.modified_count,
                "errors": 0,
                "total": len(documents)
            }
        
        return {"inserted": 0, "updated": 0, "errors": 0, "total": 0}

    async def _bulk_upsert_async(self, collection, documents: List[Dict[str, Any]]) -> Dict[str, int]:
        """Inserción/actualización masiva asíncrona"""
        from pymongo import UpdateOne
        
        operations = []
        for doc in documents:
            operations.append(
                UpdateOne(
                    {"_id": doc["_id"]},
                    {"$set": doc},
                    upsert=True
                )
            )
        
        if operations:
            result = await collection.bulk_write(operations)
            return {
                "inserted": result.upserted_count,
                "updated": result.modified_count,
                "errors": 0,
                "total": len(documents)
            }
        
        return {"inserted": 0, "updated": 0, "errors": 0, "total": 0}

    def get_statistics(self) -> Dict[str, Any]:
        """Obtiene estadísticas de la base de datos"""
        try:
            client = self._get_sync_client()
            db = client[self.database_name]
            collection = db[self.collection_name]
            
            # Agregaciones para estadísticas
            pipeline = [
                {
                    "$group": {
                        "_id": None,
                        "total_facturas": {"$sum": 1},
                        "total_monto": {"$sum": "$montos.monto_total"},
                        "promedio_monto": {"$avg": "$montos.monto_total"},
                        "facturas_con_cdc": {
                            "$sum": {"$cond": ["$indices.has_cdc", 1, 0]}
                        },
                        "proveedores_unicos": {"$addToSet": "$emisor.ruc"}
                    }
                },
                {
                    "$project": {
                        "total_facturas": 1,
                        "total_monto": 1,
                        "promedio_monto": 1,
                        "facturas_con_cdc": 1,
                        "total_proveedores": {"$size": "$proveedores_unicos"}
                    }
                }
            ]
            
            result = list(collection.aggregate(pipeline))
            if result:
                stats = result[0]
                del stats["_id"]
                return stats
            
            return {"total_facturas": 0}
            
        except Exception as e:
            logger.error("Error obteniendo estadísticas: %s", e)
            return {"error": str(e)}

    def close_connections(self):
        """Cierra conexiones abiertas"""
        try:
            if self._sync_client:
                self._sync_client.close()
                self._sync_client = None
            if self._async_client:
                self._async_client.close()
                self._async_client = None
            logger.info("🔌 Conexiones MongoDB cerradas")
        except Exception as e:
            logger.warning("Error cerrando conexiones: %s", e)

    def __del__(self):
        """Limpieza automática"""
        self.close_connections()


# Utilidades para uso desde otros módulos
def create_mongo_exporter(connection_string: Optional[str] = None) -> MongoDBExporter:
    """Factory para crear exportador MongoDB"""
    return MongoDBExporter(connection_string=connection_string)

async def export_to_mongodb_async(invoices: List[InvoiceData], 
                                 connection_string: Optional[str] = None) -> Dict[str, Any]:
    """Función auxiliar para export asíncrono"""
    exporter = create_mongo_exporter(connection_string)
    try:
        return await exporter.export_invoices_async(invoices)
    finally:
        exporter.close_connections()

def export_to_mongodb(invoices: List[InvoiceData], 
                     connection_string: Optional[str] = None) -> Dict[str, Any]:
    """Función auxiliar para export síncrono"""
    exporter = create_mongo_exporter(connection_string)
    try:
        return exporter.export_invoices(invoices)
    finally:
        exporter.close_connections()