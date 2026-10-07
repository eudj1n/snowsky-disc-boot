"""Offline USB journal comparison shared by independent write/readback audits."""
import hashlib
from pathlib import Path
import stat


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compare(rows, expected, run: Path, transport, call_limit, executions, connection):
    require(rows[:2] == [{'phase': 'usb-discovery-attempt'},
                         {'phase': 'connection', **connection}], 'Discovery/connection differs')
    require((len(rows) - 2) % 2 == 0, 'Unpaired USB journal row')
    pairs = iter(zip(rows[2::2], rows[3::2]))
    reads = read_bytes = writes = calls = 0
    actual_executions = []
    for calls, (kind, number, parameter, data) in enumerate(expected, 1):
        require(calls <= call_limit, 'USB call budget exceeded')
        pair = next(pairs, None)
        require(pair is not None, f'Missing USB call {calls}')
        attempt, reply = pair
        require(attempt.get('sequence') == reply.get('sequence') == calls and
                attempt.get('phase') == 'attempt' and reply.get('phase') == 'return' and
                attempt.get('kind') == kind, f'USB call {calls} order/kind differs')
        if kind == 'control':
            require(attempt.get('request') == number and attempt.get('parameter') == parameter and
                    reply.get('code') == (5 if number == 0 else 0),
                    f'Control call {calls} differs')
            limit = transport['execution_timeout_ms' if number == 4 else 'control_timeout_ms']
            incoming = number == 0
            if number == 4:
                actual_executions.append(parameter)
        elif kind == 'bulk':
            require(attempt.get('endpoint') == number and attempt.get('bytes') == parameter and
                    reply.get('code') == 0 and reply.get('transferred') == parameter,
                    f'Bulk call {calls} differs')
            limit = transport['bulk_timeout_ms']
            incoming = number == 129
            require(attempt.get('outgoing_sha256') == (None if incoming else digest(data)),
                    f'Bulk source {calls} differs')
            if not incoming:
                writes += 1
        else:
            raise ValueError(f'Unexpected USB kind {kind}')
        require(0 < attempt.get('timeout_ms', 0) <= limit,
                f'USB call {calls} timeout differs')
        if incoming:
            name = reply.get('file')
            require(name == f'read-{calls:03}.bin' and reply.get('bytes') == len(data),
                    f'Saved read {calls} name/length differs')
            saved = run / name
            require(stat.S_ISREG(saved.lstat().st_mode), f'Saved read {calls} is not regular')
            actual = saved.read_bytes()
            require(actual == data and digest(actual) == reply.get('sha256'),
                    f'Saved read {calls} bytes differ')
            reads += 1
            read_bytes += len(actual)
        else:
            require('file' not in reply, f'Unsolicited read file at call {calls}')
    require(next(pairs, None) is None and calls > 0, 'Extra or empty USB journal')
    require(actual_executions == executions, 'Executed unexpected RAM entry')
    require(len(list(run.glob('read-*.bin'))) == reads, 'Unaccounted saved USB reads')
    return dict(calls=calls, raw_reads=reads, read_bytes=read_bytes,
                bulk_writes=writes, executions=actual_executions)
