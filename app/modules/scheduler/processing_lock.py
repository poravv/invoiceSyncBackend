import threading

# Global lock to serialize invoice processing/export across automation and manual uploads
PROCESSING_LOCK = threading.Lock()

