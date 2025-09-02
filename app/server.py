#!/usr/bin/env python3
import uvicorn
import sys
import os
from datetime import datetime
import pytz

# Configurar zona horaria de Paraguay
os.environ['TZ'] = 'America/Asuncion'
if hasattr(os, 'tzset'):
    os.tzset()

# Agregar el directorio padre al path para importaciones absolutas
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

# Configurar zona horaria global para la aplicación
paraguay_tz = pytz.timezone('America/Asuncion')
print(f"[INFO] Zona horaria configurada: America/Asuncion")
print(f"[INFO] Hora actual en Paraguay: {datetime.now(paraguay_tz).strftime('%Y-%m-%d %H:%M:%S %Z')}")

# Ahora importar desde el módulo app
from app.api.api import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
