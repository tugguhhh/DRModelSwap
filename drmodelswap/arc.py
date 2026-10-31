"""Dead Rising .arc packages (version 4).

Layout: "ARC\\0", u16 version, u16 file count, then one 80-byte entry per file
(name[64], u32 type, u32 compressed size, u32 real size, u32 offset). File data is
zlib-compressed and starts at a 0x8000 boundary after the entry table.
"""
import os, struct, zlib

TYPE_MODEL = 0x1041BD9E    # rModel ("MOD")
TYPE_TEXTURE = 0x3CAD8076  # rTexture ("TEX")


class Entry:
    __slots__ = ('name', 'type', 'size', 'raw')

    def __init__(self, name, type_, size, raw):
        self.name, self.type, self.size, self.raw = name, type_, size, raw  # name: str (game path), raw: compressed bytes

    @classmethod
    def from_data(cls, name, type_, data, level=9):
        return cls(name, type_, len(data), zlib.compress(data, level))

    def data(self):
        return zlib.decompress(self.raw)


def read_header(path):
    """[(name, type, csize, size, offset)] without reading the file data."""
    with open(path, 'rb') as f:
        h = f.read(8)
        if len(h) < 8 or h[:4] != b'ARC\0':
            raise ValueError('not an ARC file: %s' % path)
        n = struct.unpack_from('<H', h, 6)[0]
        t = f.read(n * 80)
    out = []
    for i in range(n):
        o = i * 80
        name = t[o:o + 64].split(b'\0')[0].decode('latin1')
        typ, cs, ds, off = struct.unpack_from('<IIII', t, o + 64)
        out.append((name, typ, cs, ds, off))
    return out


def read_entry(path, name, type_):
    """Decompressed data of one file inside an arc, or None."""
    for nm, typ, cs, ds, off in read_header(path):
        if typ == type_ and nm.lower() == name.lower():
            with open(path, 'rb') as f:
                f.seek(off)
                return zlib.decompress(f.read(cs))
    return None


def read_entries(path, pred=None):
    """[(name, type, data)] for every file where pred(name, type) is true (all if pred is None)."""
    hdr = read_header(path)
    out = []
    with open(path, 'rb') as f:
        for nm, typ, cs, ds, off in hdr:
            if pred is None or pred(nm, typ):
                f.seek(off)
                out.append((nm, typ, zlib.decompress(f.read(cs))))
    return out


def read_arc(path):
    with open(path, 'rb') as f:
        d = f.read()
    if d[:4] != b'ARC\0':
        raise ValueError('not an ARC file: %s' % path)
    ver, n = struct.unpack_from('<HH', d, 4)
    ents = []
    for i in range(n):
        o = 8 + i * 80
        name = d[o:o + 64].split(b'\0')[0].decode('latin1')
        typ, cs, ds, off = struct.unpack_from('<IIII', d, o + 64)
        ents.append(Entry(name, typ, ds, d[off:off + cs]))
    return ver, ents


def write_arc(path, ver, ents):
    """Write an arc; goes through a temporary file so a failure never leaves half a file."""
    n = len(ents)
    start = (8 + 80 * n + 0x7FFF) // 0x8000 * 0x8000
    hdr = bytearray(b'ARC\0' + struct.pack('<HH', ver, n))
    off = start
    for e in ents:
        nm = e.name.encode('latin1')
        if len(nm) >= 64:
            raise ValueError('name too long for an arc: ' + e.name)
        hdr += nm + b'\0' * (64 - len(nm)) + struct.pack('<IIII', e.type, len(e.raw), e.size, off)
        off += len(e.raw)
    hdr += b'\0' * (start - len(hdr))
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(hdr)
        for e in ents:
            f.write(e.raw)
    os.replace(tmp, path)


def patch_entries(ents, payload):
    """Replace same-name/same-type entries with the payload's, add the rest. Returns (replaced, added)."""
    pos = {(e.name.lower(), e.type): i for i, e in enumerate(ents)}
    rep = add = 0
    for p in payload:
        k = (p.name.lower(), p.type)
        if k in pos:
            ents[pos[k]] = p
            rep += 1
        else:
            pos[k] = len(ents)
            ents.append(p)
            add += 1
    return rep, add
