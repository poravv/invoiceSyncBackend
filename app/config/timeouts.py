"""
Configuración centralizada de timeouts para evitar cuelgues del servidor.
Estos valores están optimizados para balance entre responsividad y robustez.
"""

# Timeouts de conexión IMAP (segundos)
IMAP_CONNECTION_TIMEOUT = 30       # Timeout para establecer conexión
IMAP_LOGIN_TIMEOUT = 20           # Timeout para autenticación
IMAP_SEARCH_TIMEOUT = 15          # Timeout para búsquedas
IMAP_FETCH_TIMEOUT = 20           # Timeout para obtener mensajes
IMAP_MARK_SEEN_TIMEOUT = 10       # Timeout para marcar como leído
IMAP_CLOSE_TIMEOUT = 5            # Timeout para cerrar conexión

# Timeouts de descarga HTTP (segundos)
HTTP_CONNECT_TIMEOUT = 5          # Timeout de conexión HTTP
HTTP_READ_TIMEOUT = 15            # Timeout de lectura HTTP
HTTP_MAX_RETRIES = 2              # Máximo número de reintentos
HTTP_RETRY_DELAY = 2              # Delay base entre reintentos

# Timeouts de OpenAI API (segundos)
OPENAI_API_TIMEOUT = 60           # Timeout para llamadas a OpenAI
OPENAI_MAX_RETRIES = 3            # Máximo número de reintentos
OPENAI_RETRY_DELAY = 2            # Delay base entre reintentos

# Timeouts de procesamiento por thread (segundos)
EMAIL_ACCOUNT_TIMEOUT = 180       # Timeout por cuenta de email (3 min)
GLOBAL_PROCESSING_TIMEOUT = 600   # Timeout global del sistema (10 min)

# Timeouts del pool de conexiones (segundos)
POOL_CONNECTION_TIMEOUT = 300     # Tiempo antes de cerrar conexión inactiva (5 min)
POOL_CLEANUP_INTERVAL = 60        # Intervalo de limpieza del pool (1 min)
POOL_TEST_CONNECTION_TIMEOUT = 5  # Timeout para test de conexión

# Configuración de retry con backoff exponencial
def get_retry_delay(attempt: int, base_delay: int = 2) -> int:
    """Calcula delay con backoff exponencial."""
    return min(base_delay * (2 ** attempt), 30)  # Máximo 30 segundos

def is_fatal_error(error_msg: str) -> bool:
    """Determina si un error es fatal y no debe reintentarse."""
    error_lower = error_msg.lower()
    fatal_keywords = [
        "invalid api key", "api key", "authentication", "unauthorized",
        "insufficient quota", "quota exceeded", "billing", "permission denied"
    ]
    return any(keyword in error_lower for keyword in fatal_keywords)

def is_retryable_error(error_msg: str) -> bool:
    """Determina si un error es transitorio y puede reintentarse."""
    error_lower = error_msg.lower()
    retryable_keywords = [
        "timeout", "rate limit", "too many requests", "connection", 
        "network", "server error", "503", "502", "504", "temporary"
    ]
    return any(keyword in error_lower for keyword in retryable_keywords)