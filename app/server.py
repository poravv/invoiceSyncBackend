#!/usr/bin/env python3
import uvicorn
import sys
import os

# Agregar el directorio padre al path para importaciones absolutas
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

# Ahora importar desde el módulo app
from app.api.api import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
