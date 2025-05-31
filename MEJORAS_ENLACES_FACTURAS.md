# Implementación de Detección de Enlaces de Facturas Electrónicas

## ✅ Cambios Implementados

### 1. **Actualización de Dependencias**
- ✅ Agregado `beautifulsoup4==4.12.2` al `requirements.txt`
- ✅ Instaladas todas las dependencias necesarias

### 2. **Mejoras en `_extract_links_from_email()`**
- ✅ **Detección de enlaces SIGA**: Busca patrones `https://facte.siga.com.py/*`
- ✅ **Análisis de HTML**: Usa BeautifulSoup para extraer enlaces de etiquetas `<a>`
- ✅ **Palabras clave**: Detecta enlaces con texto como "visualizar documento", "descargar factura", etc.
- ✅ **Logging mejorado**: Registra todos los enlaces encontrados para depuración

### 3. **Mejoras en `download_pdf_from_url()`**
- ✅ **Headers realistas**: Simula un navegador real para evitar bloqueos
- ✅ **Manejo de HTML**: Procesa páginas web que pueden contener enlaces a PDFs
- ✅ **Extracción inteligente**: Busca enlaces de descarga dentro de páginas HTML
- ✅ **Nombres de archivo**: Genera nombres únicos basados en RUC y CDC para facturas SIGA

### 4. **Nuevo método `_extract_pdf_from_html_page()`**
- ✅ **Análisis de páginas**: Procesa contenido HTML de sistemas de facturación
- ✅ **Búsqueda de PDFs**: Encuentra enlaces de descarga dentro de las páginas
- ✅ **Múltiples intentos**: Prueba diferentes enlaces hasta encontrar un PDF válido

### 5. **Mejoras en el Procesamiento de Enlaces**
- ✅ **Procesamiento universal**: Ya no solo busca enlaces que terminen en `.pdf`
- ✅ **Logging detallado**: Registra cada intento de descarga para depuración
- ✅ **Metadatos adicionales**: Guarda la URL original en la información del PDF procesado

## 🧪 Tests Implementados

### `test_link_detection.py`
- ✅ Valida la detección básica de enlaces
- ✅ Verifica patrones de expresiones regulares
- ✅ Prueba la generación de nombres de archivo

### `test_complete_flow.py`
- ✅ Test completo del flujo de procesamiento
- ✅ Simula emails reales con facturas SIGA
- ✅ Valida extracción de metadatos
- ✅ Verifica todos los tipos de enlaces soportados

## 📋 Tipos de Enlaces Soportados

### ✅ Enlaces SIGA (Paraguay)
```
https://facte.siga.com.py/FacturaE/printDE?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317
https://facte.siga.com.py/FacturaE/downloadXML?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317
```

### ✅ PDFs Directos
```
https://example.com/factura.pdf
https://sistema.com/documentos/invoice_123.pdf
```

### ✅ Enlaces con Palabras Clave
Detecta automáticamente enlaces con texto como:
- "Visualizar documento"
- "Ver factura"
- "Descargar factura"
- "Factura electrónica"
- "Descargar XML"

## 🚀 Cómo Usar

### 1. **Instalación**
```bash
cd /Users/andresvera/Desktop/Proyectos/invoicesync/backend
pip install -r requirements.txt
```

### 2. **Pruebas**
```bash
# Test básico de detección
python3 test_link_detection.py

# Test completo del flujo
python3 test_complete_flow.py
```

### 3. **Producción**
El sistema funcionará automáticamente con los correos que contengan:
- Enlaces de facturas SIGA
- PDFs adjuntos (como antes)
- Enlaces directos a PDFs
- Páginas web con enlaces de descarga de PDFs

## 📊 Ejemplo de Funcionamiento

### Email de Entrada:
```html
EN EL PRESENTE CORREO SE ADJUNTA EL SIGUIENTE DOCUMENTO

FACTURA ELECTRONICA
NUMERO: 001-001-0000561
EMITIDO POR: DASE GROUP E.A.S.
A NOMBRE DE: VARGAS RAMIREZ, CARLOS VICENTE
FECHA DE EMISION: 2025-05-06
MONEDA: PYG
MONTO: 100.000

Para visualizar o descargar acceder al siguiente link
<a href="https://facte.siga.com.py/FacturaE/printDE?ruc=80124544-3&cdc=01801245443001001000056122025050619933575317">VISUALIZAR DOCUMENTO</a>
```

### Procesamiento Automático:
1. ✅ **Detección**: Sistema encuentra el enlace SIGA
2. ✅ **Acceso**: Abre la página de la factura
3. ✅ **Descarga**: Encuentra y descarga el PDF
4. ✅ **Procesamiento**: Envía a OpenAI para extracción de datos
5. ✅ **Exportación**: Agrega datos al Excel final

## 🔧 Configuración Adicional

### Variables de Entorno
No se requieren cambios en las variables de entorno existentes.

### Logs
Los nuevos logs aparecerán en `invoicesync_api.log`:
```
INFO:app.modules.email_processor.email_processor:Encontrado enlace de factura: https://facte.siga.com.py/... (texto: 'visualizar documento')
INFO:app.modules.email_processor.email_processor:Intentando descargar desde: https://facte.siga.com.py/...
INFO:app.modules.email_processor.email_processor:PDF encontrado y descargado desde: https://facte.siga.com.py/...
```

## ⚠️ Consideraciones

### Limitaciones
- Algunos sistemas pueden requerir autenticación
- Páginas con JavaScript dinámico pueden no funcionar
- Rate limiting de los servidores de facturación

### Soluciones
- El sistema incluye headers realistas para evitar bloqueos
- Timeouts configurados para evitar cuelgues
- Logging detallado para depuración de problemas

## 🎯 Resultado

**Tu sistema InvoiceSync ahora puede procesar facturas electrónicas que lleguen como enlaces en el correo electrónico, especialmente del sistema SIGA de Paraguay.**

Los tests confirman que:
- ✅ Detecta enlaces de facturas SIGA correctamente
- ✅ Puede procesar diferentes tipos de enlaces
- ✅ Genera nombres de archivo únicos e informativos
- ✅ Mantiene compatibilidad con PDFs adjuntos existentes
