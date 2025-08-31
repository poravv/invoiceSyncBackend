#!/usr/bin/env python3
"""
Parser XML nativo para facturas electrónicas paraguayas (SIFEN v150)
Más rápido y eficiente que OpenAI para estructuras estándar
"""

import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime
import re

logger = logging.getLogger(__name__)

class ParaguayanXMLParser:
    """Parser nativo para facturas electrónicas paraguayas"""

    def __init__(self):
        self.namespaces = {
            'sifen': 'http://ekuatia.set.gov.py/sifen/xsd',
            'dsig': 'http://www.w3.org/2000/09/xmldsig#'
        }

    def can_parse(self, xml_content: str) -> bool:
        try:
            root = ET.fromstring(xml_content)
            if any(tag in root.tag for tag in ['rDE', 'rLoteDE']):
                return True
            if self._find_element_by_name(root, 'DE') is not None:
                return True
            return False
        except Exception as e:
            logger.debug(f"XML no puede ser parseado nativamente: {e}")
            return False

    def _find_element_by_name(self, element: ET.Element, name: str) -> Optional[ET.Element]:
        try:
            for namespace_prefix, namespace_uri in self.namespaces.items():
                qualified_name = f"{{{namespace_uri}}}{name}"
                if element.tag == qualified_name or element.tag.endswith(name):
                    return element
            for child in element:
                result = self._find_element_by_name(child, name)
                if result is not None:
                    return result
        except Exception as e:
            logger.error(f"Error buscando el elemento {name}: {e}")

    def _find_element_by_name_in_de(self, de_element: ET.Element, name: str) -> Optional[ET.Element]:
        for child in de_element.iter():
            if child.tag.endswith(name):
                return child
        return None

    def parse_xml(self, xml_content: str) -> Tuple[bool, Dict[str, Any]]:
        try:
            root = ET.fromstring(xml_content)
            de_element = self._find_element_by_name(root, 'DE')
            if de_element is None:
                logger.debug("No se encontró elemento DE en el XML")
                return False, {}
            data = self._extract_basic_data(de_element)
            self._extract_operation_data(de_element, data)
            self._extract_entity_data(de_element, data)
            self._extract_items(de_element, data)
            self._extract_items_and_totals(de_element, data)
            if self._validate_minimum_data(data):
                logger.info("✅ XML parseado exitosamente de forma nativa")
                return True, data
            else:
                logger.warning("⚠️ XML parseado pero faltan datos mínimos")
                return False, data
        except Exception as e:
            logger.error(f"Error parseando XML nativamente: {e}")
            return False, {}

    def _extract_basic_data(self, de_element: ET.Element) -> Dict[str, Any]:
        data = {}
        fecha_emision = self._find_element_by_name_in_de(de_element, 'dFeEmiDE')
        if fecha_emision is not None and fecha_emision.text:
            data['fecha'] = fecha_emision.text[:10] if len(fecha_emision.text) >= 10 else fecha_emision.text
        num_doc = self._find_element_by_name_in_de(de_element, 'dNumDoc')
        dEst = self._find_element_by_name_in_de(de_element, 'dEst')
        dPunExp = self._f(de_element, 'dPunExp')
        if num_doc is not None and dEst is not None and dPunExp is not None:
            data['numero_factura'] = (
                (dEst.text or "") + ("-"+dPunExp.text or "") + ("-"+num_doc.text or "")
            )
        num_tim = self._find_element_by_name_in_de(de_element, 'dNumTim')
        if num_tim is not None and num_tim.text:
            data['timbrado'] = num_tim.text
        # Intentar extraer el CDC desde dCodSeg
        cdc = self._find_element_by_name_in_de(de_element, 'dCodSeg')
        if cdc is not None and cdc.text:
            data['cdc'] = cdc.text
        else:
            # Si no se encontró, usar el atributo Id del nodo DE
            cdc_attr = de_element.attrib.get('Id')
            if cdc_attr:
                data['cdc'] = cdc_attr
        return data

    def _extract_operation_data(self, de_element: ET.Element, data: Dict[str, Any]):
        tipo_tra = self._find_element_by_name_in_de(de_element, 'iTipTra')
        if tipo_tra is not None and tipo_tra.text:
            if tipo_tra.text == "1":
                data['tipo_transaccion'] = "Venta de mercadería"
            elif tipo_tra.text == "2":
                data['tipo_transaccion'] = "Prestación de servicios"
        cond_ope = self._find_element_by_name_in_de(de_element, 'dDCondOpe')
        if cond_ope is not None and cond_ope.text:
            data['condicion_venta'] = cond_ope.text
        moneda = self._find_element_by_name_in_de(de_element, 'cMoneOpe')
        if moneda is not None and moneda.text:
            data['moneda'] = moneda.text

    def _extract_entity_data(self, de_element: ET.Element, data: Dict[str, Any]):
        ruc_em = self._find_element_by_name_in_de(de_element, 'dRucEm')
        if ruc_em is not None and ruc_em.text:
            data['ruc_emisor'] = ruc_em.text
        nom_em = self._find_element_by_name_in_de(de_element, 'dNomEmi')
        if nom_em is not None and nom_em.text:
            data['nombre_emisor'] = nom_em.text
        act_eco = self._find_element_by_name_in_de(de_element, 'cActEco')
        if act_eco is not None and act_eco.text:
            data['actividad_economica'] = act_eco.text
        ruc_rec = self._find_element_by_name_in_de(de_element, 'dRucRec')
        if ruc_rec is not None and ruc_rec.text:
            data['ruc_cliente'] = ruc_rec.text
        nom_rec = self._find_element_by_name_in_de(de_element, 'dNomRec')
        if nom_rec is not None and nom_rec.text:
            data['nombre_cliente'] = nom_rec.text

    def _extract_items(self, de_element: ET.Element, data: Dict[str, Any]):
        """
        Extrae los productos del XML y los carga como una lista en data['productos'],
        en formato compatible con ProductoFactura (modelo Pydantic).
        """
        productos = []
        for item_element in de_element.findall('.//gCamItem'):
            producto = {
                'codigo_interno': self._get_text(item_element, 'dCodInt'),
                'descripcion': self._get_text(item_element, 'dDesProSer'),
                'unidad_medida': self._get_text(item_element, 'cUniMed'),
                'cantidad': self._get_float(item_element, 'dCantProSer'),
                'precio_unitario': self._get_float(item_element, 'dPUniProSer'),
                'total_bruto': self._get_float(item_element, 'dTotBruOpeItem'),
                'total': self._get_float(item_element, 'dTotBruOpeItem'),  # alias para compatibilidad
                'articulo': self._get_text(item_element, 'dDesProSer')     # alias para compatibilidad
            }

            cam_iva = self._find_element_by_name(item_element, 'gCamIVA')
            if cam_iva is not None:
                producto['afectacion_iva'] = self._get_text(cam_iva, 'iAfecIVA')
                producto['tasa_iva'] = self._get_float(cam_iva, 'dTasaIVA')
                producto['iva'] = int(float(producto['tasa_iva'] or 0))

            productos.append(producto)

        data['productos'] = productos

    # Métodos auxiliares recomendados dentro de la clase:
    def _get_text(self, element: ET.Element, tag: str) -> Optional[str]:
        el = self._find_element_by_name(element, tag)
        return el.text.strip() if el is not None and el.text else None

    def _get_float(self, element: ET.Element, tag: str) -> Optional[float]:
        try:
            txt = self._get_text(element, tag)
            return float(txt.replace(',', '')) if txt else 0.0
        except Exception:
            return 0.0

    def _extract_items_and_totals(self, de_element: ET.Element, data: Dict[str, Any]):
        total_fields = {
            'dTotGralOpe': 'monto_total',
            'dSub5': 'subtotal_5', 'dSub10': 'subtotal_10', 'dSubExe': 'subtotal_exentas',
            'dIVA5': 'iva_5', 'dIVA10': 'iva_10', 'dTotIVA': 'iva',
            'dBaseGrav5': 'gravado_5', 'dBaseGrav10': 'gravado_10'
        }
        for xml_key, field_name in total_fields.items():
            el = self._find_element_by_name_in_de(de_element, xml_key)
            if el is not None and el.text:
                try:
                    data[field_name] = float(el.text)
                except (ValueError, TypeError):
                    data[field_name] = 0.0

    def _validate_minimum_data(self, data: Dict[str, Any]) -> bool:
        required_fields = ['fecha', 'numero_factura', 'ruc_emisor']
        for field in required_fields:
            if field not in data or data[field] is None:
                logger.debug(f"Campo requerido faltante: {field}")
                return False
        return True

    def normalize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        normalized = {}
        field_mapping = {
            'fecha': 'fecha', 'numero_factura': 'numero_documento',
            'ruc_emisor': 'ruc_proveedor', 'nombre_emisor': 'razon_social_proveedor',
            'ruc_cliente': 'ruc_cliente', 'nombre_cliente': 'nombre_cliente',
            'monto_total': 'total_factura', 'subtotal_5': 'gravado_5',
            'subtotal_10': 'gravado_10', 'subtotal_exentas': 'exento',
            'iva_5': 'iva_5', 'iva_10': 'iva_10', 'iva': 'total_iva',
            'timbrado': 'timbrado', 'cdc': 'cdc', 'moneda': 'moneda',
            'condicion_venta': 'condicion_compra', 'actividad_economica': 'actividad_economica'
        }
        for k, v in field_mapping.items():
            if k in data:
                normalized[v] = data[k]
        if 'condicion_compra' in normalized:
            normalized['tipo_documento'] = "CR" if "CREDITO" in normalized['condicion_compra'].upper() else "CO"
        if normalized.get('moneda') == 'PYG':
            normalized['moneda'] = 'GS'
        return normalized

def parse_paraguayan_xml(xml_content: str) -> Tuple[bool, Dict[str, Any]]:
    parser = ParaguayanXMLParser()
    if not parser.can_parse(xml_content):
        logger.debug("XML no puede ser parseado nativamente")
        return False, {}
    success, raw_data = parser.parse_xml(xml_content)
    if success:
        return True, parser.normalize_data(raw_data)
    return False, raw_data