"""Deterministic cancellation checks without game assets or a GL context."""
import unittest
from unittest.mock import patch

import async_model_resolve
import studio_polish_117


class ModelPreparationCancellationTests(unittest.TestCase):
    def test_cancel_before_looking_up_workspace(self):
        with patch.object(async_model_resolve, 'checked_path') as lookup:
            with self.assertRaises(InterruptedError):
                async_model_resolve.resolve_model(
                    '/unused', 'sample.skinm', cancel=lambda: True)
            lookup.assert_not_called()

    def test_cancel_after_read_does_not_parse_or_discover_dependencies(self):
        cancelled = [False]

        class Source:
            def read_bytes(self):
                cancelled[0] = True
                return b'ignored by cancellation'

        with patch.object(async_model_resolve, 'checked_path', return_value=Source()), \
             patch.object(async_model_resolve, 'inspect_model') as inspect:
            with self.assertRaises(InterruptedError):
                async_model_resolve.resolve_model(
                    '/unused', 'sample.skinm', cancel=lambda: cancelled[0])
            inspect.assert_not_called()

    def test_cancelled_cache_request_never_reads_model_or_reuses_result(self):
        with patch.object(studio_polish_117, 'model_cache_key') as key:
            with self.assertRaises(InterruptedError):
                studio_polish_117.prepare_model_cached(
                    '/unused', 'sample.skinm', cancel=lambda: True)
            key.assert_not_called()

    def test_cancelled_resolve_never_publishes_cache_entry(self):
        studio_polish_117.clear_model_cache()
        cancelled = [False]

        def resolved(*_args, **_kwargs):
            cancelled[0] = True
            return {'raw': b'content', 'geometry': None, 'materials': None}

        with patch.object(studio_polish_117, 'model_cache_key', return_value=('cancel-fixture',)), \
             patch.object(async_model_resolve, 'resolve_model', side_effect=resolved):
            with self.assertRaises(InterruptedError):
                studio_polish_117.prepare_model_cached(
                    '/unused', 'sample.skinm', cancel=lambda: cancelled[0])
        self.assertEqual(studio_polish_117.model_cache_stats()['entries'], 0)


if __name__ == '__main__':
    unittest.main()
