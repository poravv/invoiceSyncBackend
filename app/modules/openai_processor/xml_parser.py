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

    # -----------------------
    # Helpers numéricos robustos
    # -----------------------
    def _to_float(self, value: Optional[str]) -> float:
        """Convierte strings con formato ES/EN a float.
        Acepta 7400.00 o 7400,00 o 1.234,56 o 1,234.56.
        """
        if value is None:
            return 0.0
        s = str(value).strip().replace(' ', '')
        if not s:
            return 0.0
        try:
            if ',' in s and '.' in s:
                # El último separador es el decimal
                if s.rfind(',') > s.rfind('.'):
                    # decimal=',' → quitar puntos, coma→punto
                    s = s.replace('.', '')
                    s = s.replace(',', '.')
                else:
                    # decimal='.' → quitar comas
                    s = s.replace(',', '')
            elif ',' in s:
                # solo coma → decimal
                s = s.replace(',', '.')
            # else: solo punto o ninguno
            return float(s)
        except Exception:
            import re
            s2 = re.sub(r'[^0-9\.-]', '', s)
            try:
                return float(s2)
            except Exception:
                return 0.0

    def can_parse(self, xml_content: str) -> bool:
        try:
            root = ET.fromstring(xml_content)
            if any(tag in root.tag for tag in ['rDE', 'rLoteDE']):
                return True
            if self._find_element_by_name(root, 'DE') is not None:
                return True
            logger.warning("XML nativo: no se encontró nodo 'DE' ni raíz compatible (rDE/rLoteDE)")
            return False
        except Exception as e:
            logger.warning(f"XML nativo: error al parsear/leer XML: {e}")
            # Intento de recuperación: parsear solo el fragmento <DE>...</DE>
            frag = self._extract_de_fragment(xml_content)
            if frag:
                try:
                    ET.fromstring(frag)
                    logger.info("XML nativo: recuperación por fragmento <DE> exitosa")
                    return True
                except Exception as e2:
                    logger.warning(f"XML nativo: recuperación por fragmento falló: {e2}")
            return False

    def _find_element_by_name(self, element: ET.Element, name: str) -> Optional[ET.Element]:
        """Busca por localname exacto (ignora namespace). Evita confundir rDE con DE."""
        try:
            local = element.tag.split('}')[-1] if isinstance(element.tag, str) else ''
            if local == name:
                return element
            for child in element:
                result = self._find_element_by_name(child, name)
                if result is not None:
                    return result
        except Exception as e:
            logger.error(f"Error buscando el elemento {name}: {e}")

    def _find_element_by_name_in_de(self, de_element: ET.Element, name: str) -> Optional[ET.Element]:
        """Busca descendiente por localname exacto dentro del nodo DE."""
        for child in de_element.iter():
            try:
                local = child.tag.split('}')[-1] if isinstance(child.tag, str) else ''
                if local == name:
                    return child
            except Exception:
                continue
        return None

    def parse_xml(self, xml_content: str) -> Tuple[bool, Dict[str, Any]]:
        try:
            try:
                root = ET.fromstring(xml_content)
            except Exception:
                frag = self._extract_de_fragment(xml_content)
                if not frag:
                    logger.warning("XML nativo: no se pudo recuperar fragmento <DE>")
                    return False, {}
                root = ET.fromstring(frag)
            de_element = self._find_element_by_name(root, 'DE')
            if de_element is None:
                logger.warning("XML nativo: estructura SIFEN inválida: falta elemento 'DE'")
                return False, {}
            data = self._extract_basic_data(de_element)
            self._extract_operation_data(de_element, data)
            self._extract_entity_data(de_element, data)
            self._extract_items(de_element, data)
            self._extract_items_and_totals(de_element, data)
            try:
                logger.debug(f"XML (raw extract) -> {data}")
            except Exception:
                pass
            if self._validate_minimum_data(data):
                logger.info("✅ XML parseado exitosamente de forma nativa")
                return True, data
            else:
                logger.warning("⚠️ XML nativo: parseado pero faltan datos mínimos requeridos (fecha, numero_factura, ruc_emisor)")
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
        dPunExp = self._find_element_by_name_in_de(de_element, 'dPunExp')
        if num_doc is not None and dEst is not None and dPunExp is not None:
            data['numero_factura'] = (
                (dEst.text or "") + ("-"+dPunExp.text or "") + ("-"+num_doc.text or "")
            )
        num_tim = self._find_element_by_name_in_de(de_element, 'dNumTim')
        if num_tim is not None and num_tim.text:
            data['timbrado'] = num_tim.text
        # CDC: usar exclusivamente el atributo Id del nodo DE (44 dígitos numéricos)
        cdc_attr = de_element.attrib.get('Id')
        try:
            if cdc_attr and cdc_attr.isdigit() and len(cdc_attr) == 44:
                data['cdc'] = cdc_attr
            else:
                logger.debug(f"CDC no válido en atributo Id: {cdc_attr}")
        except Exception:
            logger.debug("No se pudo validar CDC desde atributo Id")
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
        # Tipo de cambio (si viene en el XML SIFEN)
        ti_cam = self._find_element_by_name_in_de(de_element, 'dTiCam')
        if ti_cam is not None and ti_cam.text:
            try:
                data['tipo_cambio'] = float(str(ti_cam.text).replace(',', '.'))
            except Exception:
                pass

    def _extract_entity_data(self, de_element: ET.Element, data: Dict[str, Any]):
        ruc_em = self._find_element_by_name_in_de(de_element, 'dRucEm')
        dv_em = self._find_element_by_name_in_de(de_element, 'dDVEmi')
        if ruc_em is not None and ruc_em.text:
            if dv_em is not None and dv_em.text:
                data['ruc_emisor'] = f"{ruc_em.text}-{dv_em.text}"
            else:
                data['ruc_emisor'] = ruc_em.text
        nom_em = self._find_element_by_name_in_de(de_element, 'dNomEmi')
        if nom_em is not None and nom_em.text:
            data['nombre_emisor'] = nom_em.text
        act_eco = self._find_element_by_name_in_de(de_element, 'cActEco')
        if act_eco is not None and act_eco.text:
            data['actividad_economica'] = act_eco.text
        ruc_rec = self._find_element_by_name_in_de(de_element, 'dRucRec')
        dv_rec = self._find_element_by_name_in_de(de_element, 'dDVRec')
        if ruc_rec is not None and ruc_rec.text:
            if dv_rec is not None and dv_rec.text:
                data['ruc_cliente'] = f"{ruc_rec.text}-{dv_rec.text}"
            else:
                data['ruc_cliente'] = ruc_rec.text
        nom_rec = self._find_element_by_name_in_de(de_element, 'dNomRec')
        if nom_rec is not None and nom_rec.text:
            data['nombre_cliente'] = nom_rec.text
        email_rec = self._find_element_by_name_in_de(de_element, 'dEmailRec')
        if email_rec is not None and email_rec.text:
            data['email_cliente'] = email_rec.text

    def _extract_items(self, de_element: ET.Element, data: Dict[str, Any]):
        """
        Extrae los productos del XML y los carga como una lista en data['productos'],
        en formato compatible con ProductoFactura (modelo Pydantic).
        """
        productos = []
        # Iterar ignorando namespace
        for item_element in de_element.iter():
            if not (isinstance(item_element.tag, str) and item_element.tag.endswith('gCamItem')):
                continue
            desc = self._get_text(item_element, 'dDesProSer')
            producto = {
                'articulo': desc or '',
                'cantidad': self._get_float(item_element, 'dCantProSer'),
                'precio_unitario': self._get_float(item_element, 'dPUniProSer'),
                'total': self._get_float(item_element, 'dTotBruOpeItem'),
            }

            cam_iva = self._find_element_by_name(item_element, 'gCamIVA')
            if cam_iva is not None:
                tasa = self._get_float(cam_iva, 'dTasaIVA')
                try:
                    producto['iva'] = int(float(tasa or 0))
                except Exception:
                    producto['iva'] = 0

            productos.append(producto)

        data['productos'] = productos

    # Métodos auxiliares recomendados dentro de la clase:
    def _get_text(self, element: ET.Element, tag: str) -> Optional[str]:
        el = self._find_element_by_name(element, tag)
        return el.text.strip() if el is not None and el.text else None

    def _get_float(self, element: ET.Element, tag: str) -> Optional[float]:
        txt = self._get_text(element, tag)
        return self._to_float(txt)

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
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            logger.warning(f"XML nativo: faltan campos mínimos: {missing}")
            return False
        return True

    def normalize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normaliza al contrato esperado por InvoiceData.from_dict.
        Nota: En este proyecto 'subtotal_5' y 'subtotal_10' representan la BASE (gravado),
        es decir, los montos SIN IVA. El XML SIFEN provee tanto los subtotales con IVA (dSub5/dSub10)
        como las bases (dBaseGrav5/dBaseGrav10). Usamos las bases para poblar 'subtotal_*'.
        """
        normalized: Dict[str, Any] = {}

        # Copiar campos directos
        for k in ['fecha', 'numero_factura', 'ruc_emisor', 'nombre_emisor',
                  'condicion_venta', 'moneda', 'tipo_cambio', 'monto_total',
                  'timbrado', 'cdc', 'ruc_cliente', 'nombre_cliente', 'email_cliente']:
            if k in data:
                normalized[k] = data[k]

        # Bases e IVA desde XML (preferir bases)
        base5 = data.get('gravado_5') if data.get('gravado_5') is not None else None
        base10 = data.get('gravado_10') if data.get('gravado_10') is not None else None
        iva5 = data.get('iva_5') if data.get('iva_5') is not None else None
        iva10 = data.get('iva_10') if data.get('iva_10') is not None else None

        if base5 is not None:
            normalized['subtotal_5'] = base5
            normalized['gravado_5'] = base5
        elif data.get('subtotal_5') is not None and iva5:
            # Si solo vino subtotal (con IVA) e IVA, estimar base
            normalized['subtotal_5'] = max(float(data['subtotal_5']) - float(iva5), 0.0)
            normalized['gravado_5'] = normalized['subtotal_5']

        if base10 is not None:
            normalized['subtotal_10'] = base10
            normalized['gravado_10'] = base10
        elif data.get('subtotal_10') is not None and iva10:
            normalized['subtotal_10'] = max(float(data['subtotal_10']) - float(iva10), 0.0)
            normalized['gravado_10'] = normalized['subtotal_10']

        if iva5 is not None:
            normalized['iva_5'] = iva5
        if iva10 is not None:
            normalized['iva_10'] = iva10

        # Exentas (no tienen IVA, pueden usarse tal cual)
        if data.get('subtotal_exentas') is not None:
            normalized['subtotal_exentas'] = data['subtotal_exentas']

        # Productos al formato del modelo
        productos = []
        for p in data.get('productos', []) or []:
            articulo = (p.get('articulo') or p.get('descripcion') or '')
            try:
                iva_val = int(float(p.get('iva', 0) or 0))
            except Exception:
                iva_val = 0
            productos.append({
                'articulo': articulo,
                'cantidad': p.get('cantidad', 0),
                'precio_unitario': p.get('precio_unitario', 0),
                'total': p.get('total', 0),
                'iva': iva_val,
            })
        if productos:
            normalized['productos'] = productos

        # descripcion_factura: concatenación breve de artículos
        if productos and not normalized.get('descripcion_factura'):
            articulos = [str(p.get('articulo', '')).strip() for p in productos if p.get('articulo')]
            if articulos:
                normalized['descripcion_factura'] = ', '.join(articulos[:10])  # limitar a 10 ítems

        return normalized

def parse_paraguayan_xml(xml_content: str) -> Tuple[bool, Dict[str, Any]]:
    parser = ParaguayanXMLParser()
    if not parser.can_parse(xml_content):
        logger.warning("XML nativo: no compatible con SIFEN o estructura inválida")
        return False, {}
    success, raw_data = parser.parse_xml(xml_content)
    if success:
        return True, parser.normalize_data(raw_data)
    return False, raw_data

    # -------- Helpers de recuperación ---------
def _find_fragment(content: str, start_tag: str, end_tag: str) -> Optional[str]:
    try:
        i = content.find(start_tag)
        if i == -1:
            return None
        j = content.find(end_tag, i)
        if j == -1:
            return None
        return content[i:j+len(end_tag)]
    except Exception:
        return None

def _strip_ns_declaration(fragment: str) -> str:
    # No modificamos namespaces; retornamos tal cual
    return fragment

def _wrap_if_needed(fragment: str) -> str:
    # Si el fragmento empieza con <DE ...> podemos parsearlo solo
    return fragment

def _safe_de_fragment(xml_content: str) -> Optional[str]:
    frag = _find_fragment(xml_content, '<DE ', '</DE>')
    if frag:
        return _wrap_if_needed(_strip_ns_declaration(frag))
    return None

setattr(ParaguayanXMLParser, "_extract_de_fragment", staticmethod(_safe_de_fragment))
