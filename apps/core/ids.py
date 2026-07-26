"""UUIDv7 primary keys — TODO 0.3.1 / SEC 2.2.

Opaque, non-enumerable external identifiers, but time-ordered so they index well
as primary keys (unlike UUIDv4, which fragments B-trees badly at scale).

Python 3.14 ships uuid.uuid7(); we fall back to a local implementation below.
"""

from __future__ import annotations

import secrets
import time
import uuid

_stdlib_uuid7 = getattr(uuid, "uuid7", None)


def _local_uuid7() -> uuid.UUID:
    """RFC 9562 version 7 UUID: 48-bit ms timestamp, version, variant, random tail."""
    timestamp_ms = int(time.time() * 1000)
    data = bytearray(16)
    data[0:6] = timestamp_ms.to_bytes(6, "big")
    data[6:16] = secrets.token_bytes(10)
    data[6] = (data[6] & 0x0F) | 0x70  # version 7
    data[8] = (data[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(data))


def uuid7() -> uuid.UUID:
    if _stdlib_uuid7 is not None:
        return _stdlib_uuid7()
    return _local_uuid7()
