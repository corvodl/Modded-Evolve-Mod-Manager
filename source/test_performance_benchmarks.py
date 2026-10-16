"""Benchmark runner verifies measurement counts without fragile timing targets."""
import unittest

from performance_benchmarks import measure, bench_spatial_index


class RepeatableBenchmarksTests(unittest.TestCase):
    def test_measure_five_comparable_iterations(self):
        hits = []
        result = measure(lambda: hits.append(True), repeats=5)
        self.assertEqual(len(hits), 5)
        self.assertEqual(result['count'], 5)
        self.assertEqual(len(result['samples_ms']), 5)
        self.assertGreaterEqual(result['median_ms'], result['min_ms'])
        self.assertLessEqual(result['median_ms'], result['max_ms'])

    def test_rejects_insufficient_samples(self):
        with self.assertRaises(ValueError):
            measure(lambda: None, repeats=4)

    def test_large_spatial_fixture_reports_each_stage(self):
        result = bench_spatial_index(5)
        self.assertEqual(result['placements'], 25000)
        self.assertEqual(result['build_ms']['count'], 5)
        self.assertEqual(result['60_camera_queries_ms']['count'], 5)


if __name__ == '__main__':
    unittest.main()
