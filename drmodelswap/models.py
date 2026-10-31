"""Reading a model in any supported form: .glb, .gltf (+ .bin + textures), .obj (+ .mtl + textures), given as one
file, several files picked together, a dropped folder, or a .zip of them. Files a model refers to are found by their
path relative to it, or else by file name anywhere among the files given (exporters often write absolute paths)."""
import io
import os
import posixpath
import struct
import json
import zipfile

import numpy as np

from . import blender, glb, objfile
from .glb import GlbError

MODEL_EXTS = ('.glb', '.gltf', '.obj') + blender.EXTS
ACCEPT = '.glb,.gltf,.obj,.mtl,.bin,.zip,.png,.jpg,.jpeg,.bmp,.tga,.webp,.tif,.tiff,.dds,.psd,.exr,.hdr,' + ','.join(blender.EXTS)
PACK_MAGIC = b'DRMSPACK'          # several files sent by the tool's page in one request
MAX_TOTAL = 1536 * 1024 * 1024


def _norm(p):
    p = p.replace('\\', '/').strip()
    while p.startswith('./'):
        p = p[2:]
    return posixpath.normpath(p).lstrip('/').lower() if p else ''


def unpack(name, data):
    """One upload -> {path: bytes}: a .zip, several files packed by the page, or a single file."""
    if data[:8] == PACK_MAGIC:
        n = struct.unpack_from('<I', data, 8)[0]
        head = json.loads(data[12:12 + n])
        out, o = {}, 12 + n
        for f in head:
            out[f['name']] = data[o:o + f['size']]
            o += f['size']
        # a .zip among them: open it too
        for k in [k for k in out if k.lower().endswith('.zip')]:
            z = out.pop(k)
            out.update({posixpath.join(posixpath.dirname(k), m): v for m, v in unpack(k, z).items()})
        return out
    if data[:4] == b'PK\x03\x04' or name.lower().endswith('.zip'):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise GlbError('this .zip file can\'t be opened')
        out, total = {}, 0
        for zi in zf.infolist():
            if zi.is_dir() or zi.filename.startswith('__MACOSX/') or '/.' in '/' + zi.filename:
                continue
            total += zi.file_size
            if total > MAX_TOTAL:
                raise GlbError('this .zip unpacks to more than 1.5 GB')
            out[zi.filename] = zf.read(zi)
        return out
    return {name: data}


def pick_main(files):
    cands = [k for k in files if os.path.splitext(k)[1].lower() in MODEL_EXTS]
    if not cands:
        raise GlbError('no model file (.glb, .gltf, .obj, .fbx, .blend...) among: %s' % ', '.join(sorted(files)[:8]))
    rank = {'.glb': 0, '.gltf': 1, '.obj': 2}
    rank.update({e: 3 + k for k, e in enumerate(blender.EXTS)})
    # the preferred kind; of several, the biggest (the model, not a low-poly proxy)
    return min(cands, key=lambda k: (rank[os.path.splitext(k)[1].lower()], -len(files[k]), k))


def resolver(files, main):
    by_path = {_norm(k): v for k, v in files.items()}
    by_name = {}
    for k, v in files.items():
        by_name.setdefault(posixpath.basename(_norm(k)), v)
    base = posixpath.dirname(_norm(main))

    def resolve(uri):
        u = _norm(uri)
        if not u:
            return None
        for cand in (_norm(posixpath.join(base, u)), u):
            if cand in by_path:
                return by_path[cand]
        return by_name.get(posixpath.basename(u))
    return resolve


def bake_colours(parts, mats):
    """Vertex colours (low-poly models are often painted this way, with no texture) -> a small palette texture, since
    the game's models take textures, not vertex colours. Each triangle gets one colour (the average of its corners)
    and its corners point at that colour's cell (a texture lookup blended between cells would show other colours);
    a corner is split only where neighbouring triangles differ. Parts whose material has a texture keep it.
    -> (parts, materials)"""
    from PIL import Image
    use = [i for i, p in enumerate(parts) if 'colors' in p[5] and len(p[4]) and not (0 <= p[0] < len(mats) and mats[p[0]]['image'] is not None)
           and np.ptp(p[5]['colors'], 0).max() > 1e-3]
    if not use:
        return parts, mats
    face = []
    for i in use:
        mi, P, N, UV, T, info = parts[i]
        base = np.array(mats[mi]['colour'] if 0 <= mi < len(mats) else [1, 1, 1])
        face.append(np.clip(info['colors'][T].mean(1) * base, 0, 1))
    allc = np.concatenate(face)
    for bits in (6, 5, 4, 3):
        q = np.round(allc * (2 ** bits - 1)).astype(np.int64)
        pal, inv = np.unique(q, axis=0, return_inverse=True)
        if len(pal) <= 4096:
            break
    inv = inv.reshape(-1)
    side = int(np.ceil(np.sqrt(len(pal))))
    cell = 4
    size = 1 << int(np.ceil(np.log2(max(side * cell, 16))))
    img = np.zeros((size, size, 3), np.uint8)
    rgb = np.round(pal / (2 ** bits - 1) * 255).astype(np.uint8)
    for k, c in enumerate(rgb):
        y, x = divmod(k, side)
        img[y * cell:(y + 1) * cell, x * cell:(x + 1) * cell] = c
    mats = list(mats) + [dict(name='vertex colours', image=Image.fromarray(img, 'RGB'), colour=[1.0, 1.0, 1.0])]
    new_mi = len(mats) - 1
    parts = list(parts)
    o = 0
    for i, fc in zip(use, face):
        mi, P, N, _, T, info = parts[i]
        k = inv[o:o + len(fc)]
        o += len(fc)
        # one vertex per (old vertex, colour) pair
        corner = np.c_[T.reshape(-1), np.repeat(k, 3)]
        uniq, cinv = np.unique(corner, axis=0, return_inverse=True)
        cinv = cinv.reshape(-1)
        y, x = np.divmod(uniq[:, 1], side)
        UV = np.c_[(x * cell + cell / 2) / size, (y * cell + cell / 2) / size]
        info = {kk: vv for kk, vv in info.items() if kk != 'colors'}
        parts[i] = (new_mi, P[uniq[:, 0]], None if N is None else N[uniq[:, 0]], UV, cinv.reshape(-1, 3), info)
    return parts, mats


