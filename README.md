# InvoiceSync - Backend

InvoiceSync es un sistema automatizado para la extracción de datos de facturas electrónicas paraguayas. El sistema monitorea una casilla de correo electrónico para detectar facturas en formato PDF, extrae la información utilizando OpenAI Vision API, y exporta los datos a un archivo Excel para su posterior procesamiento o integración con sistemas contables.

## Características

- **Monitoreo automático de email:** Revisa automáticamente una casilla de correo para buscar facturas.
- **Extracción de datos con IA:** Utiliza OpenAI Vision API para extraer datos precisos de los PDFs.
- **Procesamiento estructurado:** Extrae datos de forma estructurada incluyendo:
  - Información del emisor
  - Datos del cliente
  - Detalles de timbrado
  - Productos/servicios facturados
  - Totales e impuestos
- **Exportación a Excel:** Genera archivos Excel con los datos extraídos en múltiples hojas:
  - Hoja principal con resumen de facturas
  - Hoja de productos con detalle de items
- **API RESTful:** Permite integrar con otros sistemas y ejecutar el procesamiento bajo demanda.
- **Procesamiento periódico:** Programación de tareas automáticas para revisar correos en intervalos configurables.

## Tecnologías utilizadas

- **Python 3.9+**
- **FastAPI:** Framework web para la API.
- **OpenAI API:** Para el procesamiento de imágenes y extracción de datos.
- **PyMuPDF:** Convertir PDFs a imágenes para mejor procesamiento.
- **Pandas/Openpyxl:** Generación de reportes Excel.
- **imaplib2:** Conexión a servidores de correo electrónico.

## Requisitos

- Python 3.9 o superior
- Una cuenta en OpenAI con acceso a la API Vision (GPT-4o o GPT-4-Vision)
- Acceso a un servidor de correo con IMAP habilitado

## Instalación

1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/yourusername/invoicesync.git
   cd invoicesync/backend
   ```

2. **Crear y activar un entorno virtual:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # En Windows: venv\Scripts\activate
   ```

3. **Instalar dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurar variables de entorno:**
   Copia el archivo `.env.example` a `.env` y configura las variables:
   ```bash
   cp .env.example .env
   ```
   Edita el archivo `.env` con tus propias credenciales.

5. **Crear directorios necesarios:**
   ```bash
   mkdir -p data/temp_pdfs
   ```

## Configuración

Edita el archivo `.env` para configurar:

| Variable | Descripción |
|----------|-------------|
| EMAIL_HOST | Servidor IMAP de correo electrónico |
| EMAIL_PORT | Puerto del servidor IMAP (típicamente 993 para SSL) |
| EMAIL_USE_SSL | Usar SSL para la conexión (True/False) |
| EMAIL_USERNAME | Nombre de usuario para la cuenta de correo |
| EMAIL_PASSWORD | Contraseña para la cuenta de correo |
| EXCEL_OUTPUT_PATH | Ruta donde se guardará el archivo Excel |
| TEMP_PDF_DIR | Directorio temporal para almacenar PDFs |
| LOG_LEVEL | Nivel de log (INFO, DEBUG, ERROR, etc.) |
| OPENAI_API_KEY | Clave API para OpenAI |
| JOB_INTERVAL_MINUTES | Intervalo para revisar correos (en minutos) |
| EMAIL_SEARCH_TERMS | Términos para buscar en asuntos de correos |
| API_HOST | Host para el servidor API |
| API_PORT | Puerto para el servidor API |

## Uso

### Iniciar el servidor API

```bash
python start.py
```

El servidor se iniciará en `http://API_HOST:API_PORT/` (por defecto `http://0.0.0.0:8000/`).

También puedes especificar el modo de ejecución:

```bash
# Ejecución única (procesa correos una vez y termina)
python start.py --mode=single

# Modo daemon (procesa correos periódicamente según intervalo)
python start.py --mode=daemon

# Modo API (inicia el servidor FastAPI)
python start.py --mode=api
```

### Endpoints disponibles

- **GET /health**: Verifica el estado del servicio
- **GET /docs**: Documentación interactiva de la API (Swagger UI)
- **POST /process**: Inicia manualmente el procesamiento de correos
- **POST /job/start**: Inicia el job programado para procesamiento periódico
- **POST /job/stop**: Detiene el job programado
- **GET /job/status**: Obtiene el estado del job programado

