"""Offline USB journal comparison shared by independent write/readback audits."""
import hashlib
from pathlib import Path
import stat

LIBUSB_ERROR_TIMEOUT = -7


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ready(poll_ms, settle_ms, answer, tolerant=False):
    """The expected ROM asked until it answers after an execution (completion polling, plan, stage
    4c): CPU-info requests of at most poll_ms each, timeouts without data, then one answer; at most
    settle_ms/poll_ms asks in all. tolerant (the writer's wait): any error without data is also no
    answer yet."""
    return ('ready', poll_ms, -(-settle_ms // poll_ms), answer, tolerant)


def compare(rows, expected, run: Path, transport, call_limit, executions, connection):
    require(rows[:2] == [{'phase': 'usb-discovery-attempt'},
                         {'phase': 'connection', **connection}], 'Discovery/connection differs')
    require((len(rows) - 2) % 2 == 0, 'Unpaired USB journal row')
    pairs = iter(zip(rows[2::2], rows[3::2]))
    counts = dict(calls=0, reads=0, read_bytes=0, writes=0)
    actual_executions = []

    def take(kind):
        counts['calls'] += 1
        calls = counts['calls']
        require(calls <= call_limit, 'USB call budget exceeded')
        pair = next(pairs, None)
        require(pair is not None, f'Missing USB call {calls}')
        attempt, reply = pair
        require(attempt.get('sequence') == reply.get('sequence') == calls and
                attempt.get('phase') == 'attempt' and reply.get('phase') == 'return' and
                attempt.get('kind') == kind, f'USB call {calls} order/kind differs')
        return calls, attempt, reply

    def saved_read(calls, reply, data):
        name = reply.get('file')
        require(name == f'read-{calls:03}.bin' and reply.get('bytes') == len(data),
                f'Saved read {calls} name/length differs')
        saved = run / name
        require(stat.S_ISREG(saved.lstat().st_mode), f'Saved read {calls} is not regular')
        actual = saved.read_bytes()
        require(actual == data and digest(actual) == reply.get('sha256'),
                f'Saved read {calls} bytes differ')
        counts['reads'] += 1
        counts['read_bytes'] += len(actual)

    for item in expected:
        if item[0] == 'ready':
            _, poll, asks, answer, tolerant = item
            for _ in range(asks):
                calls, attempt, reply = take('control')
                require(attempt.get('request') == 0 and attempt.get('parameter') == 0 and attempt.get('poll') is True
                        and 0 < attempt.get('timeout_ms', 0) <= poll, f'Completion poll {calls} differs')
                if reply.get('code') == LIBUSB_ERROR_TIMEOUT or (tolerant and reply.get('code', 0) < 0):
                    require('file' not in reply, f'Unsolicited read file at call {calls}')
                    continue
                require(reply.get('code') == len(answer), f'Completion poll {calls} answer differs')
                saved_read(calls, reply, answer)
                break
            else:
                raise ValueError('The ROM did not answer within the settle')
            continue
        kind, number, parameter, data = item
        calls, attempt, reply = take(kind)
        require('poll' not in attempt, f'Unexpected completion poll at call {calls}')
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
                counts['writes'] += 1
        else:
            raise ValueError(f'Unexpected USB kind {kind}')
        require(0 < attempt.get('timeout_ms', 0) <= limit,
                f'USB call {calls} timeout differs')
        if incoming:
            saved_read(calls, reply, data)
        else:
            require('file' not in reply, f'Unsolicited read file at call {calls}')
    require(next(pairs, None) is None and counts['calls'] > 0, 'Extra or empty USB journal')
    require(actual_executions == executions, 'Executed unexpected RAM entry')
    require(len(list(run.glob('read-*.bin'))) == counts['reads'], 'Unaccounted saved USB reads')
    return dict(calls=counts['calls'], raw_reads=counts['reads'], read_bytes=counts['read_bytes'],
                bulk_writes=counts['writes'], executions=actual_executions)
