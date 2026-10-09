#!/usr/bin/env python3
"""Offline namespace-preserving CryXmlB regression self-test, no game files needed."""
import xml.etree.ElementTree as ET
from evolve_gameplay_editor import encode_cryxml, decode_cryxml, guarded_serialize, parse_cryxml_text, semantic

sample = ET.Element('Workbook', {'xmlns': 'urn:schemas-microsoft-com:office:spreadsheet',
                                 'xmlns:ss': 'urn:schemas-microsoft-com:office:spreadsheet',
                                 'xmlns:o': 'urn:schemas-microsoft-com:office:office'})
worksheet = ET.SubElement(sample, 'Worksheet', {'ss:Name': 'Settings'})
ET.SubElement(worksheet, 'Cell', {'ss:Type': 'Number', 'value': '11'})
encoded = encode_cryxml(sample)
root = decode_cryxml(encoded)
ET.indent(root, space='  ')
xml = ET.tostring(root, encoding='utf-8', xml_declaration=True)
restored = parse_cryxml_text(xml)
assert semantic(decode_cryxml(guarded_serialize(restored))) == semantic(sample)
assert restored.get('xmlns:ss') == sample.get('xmlns:ss')
assert list(restored)[0].get('ss:Name') == 'Settings'
list(list(restored)[0])[0].set('value', '15')
modified = decode_cryxml(guarded_serialize(restored))
assert list(list(modified)[0])[0].get('value') == '15'
assert modified.attrib == sample.attrib
print('PASS: Namespace attributes and qualified tag/attribute names are preserved.')
print('PASS: Manual attribute edit survives XML -> CryXmlB reconstruction.')
print('PASS: CryXmlB semantic guard remains active.')
