FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Instalar Tesseract OCR y dependencias
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    poppler-utils \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Establecer directorio de trabajo
WORKDIR /app

# Crear directorios necesarios
RUN mkdir -p /app/data/temp_pdfs

# Copiar archivos de requerimientos
COPY requirements.txt .

# Instalar dependencias y spaCy de forma explícita
RUN pip install --no-cache-dir numpy==1.24.3 && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir spacy==3.6.1 && \
    python -m spacy download es_core_news_md && \
    python -m spacy download en_core_web_md

# Copiar código fuente
COPY . .

# Establecer variables de entorno
ENV PYTHONPATH=/app
ENV TESSERACT_PATH=/usr/bin/tesseract

# Exponer puerto
EXPOSE 8000

# Comando por defecto
CMD ["python", "app/server.py"]
