"""Twofish ECB bulk tests against published vectors and reference CTR."""
import os
import random
import unittest

from native_twofish_bulk import GcryptBulkTwofish, candidate_libraries
from evolve_video_pak_tool_fixed import Twofish, twofish_ctr


class TwofishBulkTests(unittest.TestCase):
    def test_published_zero_128_bit_vector(self):
        # Standard 128-bit all-zero-key, all-zero-plaintext Twofish vector.
        cipher = Twofish(bytes(16))
        try:
            self.assertEqual(
                cipher.encrypt_blocks(bytes(16)).hex(),
                '9f589f5cf6122c32b6bfec2f2ae8c35a')
        finally:
            cipher.close()

    def test_bulk_matches_reference_library_when_available(self):
        try:
            from twofish import Twofish as Reference
        except ImportError:
            self.skipTest('Python twofish reference library not installed')
        rng = random.Random(19371)
        for _ in range(12):
            key = bytes(rng.randrange(256) for i in range(16))
            count = rng.randrange(1, 33)
            blocks = bytes(rng.randrange(256) for i in range(16 * count))
            expected = b''.join(
                Reference(key).encrypt(blocks[i:i + 16])
                for i in range(0, len(blocks), 16))
            try:
                native = GcryptBulkTwofish(key)
            except OSError:
                self.skipTest('No approved native bulk provider installed')
            try:
                self.assertEqual(native.encrypt_blocks(blocks), expected)
            finally:
                native.close()

    def test_ctr_partial_blocks_offsets_and_counter_wrap(self):
        rng = random.Random(2076)
        key = bytes(range(16))
        for size in (0, 1, 15, 16, 17, 127, 4095, 65537):
            plaintext = bytes(rng.randrange(256) for _ in range(size))
            for offset in (0, 1, 2**16, 2**128 - 2):
                iv = (2**128 - 3).to_bytes(16, 'little')
                encrypted = twofish_ctr(plaintext, key, iv, offset)
                self.assertEqual(twofish_ctr(encrypted, key, iv, offset), plaintext)
                self.assertEqual(len(encrypted), len(plaintext))

    @unittest.skipUnless(os.name == 'nt', 'Windows-only DLL search policy')
    def test_windows_only_uses_existing_absolute_bundled_library(self):
        for candidate in candidate_libraries():
            self.assertTrue(os.path.isabs(candidate))
            self.assertTrue(os.path.isfile(candidate))


if __name__ == '__main__':
    unittest.main()
