"""Offline framing for the experimental little-endian NAND core ABI.

No device transport. Validated framing/CRC is not chip or backup qualification.
"""
import struct
import zlib

REQUEST_MAGIC, RESULT_MAGIC, DONE = 0x3151524e, 0x3153524e, 0x454e4f44
DATA_MAX = 4352
# A page read by digest (plan, stage 4b; device/acquisition/digest.h): the full result's words,
# the first OOB bytes and the SHA-256 of the main bytes, in 128 bytes.
DIGEST_MAGIC, DIGEST_BYTES = 0x3144524e, 128


def require(ok, message):
    if not ok:
        raise ValueError(message)


def encode_request(operation, nonce, sequence, page=0):
    require(type(operation) is int and operation in (1, 2), 'Unknown operation')
    require(isinstance(nonce, bytes) and len(nonce) == 16 and any(nonce), 'Expected nonzero 128-bit nonce')
    require(type(sequence) is int and 0 <= sequence <= 0xffffffff, 'Invalid sequence')
    require(type(page) is int and 0 <= page <= 0xffffff and (operation == 2 or page == 0), 'Invalid physical page')
    return struct.pack('<3I16s5I', REQUEST_MAGIC, 1, operation, nonce, sequence, page, 0, 0, 0)


def decode_result(raw, request, expected_bytes):
    require(isinstance(raw, bytes) and len(raw) == 76 + DATA_MAX, 'Incomplete/oversized result')
    require(isinstance(request, bytes) and len(request) == 48, 'Invalid saved request')
    q = struct.unpack('<12I', request)
    nonce = request[12:28]
    require(encode_request(q[2], nonce, q[7], q[8]) == request, 'Invalid request header/reserved fields')
    w = struct.unpack_from('<19I', raw)
    require(w[:3] == (RESULT_MAGIC, 1, DONE), 'Missing completed result header')
    require(w[4:8] == q[3:7] and (w[8], w[9], w[10]) == (q[7], q[2], q[8]),
            'Stale result or wrong sequence/operation/physical page')
    require(w[3] == 0, f'NAND core failed with code {w[3]}; payload is not valid')
    require(type(expected_bytes) is int and
            ((q[2] == 1 and expected_bytes == 0) or (q[2] == 2 and 0 < expected_bytes <= DATA_MAX)),
            'Missing valid trusted page+OOB length')
    require(w[11] == expected_bytes, 'Returned payload length differs from requested geometry')
    data = raw[76:76 + expected_bytes]
    require(zlib.crc32(data) == w[12], 'Payload CRC mismatch')
    require(not any(raw[76 + expected_bytes:]), 'Unexpected data after declared payload')
    return dict(page=w[10], observed_id=w[13], protect=w[14], feature=w[15], status=w[16],
                polls=w[17], transfers=w[18], data=data, hardware_qualified=False)


def decode_digest(raw, request, expected_bytes):
    """A digest record checked as a full result is, except the data's CRC, which only the player
    saw: the request it answers, completion, the core's code, the page's length."""
    require(isinstance(raw, bytes) and len(raw) == DIGEST_BYTES, 'Incomplete/oversized digest record')
    require(isinstance(request, bytes) and len(request) == 48, 'Invalid saved request')
    q = struct.unpack('<12I', request)
    nonce = request[12:28]
    require(encode_request(q[2], nonce, q[7], q[8]) == request and q[2] == 2, 'Invalid request header/reserved fields')
    w = struct.unpack_from('<19I', raw)
    require(w[:3] == (DIGEST_MAGIC, 1, DONE), 'Missing completed digest header')
    require(w[4:8] == q[3:7] and (w[8], w[9], w[10]) == (q[7], q[2], q[8]),
            'Stale record or wrong sequence/operation/physical page')
    require(w[3] == 0, f'NAND core failed with code {w[3]}; record is not valid')
    require(type(expected_bytes) is int and 8 < expected_bytes <= DATA_MAX and w[11] == expected_bytes,
            'Returned page length differs from requested geometry')
    require(not any(raw[120:]), 'Nonzero reserved digest words')
    return dict(page=w[10], observed_id=w[13], protect=w[14], feature=w[15], status=w[16],
                polls=w[17], transfers=w[18], oob_head=raw[76:84], main_sha256=raw[84:116],
                cycles=struct.unpack_from('<I', raw, 116)[0], hardware_qualified=False)
