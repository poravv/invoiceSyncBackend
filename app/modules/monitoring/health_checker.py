"""
Health Checks Avanzados - Monitoreo completo del sistema
Proporciona visibilidad detallada del estado de todos los componentes
"""
import logging
import os
import psutil
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import asyncio

from app.config.settings import settings

logger = logging.getLogger(__name__)

class AdvancedHealthChecker:
    """
    Sistema de health checks avanzado que monitorea todos los componentes críticos.
    Proporciona métricas detalladas para observabilidad y alertas proactivas.
    """
    
    def __init__(self):
        """Inicializa el health checker."""
        self.start_time = datetime.utcnow()
        self.check_history: List[Dict[str, Any]] = []
        self.max_history_size = 100
        
        logger.info("✅ Advanced Health Checker inicializado")
    
    async def check_email_health(self) -> Dict[str, Any]:
        """Verifica el estado de las conexiones de email."""
        try:
            start_time = time.time()
            
            # Verificar configuraciones de email
            email_configs = settings.get_all_email_configs() if hasattr(settings, 'get_all_email_configs') else []
            
            if not email_configs and settings.EMAIL_USERNAME:
                # Fallback a configuración legacy
                email_configs = [{
                    "username": settings.EMAIL_USERNAME,
                    "host": settings.EMAIL_HOST,
                    "port": settings.EMAIL_PORT
                }]
            
            total_configs = len(email_configs)
            successful_connections = 0
            connection_details = []
            
            for config in email_configs:
                try:
                    # Verificar configuración básica
                    config_health = {
                        "username": config.get("username", ""),
                        "host": config.get("host", ""),
                        "port": config.get("port", 0),
                        "status": "unknown",
                        "response_time_ms": 0,
                        "error": None
                    }
                    
                    # Test rápido de conectividad (sin autenticación completa)
                    conn_start = time.time()
                    
                    # Simulación de check de conectividad (reemplazar con lógica real)
                    if config.get("host") and config.get("port"):
                        config_health["status"] = "healthy"
                        successful_connections += 1
                    else:
                        config_health["status"] = "misconfigured"
                        config_health["error"] = "Host o puerto no configurado"
                    
                    config_health["response_time_ms"] = round((time.time() - conn_start) * 1000, 2)
                    connection_details.append(config_health)
                    
                except Exception as e:
                    connection_details.append({
                        "username": config.get("username", ""),
                        "status": "error",
                        "error": str(e),
                        "response_time_ms": 0
                    })
            
            total_time = round((time.time() - start_time) * 1000, 2)
            
            return {
                "status": "healthy" if successful_connections > 0 else "unhealthy",
                "total_configurations": total_configs,
                "successful_connections": successful_connections,
                "response_time_ms": total_time,
                "connection_details": connection_details,
                "pool_enabled": True  # Asumiendo que el pool está habilitado
            }
            
        except Exception as e:
            logger.error(f"Error verificando salud de email: {e}")
            return {
                "status": "error",
                "error": str(e),
                "total_configurations": 0,
                "successful_connections": 0
            }
    
    async def check_openai_health(self) -> Dict[str, Any]:
        """Verifica el estado de la API de OpenAI."""
        try:
            start_time = time.time()
            
            api_key = settings.OPENAI_API_KEY
            
            if not api_key:
                return {
                    "status": "misconfigured",
                    "error": "API key no configurada",
                    "response_time_ms": 0,
                    "cache_enabled": False
                }
            
            # Test básico de configuración
            health_status = {
                "status": "healthy",
                "api_key_configured": bool(api_key),
                "api_key_format_valid": api_key.startswith("sk-") if api_key else False,
                "model": getattr(settings, "OPENAI_MODEL", "gpt-4o"),
                "cache_enabled": getattr(settings, "OPENAI_CACHE_ENABLED", True),
                "response_time_ms": round((time.time() - start_time) * 1000, 2)
            }
            
            # Verificar cache si está habilitado
            if health_status["cache_enabled"]:
                try:
                    from app.modules.openai_processor.cache import OpenAICache
                    cache = OpenAICache()
                    cache_stats = cache.get_cache_stats()
                    health_status["cache_stats"] = cache_stats
                except Exception as e:
                    health_status["cache_error"] = str(e)
            
            return health_status
            
        except Exception as e:
            logger.error(f"Error verificando salud de OpenAI: {e}")
            return {
                "status": "error",
                "error": str(e),
                "response_time_ms": 0
            }
    
    def check_disk_space(self) -> Dict[str, Any]:
        """Verifica el espacio en disco disponible."""
        try:
            # Verificar espacio en directorio de datos
            data_path = getattr(settings, "TEMP_PDF_DIR", "./data/temp_pdfs")
            excel_path = os.path.dirname(settings.EXCEL_OUTPUT_PATH)
            
            disk_info = {}
            
            for name, path in [("temp_pdfs", data_path), ("excel_output", excel_path)]:
                try:
                    if os.path.exists(path):
                        statvfs = os.statvfs(path)
                        total_space = statvfs.f_frsize * statvfs.f_blocks
                        # Usar f_bavail si f_available no está disponible
                        free_space = statvfs.f_frsize * getattr(statvfs, 'f_available', statvfs.f_bavail)
                        used_space = total_space - free_space
                        
                        disk_info[name] = {
                            "path": path,
                            "total_gb": round(total_space / (1024**3), 2),
                            "free_gb": round(free_space / (1024**3), 2),
                            "used_gb": round(used_space / (1024**3), 2),
                            "usage_percent": round((used_space / total_space) * 100, 1),
                            "status": "healthy" if (free_space / total_space) > 0.1 else "warning"
                        }
                    else:
                        disk_info[name] = {
                            "path": path,
                            "status": "not_found",
                            "error": "Directorio no existe"
                        }
                except Exception as e:
                    disk_info[name] = {
                        "path": path,
                        "status": "error",
                        "error": str(e)
                    }
            
            overall_status = "healthy"
            if any(info.get("status") == "warning" for info in disk_info.values()):
                overall_status = "warning"
            elif any(info.get("status") == "error" for info in disk_info.values()):
                overall_status = "error"
            
            return {
                "status": overall_status,
                "disks": disk_info
            }
            
        except Exception as e:
            logger.error(f"Error verificando espacio en disco: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Obtiene información de uso de memoria."""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_percent = process.memory_percent()
            
            # Información del sistema
            system_memory = psutil.virtual_memory()
            
            return {
                "status": "healthy" if memory_percent < 80 else "warning",
                "process": {
                    "rss_mb": round(memory_info.rss / (1024**2), 2),
                    "vms_mb": round(memory_info.vms / (1024**2), 2),
                    "percent": round(memory_percent, 2)
                },
                "system": {
                    "total_gb": round(system_memory.total / (1024**3), 2),
                    "available_gb": round(system_memory.available / (1024**3), 2),
                    "used_percent": system_memory.percent
                }
            }
            
        except Exception as e:
            logger.error(f"Error obteniendo uso de memoria: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def get_active_jobs_count(self) -> Dict[str, Any]:
        """Obtiene información sobre jobs activos."""
        try:
            # Esta función necesitaría acceso al scheduler real
            # Por ahora retornamos información básica
            
            return {
                "status": "healthy",
                "active_jobs": 0,  # Placeholder
                "scheduled_jobs": 1,  # Placeholder
                "last_execution": None  # Placeholder
            }
            
        except Exception as e:
            logger.error(f"Error obteniendo jobs activos: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def get_uptime(self) -> Dict[str, Any]:
        """Obtiene información de uptime del servicio."""
        try:
            uptime_delta = datetime.utcnow() - self.start_time
            uptime_seconds = uptime_delta.total_seconds()
            
            return {
                "status": "healthy",
                "start_time": self.start_time.isoformat(),
                "uptime_seconds": round(uptime_seconds, 2),
                "uptime_hours": round(uptime_seconds / 3600, 2),
                "uptime_days": round(uptime_seconds / 86400, 2)
            }
            
        except Exception as e:
            logger.error(f"Error calculando uptime: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def comprehensive_health_check(self) -> Dict[str, Any]:
        """
        Ejecuta un health check completo de todos los componentes.
        
        Returns:
            Dict con el estado completo del sistema
        """
        start_time = time.time()
        
        try:
            # Ejecutar checks en paralelo cuando sea posible
            email_health, openai_health = await asyncio.gather(
                self.check_email_health(),
                self.check_openai_health(),
                return_exceptions=True
            )
            
            # Checks síncronos
            disk_health = self.check_disk_space()
            memory_health = self.get_memory_usage()
            jobs_health = self.get_active_jobs_count()
            uptime_info = self.get_uptime()
            
            # Determinar estado general
            all_services = [email_health, openai_health, disk_health, memory_health, jobs_health]
            
            # Contar estados
            healthy_count = sum(1 for service in all_services if service.get("status") == "healthy")
            warning_count = sum(1 for service in all_services if service.get("status") == "warning")
            error_count = sum(1 for service in all_services if service.get("status") in ["error", "unhealthy"])
            
            if error_count > 0:
                overall_status = "unhealthy"
            elif warning_count > 0:
                overall_status = "degraded"
            else:
                overall_status = "healthy"
            
            total_time = round((time.time() - start_time) * 1000, 2)
            
            health_report = {
                "status": overall_status,
                "timestamp": datetime.utcnow().isoformat(),
                "response_time_ms": total_time,
                "summary": {
                    "healthy_services": healthy_count,
                    "warning_services": warning_count,
                    "error_services": error_count,
                    "total_services": len(all_services)
                },
                "services": {
                    "email": email_health,
                    "openai": openai_health,
                    "disk": disk_health,
                    "memory": memory_health,
                    "jobs": jobs_health
                },
                "uptime": uptime_info,
                "performance_optimizations": {
                    "openai_cache_enabled": openai_health.get("cache_enabled", False),
                    "imap_pool_enabled": email_health.get("pool_enabled", False),
                    "incremental_excel_enabled": True  # Asumimos que está habilitado
                }
            }
            
            # Guardar en historial
            self._save_to_history(health_report)
            
            return health_report
            
        except Exception as e:
            logger.error(f"Error en health check comprensivo: {e}")
            return {
                "status": "error",
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e),
                "response_time_ms": round((time.time() - start_time) * 1000, 2)
            }
    
    def _save_to_history(self, health_report: Dict[str, Any]):
        """Guarda el reporte de salud en el historial."""
        try:
            # Mantener solo los últimos N reportes
            self.check_history.append({
                "timestamp": health_report["timestamp"],
                "status": health_report["status"],
                "response_time_ms": health_report["response_time_ms"],
                "summary": health_report["summary"]
            })
            
            # Limitar tamaño del historial
            if len(self.check_history) > self.max_history_size:
                self.check_history = self.check_history[-self.max_history_size:]
                
        except Exception as e:
            logger.error(f"Error guardando historial de health checks: {e}")
    
    def get_health_trends(self) -> Dict[str, Any]:
        """Obtiene tendencias de salud basadas en el historial."""
        try:
            if not self.check_history:
                return {"trends": "No hay datos históricos disponibles"}
            
            recent_checks = self.check_history[-10:]  # Últimos 10 checks
            
            # Calcular métricas de tendencia
            healthy_count = sum(1 for check in recent_checks if check["status"] == "healthy")
            response_times = [check["response_time_ms"] for check in recent_checks]
            
            avg_response_time = sum(response_times) / len(response_times)
            
            return {
                "recent_checks_count": len(recent_checks),
                "healthy_percentage": round((healthy_count / len(recent_checks)) * 100, 1),
                "average_response_time_ms": round(avg_response_time, 2),
                "last_check": recent_checks[-1] if recent_checks else None,
                "trend": "stable"  # Placeholder para lógica más compleja
            }
            
        except Exception as e:
            logger.error(f"Error calculando tendencias de salud: {e}")
            return {"error": str(e)}

# Instancia global del health checker
_health_checker: Optional[AdvancedHealthChecker] = None

def get_health_checker() -> AdvancedHealthChecker:
    """Obtiene la instancia global del health checker."""
    global _health_checker
    if _health_checker is None:
        _health_checker = AdvancedHealthChecker()
    return _health_checker