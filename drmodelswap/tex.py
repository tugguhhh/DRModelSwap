"""Dead Rising textures ("TEX\\0").

Header (0x18 bytes): "TEX\\0", u8 0x56, u8 type, u8 flags, u8 mip count, u32 width, u32 height, u32 0,
fourcc ("DXT1"/"DXT5"/...). When flags & 4, four floats follow (average colour; the colour maps
"_BM" have them). Then the mip levels, largest first, 4x4 blocks (a 1x1 or 2x2 level still takes a block).
"""
import math, struct
import numpy as np
from PIL import Image


def _hdr(b):
    if b[:4] != b'TEX\0':
        raise ValueError('not a TEX file')
    flags, mips = b[6], b[7]
    w, h = struct.unpack_from('<II', b, 8)
    return w, h, mips, b[0x14:0x18], 0x28 if flags & 4 else 0x18


def _565(v):
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31], -1).astype(np.float32)


def _decode_blocks(blk, w, h):
    """blk: (n, 8) uint8 colour blocks -> RGB image of w x h (multiple of 4)."""
    c0 = blk[:, 0].astype(np.uint16) | (blk[:, 1].astype(np.uint16) << 8)
    c1 = blk[:, 2].astype(np.uint16) | (blk[:, 3].astype(np.uint16) << 8)
    bits = blk[:, 4:8].copy().view('<u4')[:, 0]
    p0, p1 = _565(c0), _565(c1)
    four = (c0 > c1)[:, None]
    p2 = np.where(four, (2 * p0 + p1) / 3, (p0 + p1) / 2)
    p3 = np.where(four, (p0 + 2 * p1) / 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)  # n,4,3
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    px = np.take_along_axis(pal, idx[:, :, None].astype(np.int64).repeat(3, 2), 1)  # n,16,3
    bw, bh = w // 4, h // 4
    img = px.reshape(bh, bw, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(h, w, 3)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def decode(b, max_size=256):
    """Colour of a TEX as a PIL RGB image, using the first mip level not larger than max_size.
    Formats other than DXT1/3/5 come back as None (the preview shows them grey)."""
    w, h, mips, fcc, off = _hdr(b)
    if fcc not in (b'DXT1', b'DXT3', b'DXT5'):
        return None
    bs = 8 if fcc == b'DXT1' else 16
    for _ in range(max(1, mips)):
        n = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bs
        if (w <= max_size and h <= max_size) or _ == mips - 1:
            data = np.frombuffer(b, np.uint8, count=n, offset=off).reshape(-1, bs)
            blk = data[:, bs - 8:]
            ww, hh = max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4)
            return _decode_blocks(blk, ww, hh).crop((0, 0, max(1, w), max(1, h)))
        off += n
        w, h = max(1, w // 2), max(1, h // 2)
    return None


def _dxt1(img):
    a = np.asarray(img, dtype=np.float32)
    h, w, _ = a.shape
    if w < 4 or h < 4:
        a = np.asarray(img.resize((max(4, w), max(4, h)), Image.NEAREST), dtype=np.float32)
        h, w, _ = a.shape
    b = a.reshape(h // 4, 4, w // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3)
    mean = b.mean(1)
    axis = b.max(1) - b.min(1)
    axis = axis / np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-6)
    proj = ((b - mean[:, None]) * axis[:, None]).sum(2)
    lo = mean + axis * proj.min(1)[:, None]
    hi = mean + axis * proj.max(1)[:, None]

    def to565(c):
        c = np.clip(np.rint(c), 0, 255).astype(np.int32)
        return ((c[:, 0] * 31 + 127) // 255 << 11) | ((c[:, 1] * 63 + 127) // 255 << 5) | ((c[:, 2] * 31 + 127) // 255)

    c0, c1 = to565(hi), to565(lo)
    sw = c0 < c1
    c0[sw], c1[sw] = c1[sw], c0[sw]
    eq = c0 == c1
    p0, p1 = _565(c0), _565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)
    idx = ((b[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(3).argmin(2)
    idx[eq] = 0
    bits = (idx.astype(np.uint32) << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    out = np.zeros(len(b), dtype=[('c0', '<u2'), ('c1', '<u2'), ('bits', '<u4')])
    out['c0'], out['c1'], out['bits'] = c0, c1, bits
    return out.tobytes()


def encode(img, max_size=1024, colour_map=True):
    """PIL image -> TEX bytes (DXT1, all mips). The size is rounded to a power of two (4..max_size)."""
    img = img.convert('RGB')
    w, h = img.size
    p2 = lambda v: max(4, min(max_size, 1 << int(round(math.log2(max(v, 1))))))
    W, H = p2(w), p2(h)
    img = img.resize((W, H), Image.LANCZOS)
    mips, cw, ch, cur = [], W, H, img
    while True:
        mips.append(_dxt1(cur))
        if cw == 1 and ch == 1:
            break
        cw, ch = max(1, cw // 2), max(1, ch // 2)
        cur = img.resize((cw, ch), Image.BOX)
    hdr = bytearray(b'TEX\0') + bytes([0x56, 0x02, 0x04 if colour_map else 0x00, len(mips)]) + struct.pack('<III', W, H, 0) + b'DXT1'
    if colour_map:
        avg = np.asarray(img.resize((1, 1), Image.BOX), dtype=np.float32)[0, 0] / 255
        hdr += struct.pack('<4f', avg[0], avg[1], avg[2], 0.0)
    return bytes(hdr) + b''.join(mips)


def flat_normal_map(size=16):
    """A flat normal map in the game's own normal-map format (two signed bytes per pixel, D3D format 60 = V8U8)."""
    mips, w = [], size
    while True:
        mips.append(bytes(w * w * 2))
        if w == 1:
            break
        w //= 2
    hdr = b'TEX\0' + bytes([0x56, 0x02, 0x00, len(mips)]) + struct.pack('<III', size, size, 0) + struct.pack('<I', 60)
    return hdr + b''.join(mips) + bytes(2)
