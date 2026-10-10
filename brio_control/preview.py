"""Private, bounded shared-memory preview. No frames cross D-Bus."""

import mmap
import os
import struct
import tempfile
import time
from pathlib import Path

HEADER = struct.Struct("<QIIIQ")
OFFSET = 32
MAX_BYTES = 1920 * 1080 * 3


class PreviewWriter:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, self.path = tempfile.mkstemp(prefix="preview-", dir=directory)
        os.ftruncate(fd, OFFSET + MAX_BYTES)
        self.mapping = mmap.mmap(fd, OFFSET + MAX_BYTES)
        os.close(fd)
        self.sequence = 0

    def write(self, width, height, pixels):
        count = width * height * 3
        if not 0 < width <= 1920 or not 0 < height <= 1080 or len(pixels) != count:
            raise ValueError("invalid preview dimensions or pixel count")
        self.sequence += 2
        HEADER.pack_into(self.mapping, 0, self.sequence - 1, width, height, count, time.monotonic_ns())
        self.mapping[OFFSET:OFFSET + count] = pixels
        struct.pack_into("<Q", self.mapping, 0, self.sequence)

    def invalidate(self):
        self.sequence += 2
        HEADER.pack_into(self.mapping, 0, self.sequence, 0, 0, 0, 0)

    def close(self):
        self.invalidate()
        self.mapping.close()
        Path(self.path).unlink(missing_ok=True)


class PreviewReader:
    def __init__(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            stat = os.fstat(fd)
            if stat.st_uid != os.getuid() or stat.st_mode & 0o077 or stat.st_size != OFFSET + MAX_BYTES:
                raise ValueError("invalid private preview file")
            self.mapping = mmap.mmap(fd, OFFSET + MAX_BYTES, access=mmap.ACCESS_READ)
        finally:
            os.close(fd)

    def read(self):
        seq, width, height, count, timestamp = HEADER.unpack_from(self.mapping)
        if seq & 1 or not timestamp or time.monotonic_ns() - timestamp > 1_000_000_000:
            return None
        if not 0 < width <= 1920 or not 0 < height <= 1080 or count != width * height * 3:
            return None
        pixels = self.mapping[OFFSET:OFFSET + count]
        if struct.unpack_from("<Q", self.mapping)[0] != seq:
            return None
        return width, height, pixels

    def close(self):
        self.mapping.close()
