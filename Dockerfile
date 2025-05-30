FROM python:3.9-slim

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

# Copiar archivos de requerimientos
COPY requirements.txt .

# Instalar dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Descargar modelos de spaCy
RUN python -m spacy download es_core_news_md
RUN python -m spacy download en_core_web_md

# Crear directorios necesarios
RUN mkdir -p /app/data/temp_pdfs

# Copiar código fuente
COPY . .

# Establecer variables de entorno
ENV PYTHONPATH=/app
ENV TESSERACT_PATH=/usr/bin/tesseract

# Exponer puerto
EXPOSE 8000

# Comando por defecto
CMD ["python", "start.py", "--mode=api"]
