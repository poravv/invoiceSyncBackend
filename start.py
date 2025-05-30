"""
Script para iniciar la aplicación InvoiceSync.
Puede ejecutarse de varias formas:
- Como script CLI: python start.py --mode=single
- Como daemon: python start.py --mode=daemon
- Como servidor API: python start.py --mode=api
"""

import argparse
import logging
import os
import sys

# Añadir el directorio actual al path para importaciones
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.config.settings import settings
from app.main import InvoiceSync, main as main_cli
from app.api.api import start as start_api

# Configurar logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("invoicesync.log")
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Función principal para iniciar la aplicación."""
    parser = argparse.ArgumentParser(description="InvoiceSync - Sincronizador de facturas desde correo a Excel")
    
    # Definir argumentos
    parser.add_argument(
        "--mode",
        choices=["single", "daemon", "api"],
        default="api",
        help="Modo de ejecución: una sola vez (single), continuo (daemon) o servidor API (api)"
    )
    
    parser.add_argument(
        "--interval",
        type=int,
        default=settings.EMAIL_CHECK_INTERVAL,
        help=f"Intervalo en segundos para el modo daemon (default: {settings.EMAIL_CHECK_INTERVAL})"
    )
    
    # Parsear argumentos
    args = parser.parse_args()
    
    # Ejecutar según el modo
    if args.mode == "api":
        logger.info("Iniciando en modo API")
        start_api()
    elif args.mode == "daemon":
        logger.info(f"Iniciando en modo daemon con intervalo de {args.interval} segundos")
        invoice_sync = InvoiceSync()
        invoice_sync.run_daemon(args.interval)
    else:  # single
        logger.info("Iniciando en modo de ejecución única")
        main_cli()

if __name__ == "__main__":
    main()
