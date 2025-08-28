class OpenAIFatalError(Exception):
    """Error crítico: API key inválida, sin cuota, etc. Debe abortar procesamiento."""
    pass

class OpenAIRetryableError(Exception):
    """Error temporal: timeout, rate limit, etc. Se puede reintentar."""
    pass