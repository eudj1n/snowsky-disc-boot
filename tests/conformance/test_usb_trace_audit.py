"""Firmware-free rejection tests for the independent saved USB journal checker."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'usb_trace_audit', ROOT/'scripts/deployment/usb_trace_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class UsbTraceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.data = b'X2000'
        self.transport = {'control_timeout_ms': 100, 'bulk_timeout_ms': 200,
                          'execution_timeout_ms': 300}
        self.expected = [('control', 0, 0, self.data),
                         ('bulk', 1, 3, b'abc'),
                         ('bulk', 129, 3, b'xyz'),
                         ('control', 4, 4096, None)]
        self.connection = {'bus': 1}
        self.rows = [{'phase': 'usb-discovery-attempt'},
                     {'phase': 'connection', **self.connection}]
        for seq, (kind, number, parameter, data) in enumerate(self.expected, 1):
            attempt = {'phase': 'attempt', 'sequence': seq, 'kind': kind,
                       'timeout_ms': 100}
            reply = {'phase': 'return', 'sequence': seq,
                     'code': 5 if kind == 'control' and number == 0 else 0}
            if kind == 'control':
                attempt.update(request=number, parameter=parameter)
            else:
                attempt.update(endpoint=number, bytes=parameter,
                               outgoing_sha256=None if number == 129 else hashlib.sha256(data).hexdigest())
                reply['transferred'] = parameter
            if number in (0, 129):
                name = f'read-{seq:03}.bin'
                (self.run/name).write_bytes(data)
                reply.update(file=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            self.rows.extend((attempt, reply))

    def check(self, rows=None, expected=None):
        return audit.compare(self.rows if rows is None else rows,
                             self.expected if expected is None else expected,
                             self.run, self.transport, 4, [4096], self.connection)

    def test_exact_calls_reads_and_one_ram_execution(self):
        self.assertEqual(self.check(), {'calls': 4, 'raw_reads': 2, 'read_bytes': 8,
                                        'bulk_writes': 1, 'executions': [4096]})

    def test_missing_extra_replayed_or_changed_call_is_rejected(self):
        variants = []
        rows = copy.deepcopy(self.rows); rows.pop(); variants.append(rows)
        rows = copy.deepcopy(self.rows); rows.extend(rows[-2:]); variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[4]['sequence'] = 1; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[4]['outgoing_sha256'] = '0'*64; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[6]['endpoint'] = 2; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[2]['timeout_ms'] = 101; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[3]['code'] = 0; variants.append(rows)
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.check(rows)
        with self.assertRaises(ValueError):
            audit.compare(self.rows, self.expected, self.run, self.transport, 3,
                          [4096], self.connection)
        with self.assertRaises(ValueError):
            audit.compare(self.rows, self.expected, self.run, self.transport, 4,
                          [8192], self.connection)

    def test_saved_read_is_exact_regular_and_accounted_for(self):
        saved = self.run/'read-003.bin'
        saved.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.check()
        saved.unlink()
        saved.symlink_to(self.run/'read-001.bin')
        with self.assertRaises(ValueError):
            self.check()
        saved.unlink()
        saved.write_bytes(b'xyz')
        (self.run/'read-999.bin').write_bytes(b'extra')
        with self.assertRaises(ValueError):
            self.check()


    def polled(self, timeouts, answered=True, flagged=True, poll_timeout=50):
        """The journal after the execution, with the completion poll (plan, stage 4c): `timeouts`
        unanswered asks, then the ROM's answer."""
        rows = copy.deepcopy(self.rows)
        seq = len(self.expected)
        for k in range(timeouts + (1 if answered else 0)):
            seq += 1
            attempt = {'phase': 'attempt', 'sequence': seq, 'kind': 'control', 'request': 0, 'parameter': 0,
                       'timeout_ms': poll_timeout}
            if flagged:
                attempt['poll'] = True
            reply = {'phase': 'return', 'sequence': seq, 'code': -7}
            if answered and k == timeouts:
                name = f'read-{seq:03}.bin'
                (self.run/name).write_bytes(self.data)
                reply.update(code=5, file=name, bytes=5, sha256=hashlib.sha256(self.data).hexdigest())
            rows.extend((attempt, reply))
        return rows

    def test_a_completion_poll_takes_timeouts_then_the_answer(self):
        expected = self.expected + [audit.ready(50, 200, self.data)]
        report = audit.compare(self.polled(2), expected, self.run, self.transport, 7, [4096], self.connection)
        self.assertEqual((report['calls'], report['raw_reads']), (7, 3))
        (self.run/'read-007.bin').unlink()
        report = audit.compare(self.polled(0), expected, self.run, self.transport, 5, [4096], self.connection)
        self.assertEqual(report['calls'], 5)

    def test_a_completion_poll_is_bounded_flagged_and_short(self):
        expected = self.expected + [audit.ready(50, 200, self.data)]
        for rows in (self.polled(4), self.polled(2, answered=False), self.polled(2, flagged=False),
                     self.polled(2, poll_timeout=51)):
            with self.subTest(rows=rows[-2:]), self.assertRaises(ValueError):
                audit.compare(rows, expected, self.run, self.transport, 20, [4096], self.connection)
        # An ask flagged as a poll where none is expected is refused too.
        rows = copy.deepcopy(self.rows)
        rows[2]['poll'] = True
        with self.assertRaises(ValueError):
            self.check(rows)


if __name__ == '__main__':
    unittest.main()
