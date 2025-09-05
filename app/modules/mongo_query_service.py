"""
Servicio de consultas MongoDB para InvoiceSync
Maneja todas las consultas y agregaciones de facturas desde MongoDB
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
import calendar

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson import ObjectId

from app.config.export_config import get_mongodb_config
from app.models.models import InvoiceData

logger = logging.getLogger(__name__)

class MongoQueryService:
    """
    Servicio optimizado para consultas de facturas en MongoDB
    """
    
    def __init__(self, connection_string: Optional[str] = None):
        config = get_mongodb_config()
        self.connection_string = connection_string or config["connection_string"]
        self.database_name = config["database_name"]
        self.collection_name = config["collection_name"]
        
        self._client: Optional[MongoClient] = None
        logger.info("MongoQueryService inicializado: %s", self.database_name)

    def _get_client(self) -> MongoClient:
        """Obtiene cliente MongoDB con conexión lazy"""
        if not self._client:
            try:
                self._client = MongoClient(
                    self.connection_string,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=10000,
                    socketTimeoutMS=20000,
                    maxPoolSize=50,
                    minPoolSize=5
                )
                # Test conexión
                self._client.admin.command('ping')
                logger.info("✅ Conexión MongoDB establecida para consultas")
            except Exception as e:
                logger.error("❌ Error conectando a MongoDB: %s", e)
                raise
        return self._client

    def get_available_months(self) -> List[Dict[str, Any]]:
        """
        Obtiene lista de meses disponibles con estadísticas básicas
        
        Returns:
            Lista de meses con formato [{"year_month": "2025-01", "count": 45, "total_amount": 1500000}, ...]
        """
        try:
            client = self._get_client()
            db = client[self.database_name]
            collection = db[self.collection_name]
            
            pipeline = [
                {
                    "$match": {
                        "factura.fecha": {"$ne": None}
                    }
                },
                {
                    "$group": {
                        "_id": "$indices.year_month",
                        "count": {"$sum": 1},
                        "total_amount": {"$sum": "$montos.monto_total"},
                        "first_date": {"$min": "$factura.fecha"},
                        "last_date": {"$max": "$factura.fecha"},
                        "unique_providers": {"$addToSet": "$emisor.ruc"}
                    }
                },
                {
                    "$project": {
                        "year_month": "$_id",
                        "count": 1,
                        "total_amount": 1,
                        "first_date": 1,
                        "last_date": 1,
                        "unique_providers": {"$size": "$unique_providers"}
                    }
                },
                {
                    "$sort": {"year_month": -1}
                }
            ]
            
            results = list(collection.aggregate(pipeline))
            
            # Formatear resultados
            months = []
            for result in results:
                if result.get("year_month"):
                    months.append({
                        "year_month": result["year_month"],
                        "count": result["count"],
                        "total_amount": float(result["total_amount"]),
                        "first_date": result["first_date"],
                        "last_date": result["last_date"],
                        "unique_providers": result["unique_providers"]
                    })
            
            logger.info("📅 Encontrados %d meses disponibles", len(months))
            return months
            
        except Exception as e:
            logger.error("Error obteniendo meses disponibles: %s", e)
            return []

    def get_invoices_by_month(self, year_month: str) -> List[Dict[str, Any]]:
        """
        Obtiene todas las facturas de un mes específico
        
        Args:
            year_month: Mes en formato "YYYY-MM"
            
        Returns:
            Lista de facturas completas del mes
        """
        try:
            client = self._get_client()
            db = client[self.database_name]
            collection = db[self.collection_name]
            
            # Validar formato
            try:
                datetime.strptime(year_month, "%Y-%m")
            except ValueError:
                logger.error("Formato de mes inválido: %s", year_month)
                return []
            
            # Consulta optimizada
            query = {"indices.year_month": year_month}
            
            # Proyección para optimizar transferencia de datos
            projection = {
                "_id": 1,
                "factura_id": 1,
                "metadata": 1,
                "factura": 1,
                "emisor": 1,
                "receptor": 1,
                "montos": 1,
                "productos": 1,
                "datos_tecnicos": 1,
                "indices": 1
            }
            
            results = list(collection.find(query, projection).sort("factura.fecha", 1))
            
            logger.info("📄 Encontradas %d facturas para %s", len(results), year_month)
            return results
            
        except Exception as e:
            logger.error("Error obteniendo facturas del mes %s: %s", year_month, e)
            return []

    def get_month_statistics(self, year_month: str) -> Dict[str, Any]:
        """
        Obtiene estadísticas detalladas de un mes específico
        
        Args:
            year_month: Mes en formato "YYYY-MM"
            
        Returns:
            Diccionario con estadísticas completas del mes
        """
        try:
            client = self._get_client()
            db = client[self.database_name]
            collection = db[self.collection_name]
            
            pipeline = [
                {
                    "$match": {"indices.year_month": year_month}
                },
                {
                    "$group": {
                        "_id": None,
                        "total_facturas": {"$sum": 1},
                        "total_monto": {"$sum": "$montos.monto_total"},
                        "total_iva": {"$sum": "$montos.total_iva"},
                        "total_iva_5": {"$sum": "$montos.iva_5"},
                        "total_iva_10": {"$sum": "$montos.iva_10"},
                        "total_subtotal_5": {"$sum": "$montos.subtotal_5"},
                        "total_subtotal_10": {"$sum": "$montos.subtotal_10"},
                        "total_exentas": {"$sum": "$montos.subtotal_exentas"},
                        "promedio_factura": {"$avg": "$montos.monto_total"},
                        
                        # Calidad de datos
                        "facturas_con_cdc": {
                            "$sum": {"$cond": ["$indices.has_cdc", 1, 0]}
                        },
                        "facturas_con_timbrado": {
                            "$sum": {"$cond": ["$indices.has_timbrado", 1, 0]}
                        },
                        
                        # Fuentes de datos
                        "xml_nativo": {
                            "$sum": {"$cond": [{"$eq": ["$metadata.fuente", "XML_NATIVO"]}, 1, 0]}
                        },
                        "openai_vision": {
                            "$sum": {"$cond": [{"$eq": ["$metadata.fuente", "OPENAI_VISION"]}, 1, 0]}
                        },
                        
                        # Distribución por moneda
                        "facturas_gs": {
                            "$sum": {"$cond": [{"$in": ["$factura.moneda", ["GS", "PYG", None]]}, 1, 0]}
                        },
                        "facturas_usd": {
                            "$sum": {"$cond": [{"$eq": ["$factura.moneda", "USD"]}, 1, 0]}
                        },
                        
                        # Rangos de montos
                        "facturas_bajo": {
                            "$sum": {"$cond": [{"$lte": ["$montos.monto_total", 100000]}, 1, 0]}
                        },
                        "facturas_medio": {
                            "$sum": {"$cond": [{"$and": [
                                {"$gt": ["$montos.monto_total", 100000]},
                                {"$lte": ["$montos.monto_total", 1000000]}
                            ]}, 1, 0]}
                        },
                        "facturas_alto": {
                            "$sum": {"$cond": [{"$gt": ["$montos.monto_total", 1000000]}, 1, 0]}
                        },
                        
                        # Proveedores y clientes únicos
                        "proveedores_unicos": {"$addToSet": "$emisor.ruc"},
                        "clientes_unicos": {"$addToSet": "$receptor.ruc"},
                        
                        # Fechas extremas
                        "primera_factura": {"$min": "$factura.fecha"},
                        "ultima_factura": {"$max": "$factura.fecha"}
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "year_month": year_month,
                        "fecha_consulta": {"$literal": datetime.now(timezone.utc).isoformat()},
                        
                        # Contadores principales
                        "total_facturas": 1,
                        "total_monto": 1,
                        "total_iva": 1,
                        "total_iva_5": 1,
                        "total_iva_10": 1,
                        "total_subtotal_5": 1,
                        "total_subtotal_10": 1,
                        "total_exentas": 1,
                        "promedio_factura": {"$round": ["$promedio_factura", 2]},
                        
                        # Calidad de datos
                        "facturas_con_cdc": 1,
                        "facturas_con_timbrado": 1,
                        "porcentaje_cdc": {
                            "$round": [{"$multiply": [{"$divide": ["$facturas_con_cdc", "$total_facturas"]}, 100]}, 2]
                        },
                        "porcentaje_timbrado": {
                            "$round": [{"$multiply": [{"$divide": ["$facturas_con_timbrado", "$total_facturas"]}, 100]}, 2]
                        },
                        
                        # Fuentes
                        "xml_nativo": 1,
                        "openai_vision": 1,
                        
                        # Monedas
                        "facturas_gs": 1,
                        "facturas_usd": 1,
                        
                        # Rangos
                        "facturas_bajo": 1,
                        "facturas_medio": 1,
                        "facturas_alto": 1,
                        
                        # Únicos
                        "total_proveedores": {"$size": "$proveedores_unicos"},
                        "total_clientes": {"$size": "$clientes_unicos"},
                        
                        # Fechas
                        "primera_factura": 1,
                        "ultima_factura": 1
                    }
                }
            ]
            
            result = list(collection.aggregate(pipeline))
            
            if result:
                stats = result[0]
                logger.info("📊 Estadísticas obtenidas para %s: %d facturas", year_month, stats.get("total_facturas", 0))
                return stats
            else:
                return {
                    "year_month": year_month,
                    "total_facturas": 0,
                    "message": "No se encontraron facturas para este mes"
                }
                
        except Exception as e:
            logger.error("Error obteniendo estadísticas del mes %s: %s", year_month, e)
            return {"error": str(e), "year_month": year_month}

    def search_invoices(self, 
                       query: str = "",
                       start_date: Optional[str] = None,
                       end_date: Optional[str] = None,
                       provider_ruc: Optional[str] = None,
                       client_ruc: Optional[str] = None,
                       min_amount: Optional[float] = None,
                       max_amount: Optional[float] = None,
                       limit: int = 100) -> List[Dict[str, Any]]:
        """
        Búsqueda avanzada de facturas con múltiples filtros
        
        Args:
            query: Texto libre para buscar en nombres, descripciones, etc.
            start_date: Fecha inicio en formato "YYYY-MM-DD"
            end_date: Fecha fin en formato "YYYY-MM-DD"
            provider_ruc: RUC del proveedor específico
            client_ruc: RUC del cliente específico
            min_amount: Monto mínimo
            max_amount: Monto máximo
            limit: Límite de resultados
            
        Returns:
            Lista de facturas que coinciden con los criterios
        """
        try:
            client = self._get_client()
            db = client[self.database_name]
            collection = db[self.collection_name]
            
            # Construir filtros
            filters = {}
            
            # Filtro de texto libre
            if query:
                filters["$text"] = {"$search": query}
            
            # Filtros de fecha
            if start_date or end_date:
                date_filter = {}
                if start_date:
                    date_filter["$gte"] = start_date
                if end_date:
                    date_filter["$lte"] = end_date
                filters["factura.fecha"] = date_filter
            
            # Filtros de RUC
            if provider_ruc:
                filters["emisor.ruc"] = provider_ruc
            if client_ruc:
                filters["receptor.ruc"] = client_ruc
            
            # Filtros de monto
            if min_amount is not None or max_amount is not None:
                amount_filter = {}
                if min_amount is not None:
                    amount_filter["$gte"] = min_amount
                if max_amount is not None:
                    amount_filter["$lte"] = max_amount
                filters["montos.monto_total"] = amount_filter
            
            # Proyección optimizada
            projection = {
                "_id": 1,
                "factura_id": 1,
                "factura": 1,
                "emisor": 1,
                "receptor": 1,
                "montos": 1,
                "metadata": 1,
                "indices": 1
            }
            
            # Ejecutar consulta
            results = list(
                collection.find(filters, projection)
                .sort("factura.fecha", -1)
                .limit(limit)
            )
            
            logger.info("🔍 Búsqueda encontró %d facturas (límite: %d)", len(results), limit)
            return results
            
        except Exception as e:
            logger.error("Error en búsqueda de facturas: %s", e)
            return []

    def get_recent_activity(self, days: int = 7) -> Dict[str, Any]:
        """
        Obtiene actividad reciente del sistema
        
        Args:
            days: Número de días hacia atrás para consultar
            
        Returns:
            Diccionario con actividad reciente
        """
        try:
            client = self._get_client()
            db = client[self.database_name]
            collection = db[self.collection_name]
            
            # Fecha límite
            cutoff_date = datetime.now(timezone.utc) - relativedelta(days=days)
            cutoff_str = cutoff_date.isoformat()
            
            pipeline = [
                {
                    "$match": {
                        "metadata.fecha_procesado": {"$gte": cutoff_str}
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": {"$dateFromString": {"dateString": "$metadata.fecha_procesado"}}
                            }
                        },
                        "count": {"$sum": 1},
                        "total_amount": {"$sum": "$montos.monto_total"}
                    }
                },
                {
                    "$sort": {"_id": -1}
                }
            ]
            
            daily_activity = list(collection.aggregate(pipeline))
            
            # Estadísticas totales del período
            total_stats = collection.aggregate([
                {
                    "$match": {
                        "metadata.fecha_procesado": {"$gte": cutoff_str}
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "total_facturas": {"$sum": 1},
                        "total_monto": {"$sum": "$montos.monto_total"},
                        "proveedores_unicos": {"$addToSet": "$emisor.ruc"}
                    }
                }
            ])
            
            total_result = list(total_stats)
            
            return {
                "period_days": days,
                "daily_activity": daily_activity,
                "total_summary": total_result[0] if total_result else {}
            }
            
        except Exception as e:
            logger.error("Error obteniendo actividad reciente: %s", e)
            return {"error": str(e)}

    def close_connection(self):
        """Cierra la conexión a MongoDB"""
        if self._client:
            self._client.close()
            self._client = None
            logger.info("🔌 Conexión MongoDB cerrada")

    def __del__(self):
        """Limpieza automática"""
        self.close_connection()


# Instancia global para reutilización
_query_service: Optional[MongoQueryService] = None

def get_mongo_query_service() -> MongoQueryService:
    """Factory para obtener instancia del servicio de consultas"""
    global _query_service
    if not _query_service:
        _query_service = MongoQueryService()
    return _query_service