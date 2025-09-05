# 📊 InvoiceSync - Exportadores Avanzados

## Nuevas Funcionalidades de Exportación

InvoiceSync ahora incluye **dos nuevos exportadores avanzados** diseñados para diferentes necesidades de análisis y presentación de datos:

### 🏢 1. Exportador Excel Completo
**Ideal para**: Análisis detallado, auditorías, presentaciones a clientes potenciales

#### Características:
- **6 hojas especializadas** con información completa
- **Formato profesional** con colores corporativos
- **Auto-deduplicación** inteligente
- **Análisis estadístico** incluido

#### Hojas Generadas:
1. **Facturas_Completas**: Datos principales + campos adicionales
2. **Productos_Detalle**: Detalle completo de productos/servicios  
3. **Empresa_Emisor**: Información completa del emisor con estadísticas
4. **Cliente_Receptor**: Información completa del cliente con análisis
5. **Datos_Tecnicos**: CDC, timbrados, validaciones y metadatos
6. **Resumen_Mensual**: Estadísticas y tendencias del período

#### Ubicación de Archivos:
```
/data/excels/completo/
├── facturas_completas_2025-01.xlsx
├── facturas_completas_2025-02.xlsx
└── ...
```

### 💾 2. Exportador MongoDB
**Ideal para**: Análisis avanzado, consultas complejas, integración con BI

#### Características:
- **Estructura documental** optimizada para consultas
- **Índices automáticos** para rendimiento
- **Metadatos de auditoria** incluidos
- **Agregaciones** preparadas para reportes
- **Escalabilidad** para grandes volúmenes

#### Estructura del Documento:
```json
{
  "_id": "unique_invoice_id",
  "metadata": {
    "fecha_procesado": "2025-01-08T10:30:00",
    "fuente": "XML_NATIVO|OPENAI_VISION",
    "calidad_datos": "ALTA|MEDIA|BAJA"
  },
  "factura": { /* datos principales */ },
  "emisor": { /* información del proveedor */ },
  "receptor": { /* información del cliente */ },
  "montos": { /* desglose completo */ },
  "productos": [ /* array de productos */ ],
  "datos_tecnicos": { /* CDC, timbrados */ },
  "indices": { /* para consultas optimizadas */ }
}
```

## 🚀 Cómo Usar

### API Endpoints

#### 1. Exportar Excel Completo
```bash
# Procesamiento síncrono
POST /export/excel-completo

# Procesamiento en segundo plano
POST /export/excel-completo?run_async=true

# Descargar archivo específico
GET /export/excel-completo/2025-01

# Listar archivos disponibles
GET /export/excel-completo/list
```

#### 2. Exportar a MongoDB
```bash
# Exportación síncrona
POST /export/mongodb

# Exportación en segundo plano  
POST /export/mongodb?run_async=true

# Obtener estadísticas
GET /export/mongodb/stats
```

#### 3. Procesamiento Combinado
```bash
# Procesar emails Y exportar en múltiples formatos
POST /export/process-and-export?export_types=ascont,completo,mongodb

# Solo tipos específicos
POST /export/process-and-export?export_types=completo,mongodb
```

### Desde el Frontend

Los nuevos exportadores se integran automáticamente en el dashboard existente:

1. **Botón "Export Completo"**: Genera Excel con análisis detallado
2. **Botón "Export MongoDB"**: Guarda en base documental
3. **Dropdown "Export Todo"**: Procesa emails + múltiples exports

## ⚙️ Configuración

### Variables de Entorno
```bash
# MongoDB
MONGODB_URL=mongodb://localhost:27017/
MONGODB_DATABASE=invoicesync_warehouse
MONGODB_COLLECTION=facturas_completas
ENABLE_MONGODB_EXPORT=true

# Excel Completo
ENABLE_EXCEL_COMPLETO=true
EXCEL_COMPLETO_RETENTION_MONTHS=12

# Performance
MONGO_BULK_SIZE=100
EXCEL_CHUNK_SIZE=1000
```

### Dependencias Adicionales
```bash
# Las dependencias se instalaron automáticamente:
pymongo==4.6.0    # Driver MongoDB
motor==3.3.2      # Async MongoDB driver
```

## 📈 Beneficios para Clientes Potenciales

### Para Contadores/Auditorías:
- **Excel Completo** con desglose detallado por productos
- **Validaciones automáticas** de CDC y timbrados
- **Estadísticas mensuales** para análisis de tendencias

### Para Empresas/BI:
- **MongoDB** para integración con sistemas de análisis
- **Consultas SQL-like** con agregaciones MongoDB
- **Escalabilidad** para crecimiento futuro

### Para Presentaciones:
- **Formato profesional** listo para mostrar
- **Múltiples perspectivas** de los mismos datos
- **Análisis automático** incluido

## 🔧 Mantenimiento

### Limpieza Automática:
- **Excel**: Retención configurable por meses
- **MongoDB**: TTL automático por días configurables
- **Índices**: Optimización automática de rendimiento

### Monitoreo:
- **Stats endpoint** para métricas de MongoDB
- **Health checks** incluidos
- **Logs detallados** para troubleshooting

## 📊 Casos de Uso

### 1. Auditoría Mensual
```bash
# Generar reporte completo del mes
curl -X POST "/export/excel-completo"

# Descargar archivo específico  
curl -O "/export/excel-completo/2025-01"
```

### 2. Análisis de Proveedores
```javascript
// Query MongoDB para top proveedores
db.facturas_completas.aggregate([
  {$group: {
    _id: "$emisor.ruc",
    nombre: {$first: "$emisor.nombre"},
    total_compras: {$sum: "$montos.monto_total"},
    cantidad_facturas: {$sum: 1}
  }},
  {$sort: {total_compras: -1}},
  {$limit: 10}
])
```

### 3. Validación de Calidad
```bash
# Obtener facturas con problemas de calidad
GET /export/mongodb/stats
# Revisar datos técnicos en Excel completo
```

## 🎯 Próximos Pasos

Los nuevos exportadores están **listos para producción** y proporcionan la flexibilidad necesaria para:

- **Demostrar capacidades** a clientes potenciales
- **Integrar con sistemas existentes** (ERP, BI, contabilidad)
- **Escalar según necesidades** (desde pequeñas empresas hasta corporaciones)

¿Alguna funcionalidad específica que necesites ajustar o agregar? 🚀