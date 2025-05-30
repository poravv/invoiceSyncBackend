import os
import json
from typing import List
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env
load_dotenv()

class Settings(BaseSettings):
    # Configuraciones de Email
    EMAIL_HOST: str = os.getenv("EMAIL_HOST", "mail.mindtechpy.net")
    EMAIL_PORT: int = int(os.getenv("EMAIL_PORT", 993))
    EMAIL_USERNAME: str = os.getenv("EMAIL_USERNAME", "")
    EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
    EMAIL_USE_SSL: bool = os.getenv("EMAIL_USE_SSL", "True").lower() == "true"
    
    # Configuraciones de la App
    EXCEL_OUTPUT_PATH: str = os.getenv("EXCEL_OUTPUT_PATH", "./data/facturas.xlsx")
    TEMP_PDF_DIR: str = os.getenv("TEMP_PDF_DIR", "./data/temp_pdfs")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Configuración de OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # Configuraciones de la API
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", 8000))
    
    # Configuraciones del Job
    JOB_INTERVAL_MINUTES: int = int(os.getenv("JOB_INTERVAL_MINUTES", 60))
    EMAIL_SEARCH_CRITERIA: str = os.getenv("EMAIL_SEARCH_CRITERIA", "UNSEEN")
    EMAIL_SEARCH_TERMS: List[str] = []
    
    model_config = {
        "env_file": ".env",
        "extra": "ignore"  # Ignorar campos adicionales en lugar de lanzar un error
    }

    def model_post_init(self, __context):
        # Procesamiento manual para EMAIL_SEARCH_TERMS
        search_terms_str = os.getenv("EMAIL_SEARCH_TERMS", '["factura","facturacion","factura electronica","comprobante","documento electrónico","documento electronico"]')
        try:
            self.EMAIL_SEARCH_TERMS = json.loads(search_terms_str)
        except json.JSONDecodeError:
            # Fallback para el formato antiguo
            self.EMAIL_SEARCH_TERMS = [term.strip() for term in search_terms_str.split(",")]

settings = Settings()
