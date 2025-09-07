"""
Validadores y utilidades de seguridad para InvoiceSync
"""

import re
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

class ValidationError(Exception):
    """Error de validación personalizado"""
    pass

class SecurityValidators:
    """Validadores de seguridad para datos de entrada"""
    
    @staticmethod
    def validate_year_month(year_month: str) -> bool:
        """
        Valida formato de año-mes (YYYY-MM)
        Args:
            year_month: String en formato YYYY-MM
        Returns:
            bool: True si es válido
        Raises:
            ValidationError: Si el formato es inválido
        """
        if not year_month:
            raise ValidationError("year_month no puede estar vacío")
        
        # Patrón para YYYY-MM
        pattern = r'^\d{4}-(0[1-9]|1[0-2])$'
        if not re.match(pattern, year_month):
            raise ValidationError("Formato de year_month inválido. Use YYYY-MM")
        
        try:
            year, month = year_month.split('-')
            year_int = int(year)
            month_int = int(month)
            
            # Validaciones lógicas
            if year_int < 2020 or year_int > 2030:
                raise ValidationError("Año debe estar entre 2020 y 2030")
            
            if month_int < 1 or month_int > 12:
                raise ValidationError("Mes debe estar entre 01 y 12")
                
            # Validar que la fecha no sea futura (más de 1 mes)
            target_date = datetime(year_int, month_int, 1)
            current_date = datetime.now()
            if target_date > current_date.replace(day=1):
                # Permitir hasta el mes actual
                if not (year_int == current_date.year and month_int == current_date.month):
                    raise ValidationError("No se pueden consultar meses futuros")
            
            return True
            
        except (ValueError, TypeError) as e:
            raise ValidationError(f"Error procesando fecha: {str(e)}")

    @staticmethod
    def validate_export_type(export_type: str) -> bool:
        """
        Valida tipos de exportación permitidos
        Args:
            export_type: Tipo de exportación
        Returns:
            bool: True si es válido
        Raises:
            ValidationError: Si el tipo no es válido
        """
        allowed_types = ["ascont", "completo", "mongodb"]
        
        if not export_type:
            raise ValidationError("export_type no puede estar vacío")
        
        if export_type.lower() not in allowed_types:
            raise ValidationError(f"export_type debe ser uno de: {', '.join(allowed_types)}")
        
        return True

    @staticmethod
    def validate_ruc(ruc: str) -> bool:
        """
        Valida formato de RUC paraguayo
        Args:
            ruc: Número de RUC
        Returns:
            bool: True si es válido
        """
        if not ruc:
            return True  # RUC puede ser opcional en algunos casos
        
        # Limpiar RUC (quitar espacios, guiones)
        clean_ruc = re.sub(r'[^0-9]', '', ruc)
        
        # Validar longitud (7-8 dígitos + dígito verificador)
        if len(clean_ruc) < 8 or len(clean_ruc) > 9:
            return False
        
        # Patrón básico para RUC paraguayo
        pattern = r'^\d{7,8}-?\d$'
        return bool(re.match(pattern, ruc))

    @staticmethod
    def validate_monetary_amount(amount: Any) -> bool:
        """
        Valida montos monetarios
        Args:
            amount: Monto a validar
        Returns:
            bool: True si es válido
        """
        if amount is None:
            return True  # Permitir None
        
        try:
            decimal_amount = Decimal(str(amount))
            
            # Validar rangos razonables
            if decimal_amount < 0:
                return False  # No permitir montos negativos
            
            if decimal_amount > Decimal('999999999999'):  # 999 mil millones
                return False  # Monto demasiado alto
            
            return True
            
        except (InvalidOperation, TypeError, ValueError):
            return False

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Sanitiza nombres de archivo para prevenir path traversal
        Args:
            filename: Nombre de archivo original
        Returns:
            str: Nombre de archivo sanitizado
        """
        if not filename:
            return "archivo_sin_nombre"
        
        # Remover caracteres peligrosos
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
        
        # Remover secuencias de path traversal
        sanitized = re.sub(r'\.\.+', '.', sanitized)
        
        # Limitar longitud
        sanitized = sanitized[:100]
        
        # Asegurar que no esté vacío después de sanitizar
        if not sanitized.strip():
            sanitized = "archivo_sanitizado"
        
        return sanitized

class DataValidators:
    """Validadores específicos para datos de facturas"""
    
    @staticmethod
    def validate_invoice_data(invoice_data: Dict[str, Any]) -> List[str]:
        """
        Valida datos completos de factura
        Args:
            invoice_data: Diccionario con datos de factura
        Returns:
            List[str]: Lista de errores encontrados
        """
        errors = []
        
        # Validar campos obligatorios
        required_fields = ['numero_factura', 'fecha', 'ruc_emisor', 'nombre_emisor']
        for field in required_fields:
            if not invoice_data.get(field):
                errors.append(f"Campo obligatorio faltante: {field}")
        
        # Validar RUC del emisor
        ruc_emisor = invoice_data.get('ruc_emisor')
        if ruc_emisor and not SecurityValidators.validate_ruc(ruc_emisor):
            errors.append(f"RUC emisor inválido: {ruc_emisor}")
        
        # Validar RUC del cliente
        ruc_cliente = invoice_data.get('ruc_cliente')
        if ruc_cliente and not SecurityValidators.validate_ruc(ruc_cliente):
            errors.append(f"RUC cliente inválido: {ruc_cliente}")
        
        # Validar montos
        monetary_fields = ['monto_total', 'subtotal_5', 'subtotal_10', 'iva_5', 'iva_10', 'subtotal_exentas']
        for field in monetary_fields:
            amount = invoice_data.get(field)
            if amount is not None and not SecurityValidators.validate_monetary_amount(amount):
                errors.append(f"Monto inválido en {field}: {amount}")
        
        # Validar fecha
        fecha = invoice_data.get('fecha')
        if fecha:
            try:
                if isinstance(fecha, str):
                    datetime.fromisoformat(fecha.replace('Z', '+00:00'))
                elif not isinstance(fecha, (datetime, date)):
                    errors.append(f"Formato de fecha inválido: {fecha}")
            except (ValueError, TypeError):
                errors.append(f"Fecha no parseable: {fecha}")
        
        # Validar consistencia de montos
        try:
            monto_total = float(invoice_data.get('monto_total', 0) or 0)
            subtotal_5 = float(invoice_data.get('subtotal_5', 0) or 0)
            subtotal_10 = float(invoice_data.get('subtotal_10', 0) or 0)
            iva_5 = float(invoice_data.get('iva_5', 0) or 0)
            iva_10 = float(invoice_data.get('iva_10', 0) or 0)
            exentas = float(invoice_data.get('subtotal_exentas', 0) or 0)
            
            calculated_total = subtotal_5 + subtotal_10 + iva_5 + iva_10 + exentas
            
            # Permitir pequeñas diferencias por redondeo
            if abs(monto_total - calculated_total) > 1.0:
                errors.append(f"Inconsistencia en montos: total={monto_total}, calculado={calculated_total}")
                
        except (ValueError, TypeError):
            errors.append("Error validando consistencia de montos")
        
        return errors

class SecurityHeaders:
    """Headers de seguridad para respuestas HTTP"""
    
    @staticmethod
    def get_security_headers() -> Dict[str, str]:
        """
        Obtiene headers de seguridad recomendados
        Returns:
            Dict[str, str]: Headers de seguridad
        """
        return {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": "default-src 'self'",
            "Referrer-Policy": "strict-origin-when-cross-origin"
        }

def validate_request_size(content_length: Optional[int], max_size: int = 50 * 1024 * 1024) -> bool:
    """
    Valida el tamaño de la request para prevenir ataques DoS
    Args:
        content_length: Tamaño del contenido en bytes
        max_size: Tamaño máximo permitido (default: 50MB)
    Returns:
        bool: True si el tamaño es válido
    """
    if content_length is None:
        return True  # Permitir requests sin Content-Length
    
    return content_length <= max_size

def log_security_event(event_type: str, details: Dict[str, Any], client_ip: str = "unknown"):
    """
    Registra eventos de seguridad
    Args:
        event_type: Tipo de evento (validation_error, suspicious_activity, etc.)
        details: Detalles del evento
        client_ip: IP del cliente
    """
    logger.warning(
        f"🔒 SECURITY EVENT: {event_type} | IP: {client_ip} | Details: {details}"
    )