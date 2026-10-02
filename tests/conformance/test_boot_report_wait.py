"""Firmware-free checks for observing a report while its final copy is in progress."""
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('packed_report', ROOT/'tests/integration/boot_report.py')
report = importlib.util.module_from_spec(spec); spec.loader.exec_module(report)
COMPLETE = b'DISC_WEB_BOOT_REPORT_V1\nEND_DISC_WEB_BOOT_REPORT_V1\n'


class ReportObservationTests(unittest.TestCase):
    def test_missing_empty_and_partial_publication_require_complete_footer(self):
        target = Mock()
        target.open.side_effect = [FileNotFoundError(), io.BytesIO(b''),
                                   io.BytesIO(COMPLETE[:-1]), io.BytesIO(COMPLETE)]
        with patch.object(report.time, 'monotonic', return_value=0), patch.object(report.time, 'sleep') as sleep:
            self.assertEqual(report.wait_for_report(target, 1, 1024), COMPLETE)
        self.assertEqual(sleep.call_count, 3)

    def test_incomplete_report_stops_at_deadline(self):
        target = Mock()
        target.open.return_value = io.BytesIO(COMPLETE[:-1])
        with patch.object(report.time, 'monotonic', side_effect=[0, 1]), patch.object(report.time, 'sleep'):
            with self.assertRaisesRegex(AssertionError, 'did not finish'):
                report.wait_for_report(target, 1, 1024)
        target.open.assert_called_once_with('rb')

    def test_oversized_report_is_rejected_even_with_footer(self):
        target = Mock()
        target.open.return_value = io.BytesIO(COMPLETE)
        with patch.object(report.time, 'monotonic', return_value=0):
            with self.assertRaisesRegex(AssertionError, 'exceeds profile limit'):
                report.wait_for_report(target, 1, len(COMPLETE)-1)


if __name__ == '__main__': unittest.main()