BLENDER = {'path': None}      # set by the tool's page (saved in its config) when Blender isn't where installers put it


def _blender():
    exe = blender.find(BLENDER['path'])
    if not exe:
        raise blender.BlenderMissing('.fbx, .blend, .dae and similar files are opened with Blender (free, blender.org), which '
                                     'wasn\'t found on this PC. Install it, or tell the tool where blender.exe is.')
    return exe


def load_files(files, main=None):
    """-> (parts, materials, info); info['file'] = the model file used, info['missing'] = files it refers to that
    weren't given (their textures are then plain colour)."""
    main = main or pick_main(files)
    data = files[main]
    if os.path.splitext(main)[1].lower() in blender.EXTS:
        parts, mats, info = glb.load(blender.convert(files, main, _blender()))
        info['file'], info['format'], info['via'] = posixpath.basename(main.replace('\\', '/')), os.path.splitext(main)[1].lstrip('.').lower(), 'Blender'
        parts, mats = bake_colours(parts, mats)
        info['materials'] = len(mats)
        return parts, mats, info
    res = resolver(files, main)
    ext = os.path.splitext(main)[1].lower()
    if ext == '.glb' or data[:4] == b'glTF':
        parts, mats, info = glb.load(data, res)
    elif ext == '.gltf':
        parts, mats, info = glb.load_gltf(data, res)
    else:
        parts, mats, info = objfile.load(data, res)
    info['file'] = posixpath.basename(main.replace('\\', '/'))
    info['format'] = ext.lstrip('.')
    parts, mats = bake_colours(parts, mats)
    info['materials'] = len(mats)
    return parts, mats, info


def load_upload(name, data):
    return load_files(unpack(name, data))


def load_path(path):
    """From disk (command line): a model file (the files it refers to are read from around it), a folder, or a .zip."""
    if os.path.isdir(path):
        files = {}
        for r, ds, fs in os.walk(path):
            ds[:] = [d for d in ds if not d.startswith('.')]
            for f in fs:
                p = os.path.join(r, f)
                if os.path.splitext(f)[1].lower() in ACCEPT.split(',') and os.path.getsize(p) < MAX_TOTAL:
                    files[os.path.relpath(p, path)] = open(p, 'rb').read()
        return load_files(files)
    if os.path.splitext(path)[1].lower() in blender.EXTS:
        parts, mats, info = glb.load(blender.convert_path(os.path.abspath(path), _blender()))
        info['file'], info['format'], info['via'] = os.path.basename(path), os.path.splitext(path)[1].lstrip('.').lower(), 'Blender'
        parts, mats = bake_colours(parts, mats)
        info['materials'] = len(mats)
        return parts, mats, info
    data = open(path, 'rb').read()
    if path.lower().endswith('.zip') or data[:4] == b'PK\x03\x04':
        return load_files(unpack(os.path.basename(path), data))
    base = os.path.dirname(os.path.abspath(path))
    names = {}

    def index():                      # only when a path doesn't match: look for the file name nearby
        if not names:
            names['?'] = None
            for r, ds, fs in os.walk(base):
                if r[len(base):].count(os.sep) >= 3:
                    ds[:] = []
                for f in fs:
                    names.setdefault(f.lower(), os.path.join(r, f))
        return names

    def from_disk(uri):
        u = uri.replace('\\', '/')
        for cand in (os.path.join(base, *u.split('/')), u):
            if os.path.isfile(cand):
                return open(cand, 'rb').read()
        p = index().get(posixpath.basename(u).lower())
        return open(p, 'rb').read() if p else None
    ext = os.path.splitext(path)[1].lower()
    if ext == '.glb' or data[:4] == b'glTF':
        parts, mats, info = glb.load(data, from_disk)
    elif ext == '.gltf':
        parts, mats, info = glb.load_gltf(data, from_disk)
    elif ext == '.obj':
        parts, mats, info = objfile.load(data, from_disk)
    else:
        raise GlbError('use a .glb, .gltf, .obj or .zip file')
    info['file'], info['format'] = os.path.basename(path), ext.lstrip('.')
    parts, mats = bake_colours(parts, mats)
    info['materials'] = len(mats)
    return parts, mats, info
