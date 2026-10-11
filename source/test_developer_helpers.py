import unittest
from pathlib import Path
from developer_memory import parse_address, parse_hex
from online_guard import rule_name

class HelperTests(unittest.TestCase):
    def test_memory_validation(self):
        self.assertEqual(parse_address('0x1000'), 4096)
        self.assertEqual(parse_hex('00 00 80 3F'), bytes.fromhex('0000803f'))
        for value in ('', '0', '-1', 'invalid'):
            with self.assertRaises(ValueError):
                parse_address(value)
        for value in ('', 'ZQ', 'FF ' * 257):
            with self.assertRaises(ValueError):
                parse_hex(value)

    def test_rule_scoping(self):
        with self.assertRaises(ValueError):
            rule_name(Path('something.exe'))
        self.assertTrue(rule_name(Path('Evolve.exe')).startswith('EvolveModManager-Offline-'))
