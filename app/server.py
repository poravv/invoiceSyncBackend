#!/usr/bin/env python3
import uvicorn
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api.api import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
