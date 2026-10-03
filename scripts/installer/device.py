"""The player's NAND as the installer meets it through USB Boot (plan, stage 4).

The geometry comes from the reviewed profiles: the chip (firmware/chips/, pages, spare area,
blocks, the factory bad-block marker) and the writer the firmware profile names
(firmware/writers/: the primary rootfs's first block, its logical blocks, the reserve for bad
blocks). A write puts the image's logical blocks into the good blocks from the start block on,
skipping bad ones within the reserve; a readback reads them back the same way, and every byte
is compared. Nothing is retried after an uncertain outcome.

Simulated is a NAND file with its spare area, for checks without a player, with faults on
request: no device, bad blocks, a write that stops, a flipped byte in the readback. The player's
own backend goes through the reviewed tools (scripts/deployment/) and is the next part.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DeviceError(Exception):
    pass


class Geometry:
    def __init__(self, chip, writer):
        self.chip_id, self.chip_name = chip['id_prefix_hex'], chip['family']
        self.page, self.spare, self.pages = chip['page_bytes'], chip['oob_bytes'], chip['pages_per_block']
        self.blocks, self.block = chip['blocks'], chip['page_bytes'] * chip['pages_per_block']
        self.marker = chip['factory_marker']
        if writer['block_bytes'] != self.block:
            raise DeviceError('the writer and the chip disagree on the block size')
        self.start, self.logical, self.reserve = writer['start_block'], writer['logical_blocks'], writer['bad_block_reserve']
        self.image_bytes = self.logical * self.block

    @classmethod
    def for_profile(cls, profile):
        chip = json.loads((ROOT/'firmware/chips/xt26g02c.json').read_text())
        writer = json.loads((ROOT/'firmware/writers'/f'{profile["writer"]}.json').read_text())
        return cls(chip, writer)

    def scaled(self, blocks, start, logical, reserve):
        """The same chip's pages on fewer blocks (tests)."""
        other = object.__new__(Geometry)
        other.__dict__.update(self.__dict__, blocks=blocks, start=start, logical=logical, reserve=reserve)
        other.image_bytes = logical * self.block
        return other


def sha256_file(path):
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


class Simulated:
    """A player's NAND in two files (data and spare area). faults: {'no-device': True,
    'bad-blocks': [n, ...], 'write-stops': logical block, 'readback-flip': byte offset}."""

    def __init__(self, folder, geometry, faults=None):
        self.g, self.faults = geometry, faults or {}
        self.data, self.spare = Path(folder)/'nand.bin', Path(folder)/'nand-spare.bin'
        if not self.data.exists():
            self.data.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data, 'wb') as f:
                f.truncate(self.g.blocks * self.g.block)
            with open(self.spare, 'wb') as f:
                f.write(b'\xff' * (self.g.blocks * self.g.pages * self.g.spare))
            for block in self.faults.get('bad-blocks', []):
                self._mark_bad(block)

    def _mark_bad(self, block):
        with open(self.spare, 'r+b') as f:
            f.seek((block * self.g.pages + self.g.marker['page_in_block']) * self.g.spare + self.g.marker['column'] - self.g.page)
            f.write(b'\x00')

    def bad(self, block):
        with open(self.spare, 'rb') as f:
            f.seek((block * self.g.pages + self.g.marker['page_in_block']) * self.g.spare + self.g.marker['column'] - self.g.page)
            return f.read(1) != bytes([self.g.marker['good_value']])

    def identify(self):
        if self.faults.get('no-device'):
            raise DeviceError('no player in USB Boot was found: hold Volume Down while connecting the cable')
        bad = [b for b in range(self.g.blocks) if self.bad(b)]
        return dict(chip=self.g.chip_name, idPrefix=self.g.chip_id, blocks=self.g.blocks, badBlocks=bad, simulated=True)

    def mapping(self):
        """The physical blocks that hold the image's logical blocks, in order."""
        good = [b for b in range(self.g.start, self.g.start + self.g.logical + self.g.reserve) if not self.bad(b)]
        if len(good) < self.g.logical:
            raise DeviceError(f'only {len(good)} good blocks for {self.g.logical}: more bad blocks than the reserve')
        return good[:self.g.logical]

    def backup(self, target, progress=lambda f: None):
        """All of it: every block's data and its spare area, with the bad blocks as they are."""
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        with open(self.data, 'rb') as src, open(target/'nand.bin', 'wb') as out:
            for block in range(self.g.blocks):
                out.write(src.read(self.g.block))
                progress((block + 1) / self.g.blocks)
        target_spare = target/'nand-spare.bin'
        target_spare.write_bytes(self.spare.read_bytes())
        return dict(data=str(target/'nand.bin'), sha256=sha256_file(target/'nand.bin'), spare=str(target_spare),
                    spareSha256=sha256_file(target_spare), bytes=self.g.blocks * self.g.block)

    def write(self, image, progress=lambda f: None):
        image = Path(image)
        if image.stat().st_size != self.g.image_bytes:
            raise DeviceError(f'the image is {image.stat().st_size} bytes, the writer takes {self.g.image_bytes}')
        stops = self.faults.get('write-stops')
        with open(image, 'rb') as src, open(self.data, 'r+b') as nand:
            for k, block in enumerate(self.mapping()):
                if stops is not None and k == stops:
                    raise DeviceError(f'the write stopped at logical block {k}: the outcome is uncertain, nothing is retried')
                nand.seek(block * self.g.block)
                nand.write(src.read(self.g.block))
                progress((k + 1) / self.g.logical)

    def readback(self, target, progress=lambda f: None):
        flip = self.faults.get('readback-flip')
        with open(self.data, 'rb') as nand, open(target, 'wb') as out:
            for k, block in enumerate(self.mapping()):
                nand.seek(block * self.g.block)
                chunk = bytearray(nand.read(self.g.block))
                if flip is not None and k * self.g.block <= flip < (k + 1) * self.g.block:
                    chunk[flip - k * self.g.block] ^= 0xff
                out.write(chunk)
                progress((k + 1) / self.g.logical)
        return Path(target)


def compare(image, readback):
    """Every byte: the first difference, or None."""
    with open(image, 'rb') as a, open(readback, 'rb') as b:
        offset = 0
        while True:
            x, y = a.read(1 << 20), b.read(1 << 20)
            if x != y:
                for k, (p, q) in enumerate(zip(x, y)):
                    if p != q:
                        return offset + k
                return offset + min(len(x), len(y))
            if not x:
                return None
            offset += len(x)
