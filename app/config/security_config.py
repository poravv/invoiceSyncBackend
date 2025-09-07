"""
Configuración de seguridad para InvoiceSync
"""

import os
from typing import Dict, Any

# Configuración por entorno
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Configuraciones de seguridad por entorno
SECURITY_CONFIGS = {
    "development": {
        "rate_limiting": {
            "enabled": True,
            "max_requests_per_minute": 1000,
            "max_requests_per_hour": 10000
        },
        "authentication": {
            "required": False,
            "jwt_secret": "dev-secret-key",
            "token_expiry_hours": 24
        },
        "file_uploads": {
            "max_file_size_mb": 50,
            "allowed_extensions": [".pdf", ".xml", ".xlsx", ".xls"],
            "scan_for_malware": False
        },
        "logging": {
            "log_level": "INFO",
            "log_sensitive_data": False,
            "security_event_retention_days": 30
        },
        "mongodb": {
            "connection_timeout_seconds": 10,
            "max_pool_size": 50,
            "require_ssl": False
        }
    },
    "production": {
        "rate_limiting": {
            "enabled": True,
            "max_requests_per_minute": 100,
            "max_requests_per_hour": 1000
        },
        "authentication": {
            "required": True,
            "jwt_secret": os.getenv("JWT_SECRET_KEY", "CHANGE-THIS-IN-PRODUCTION"),
            "token_expiry_hours": 8
        },
        "file_uploads": {
            "max_file_size_mb": 25,
            "allowed_extensions": [".pdf", ".xml"],
            "scan_for_malware": True
        },
        "logging": {
            "log_level": "WARNING",
            "log_sensitive_data": False,
            "security_event_retention_days": 90
        },
        "mongodb": {
            "connection_timeout_seconds": 5,
            "max_pool_size": 20,
            "require_ssl": True
        }
    }
}

def get_security_config() -> Dict[str, Any]:
    """
    Obtiene configuración de seguridad basada en el entorno
    """
    return SECURITY_CONFIGS.get(ENVIRONMENT, SECURITY_CONFIGS["development"])

# Headers de seguridad para diferentes tipos de respuesta
SECURITY_HEADERS = {
    "api_json": {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Content-Security-Policy": "default-src 'self'",
        "Referrer-Policy": "strict-origin-when-cross-origin"
    },
    "file_download": {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "attachment",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache"
    }
}

# Patterns de validación
VALIDATION_PATTERNS = {
    "year_month": r"^\d{4}-(0[1-9]|1[0-2])$",
    "ruc_paraguay": r"^\d{7,8}-?\d$",
    "cdc_sifen": r"^[0-9]{44}$",
    "timbrado": r"^\d{8}$",
    "safe_filename": r"^[a-zA-Z0-9._-]+$"
}

# Límites de datos
DATA_LIMITS = {
    "max_invoices_per_export": 10000,
    "max_months_range": 12,
    "max_file_size_bytes": 50 * 1024 * 1024,  # 50MB
    "max_invoice_amount": 999999999999,  # 999 mil millones
    "max_products_per_invoice": 1000
}

# Configuración de MongoDB
MONGODB_SECURITY = {
    "max_connection_idle_time": 30000,  # 30 segundos
    "server_selection_timeout": 5000,   # 5 segundos
    "connect_timeout": 10000,           # 10 segundos
    "socket_timeout": 20000,            # 20 segundos
    "max_pool_size": 50,
    "min_pool_size": 5,
    "retry_writes": True,
    "retry_reads": True
}

# Configuración de email processor
EMAIL_SECURITY = {
    "connection_timeout": 30,
    "max_retries": 3,
    "max_attachments_per_email": 10,
    "max_attachment_size_mb": 25,
    "allowed_attachment_types": [
        "application/pdf",
        "text/xml",
        "application/xml"
    ]
}

def get_allowed_origins() -> list:
    """
    Obtiene orígenes permitidos para CORS basado en el entorno
    """
    if ENVIRONMENT == "production":
        return [
            "https://invoicesync.yourdomain.com",
            "https://app.yourdomain.com"
        ]
    else:
        return [
            "http://localhost:4200",
            "http://localhost:3000",
            "http://127.0.0.1:4200"
        ]