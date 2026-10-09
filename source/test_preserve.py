import unittest
import xml.etree.ElementTree as ET
from cryxml_preserve import preserve_values
from evolve_gameplay_editor import encode_cryxml,decode_cryxml
class PreserveTests(unittest.TestCase):
 def test_identity(self):
  b=encode_cryxml(ET.fromstring('<r a="403"/>'))
  self.assertEqual(preserve_values(b,decode_cryxml(b)),b)
 def test_length_extended(self):
  b=encode_cryxml(ET.fromstring('<r a="403"/>'));e=decode_cryxml(b);e.set('a','1000')
  self.assertEqual(decode_cryxml(preserve_values(b,e)).get('a'),'1000')
 def test_shared_string_isolated(self):
  b=encode_cryxml(ET.fromstring('<r a="403" b="403"/>'));e=decode_cryxml(b);e.set('a','800')
  out=decode_cryxml(preserve_values(b,e))
  self.assertEqual(out.get('a'),'800');self.assertEqual(out.get('b'),'403')
 def test_structure_rejected(self):
  b=encode_cryxml(ET.fromstring('<r a="403"/>'));e=decode_cryxml(b);ET.SubElement(e,'new')
  with self.assertRaisesRegex(ValueError,'existing values'):preserve_values(b,e)
