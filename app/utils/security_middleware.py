"""
Middleware de seguridad para InvoiceSync API
"""

import time
import logging
from typing import Dict, Optional
from collections import defaultdict, deque
from fastapi import Request, Response, HTTPException
from fastapi.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.utils.validators import SecurityHeaders, log_security_event, validate_request_size

logger = logging.getLogger(__name__)

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware para rate limiting básico
    """
    
    def __init__(self, app, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, deque] = defaultdict(deque)
    
    async def dispatch(self, request: Request, call_next):
        client_ip = self._get_client_ip(request)
        current_time = time.time()
        
        # Limpiar requests antiguos
        self._cleanup_old_requests(client_ip, current_time)
        
        # Verificar límite
        if len(self.requests[client_ip]) >= self.max_requests:
            log_security_event(
                "rate_limit_exceeded",
                {
                    "max_requests": self.max_requests,
                    "window_seconds": self.window_seconds,
                    "current_requests": len(self.requests[client_ip])
                },
                client_ip
            )
            
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "message": f"Máximo {self.max_requests} requests por {self.window_seconds} segundos"
                },
                headers={"Retry-After": str(self.window_seconds)}
            )
        
        # Agregar request actual
        self.requests[client_ip].append(current_time)
        
        # Continuar con la request
        response = await call_next(request)
        return response
    
    def _get_client_ip(self, request: Request) -> str:
        """Obtiene IP del cliente considerando proxies"""
        # Verificar headers de proxy
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        
        # IP directa
        return request.client.host if request.client else "unknown"
    
    def _cleanup_old_requests(self, client_ip: str, current_time: float):
        """Limpia requests fuera de la ventana de tiempo"""
        window_start = current_time - self.window_seconds
        
        while (self.requests[client_ip] and 
               self.requests[client_ip][0] < window_start):
            self.requests[client_ip].popleft()

class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware general de seguridad
    """
    
    async def dispatch(self, request: Request, call_next):
        # Validar tamaño de request
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if not validate_request_size(size):
                    log_security_event(
                        "request_too_large",
                        {"content_length": size},
                        self._get_client_ip(request)
                    )
                    
                    return JSONResponse(
                        status_code=413,
                        content={"error": "Request too large"}
                    )
            except ValueError:
                pass
        
        # Validar headers sospechosos
        suspicious_headers = [
            "x-forwarded-host",
            "x-real-ip",
            "x-forwarded-server"
        ]
        
        for header in suspicious_headers:
            value = request.headers.get(header)
            if value and self._is_suspicious_header_value(value):
                log_security_event(
                    "suspicious_header",
                    {"header": header, "value": value},
                    self._get_client_ip(request)
                )
        
        # Procesar request
        start_time = time.time()
        
        try:
            response = await call_next(request)
        except Exception as e:
            # Log errores inesperados
            logger.error(f"Unhandled error: {str(e)}", exc_info=True)
            
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error"},
                headers=SecurityHeaders.get_security_headers()
            )
        
        # Agregar headers de seguridad
        security_headers = SecurityHeaders.get_security_headers()
        for key, value in security_headers.items():
            response.headers[key] = value
        
        # Log requests lentos
        process_time = time.time() - start_time
        if process_time > 5.0:  # Más de 5 segundos
            log_security_event(
                "slow_request",
                {
                    "path": str(request.url.path),
                    "method": request.method,
                    "process_time": process_time
                },
                self._get_client_ip(request)
            )
        
        # Agregar header de tiempo de procesamiento
        response.headers["X-Process-Time"] = str(round(process_time, 3))
        
        return response
    
    def _get_client_ip(self, request: Request) -> str:
        """Obtiene IP del cliente"""
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        
        return request.client.host if request.client else "unknown"
    
    def _is_suspicious_header_value(self, value: str) -> bool:
        """Detecta valores sospechosos en headers"""
        suspicious_patterns = [
            "127.0.0.1",
            "localhost",
            "169.254.",  # Link-local
            "10.0.0.1",
            "192.168.1.1"
        ]
        
        value_lower = value.lower()
        return any(pattern in value_lower for pattern in suspicious_patterns)

class RequestValidationMiddleware(BaseHTTPMiddleware):
    """
    Middleware para validación de requests
    """
    
    async def dispatch(self, request: Request, call_next):
        # Validar método HTTP
        allowed_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"]
        if request.method not in allowed_methods:
            return JSONResponse(
                status_code=405,
                content={"error": "Method not allowed"}
            )
        
        # Validar Content-Type para requests con body
        if request.method in ["POST", "PUT"] and request.headers.get("content-length", "0") != "0":
            content_type = request.headers.get("content-type", "")
            
            allowed_content_types = [
                "application/json",
                "application/x-www-form-urlencoded",
                "multipart/form-data",
                "text/plain"
            ]
            
            if not any(ct in content_type for ct in allowed_content_types):
                log_security_event(
                    "invalid_content_type",
                    {"content_type": content_type},
                    self._get_client_ip(request)
                )
                
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid content type"}
                )
        
        return await call_next(request)
    
    def _get_client_ip(self, request: Request) -> str:
        """Obtiene IP del cliente"""
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        
        return request.client.host if request.client else "unknown"

# Configuración por defecto para diferentes entornos
MIDDLEWARE_CONFIGS = {
    "development": {
        "rate_limit": {"max_requests": 1000, "window_seconds": 60},
        "enable_security_headers": True,
        "enable_request_validation": True
    },
    "production": {
        "rate_limit": {"max_requests": 100, "window_seconds": 60},
        "enable_security_headers": True,
        "enable_request_validation": True
    },
    "testing": {
        "rate_limit": {"max_requests": 10000, "window_seconds": 60},
        "enable_security_headers": False,
        "enable_request_validation": False
    }
}