## Funcionamiento

1. El sistema monitorea una casilla de correo buscando correos con facturas electrónicas.
2. Al detectar un correo con un PDF adjunto:
   - Descarga el PDF
   - Convierte el PDF a imagen para mejor procesamiento
   - Envía la imagen a OpenAI Vision API con un prompt específico
   - Extrae datos estructurados de la respuesta
   - Almacena la información en un modelo de datos
3. Todos los datos extraídos se exportan a un archivo Excel con múltiples hojas:
   - La hoja "Facturas" contiene un resumen de todas las facturas
   - La hoja "Productos" contiene el detalle de todos los productos/servicios

## Modelo de datos

El sistema extrae y estructura los siguientes datos:

### Datos principales
- **Fecha:** Fecha de emisión de la factura
- **RUC Emisor:** RUC de la empresa emisora (con guión)
- **Nombre Emisor:** Razón social del emisor
- **Número Factura:** Número completo de la factura (ej: 001-001-0000001)
- **Condición Venta:** CONTADO o CRÉDITO
- **Moneda:** Tipo de moneda (PYG, USD, etc.)
- **Monto Total:** Importe total a pagar
- **IVA:** Suma total de IVA
- **Timbrado:** Número de timbrado
- **CDC:** Código de Control
- **RUC Cliente:** RUC del cliente (con guión)
- **Nombre Cliente:** Nombre o razón social del cliente
- **Email Cliente:** Correo electrónico del cliente

### Datos estructurados
- **Empresa:**
  - Nombre
  - RUC
  - Dirección
  - Teléfono
  - Actividad económica

- **Timbrado:**
  - Número
  - Fecha inicio vigencia
  - Fecha fin vigencia

- **Productos:**
  - Descripción
  - Cantidad
  - Precio unitario
  - Total

- **Totales:**
  - Cantidad de artículos
  - Subtotal
  - Total a pagar
  - IVA exentas
  - IVA 5%
  - IVA 10%
  - Total IVA

## Estructura del proyecto

```
backend/
├── app/                  # Código principal
│   ├── api/              # Endpoints de la API
│   ├── config/           # Configuraciones
│   ├── models/           # Modelos de datos
│   ├── modules/          # Módulos funcionales
│   │   ├── email_processor/      # Procesamiento de emails
│   │   ├── excel_exporter/       # Exportación a Excel
│   │   ├── openai_processor/     # Integración con OpenAI
│   ├── utils/            # Utilidades
├── data/                 # Datos generados
│   ├── temp_pdfs/        # Almacenamiento temporal de PDFs
├── tests/                # Tests unitarios
├── venv/                 # Entorno virtual (ignorado en git)
├── .env                  # Variables de entorno (ignorado en git)
├── .env.example          # Ejemplo de variables de entorno
├── .gitignore            # Archivos ignorados por git
├── README.md             # Este archivo
├── requirements.txt      # Dependencias
└── start.py              # Punto de entrada
```

## Modificación del procesamiento de facturas

El sistema puede ser adaptado para diferentes tipos de facturas ajustando los siguientes componentes:

1. **Prompt de OpenAI**: Modifica el prompt en `app/modules/openai_processor/openai_processor.py` para adaptarlo a otros formatos de factura.

2. **Modelo de datos**: Ajusta los modelos en `app/models/models.py` si necesitas campos adicionales o diferentes.

3. **Exportador Excel**: Modifica `app/modules/excel_exporter/excel_exporter.py` para cambiar cómo se exportan los datos.

## Contribuir

1. Haz un fork del repositorio
2. Crea una rama para tu funcionalidad (`git checkout -b feature/amazing-feature`)
3. Haz commit de tus cambios (`git commit -m 'Add some amazing feature'`)
4. Haz push a la rama (`git push origin feature/amazing-feature`)
5. Abre un Pull Request

## Solución de problemas

### Error al procesar PDFs
- Verifica que tu API key de OpenAI sea válida y tenga acceso a GPT-4o o GPT-4-Vision
- Comprueba que los PDFs sean legibles y no estén protegidos

### Problemas de exportación a Excel
- Asegúrate de que la ruta del archivo Excel sea accesible y escribible
- Verifica que no esté abierto en otra aplicación cuando se intenta escribir

## Licencia

Este proyecto está licenciado bajo [MIT License](LICENSE).
