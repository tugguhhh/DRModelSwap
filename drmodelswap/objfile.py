"""Wavefront .obj reader (with its .mtl materials and textures): triangle meshes in the same form as glb.load.

.obj has no units and no bones: it's read as it is (Y up, like Blender's default .obj export); the tool fits the size
and does the rigging itself. Polygons are split into triangles (fans), negative (relative) indices are understood,
UVs are flipped to glTF's top-left origin. Materials: diffuse colour (Kd) and texture (map_Kd)."""
import io
import numpy as np
from PIL import Image

from .glb import GlbError

# map_Kd options and how many values each takes (the file name is what's left)
_OPT = {'-blendu': 1, '-blendv': 1, '-boost': 1, '-mm': 2, '-o': 3, '-s': 3, '-t': 3, '-texres': 1, '-clamp': 1,
        '-bm': 1, '-imfchan': 1, '-type': 1, '-cc': 1}


def _tex_name(rest):
    tok = rest.split()
    i = 0
    while i < len(tok) and tok[i].lower() in _OPT:
        n = _OPT[tok[i].lower()]
        i += 1
        k = 0
        while k < n and i < len(tok):            # -o / -s / -t take 1 to 3 numbers
            try:
                float(tok[i])
            except ValueError:
                if tok[i - 1].lower() not in ('-blendu', '-blendv', '-clamp', '-cc', '-imfchan', '-type'):
                    break
            i += 1
            k += 1
    return ' '.join(tok[i:]).strip().strip('"')


def _mtl(text, resolve, missing):
    mats, cur = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, _, rest = line.partition(' ')
        key = key.lower()
        rest = rest.strip()
        if key == 'newmtl':
            cur = dict(name=rest, image=None, colour=[0.8, 0.8, 0.8])
            mats[rest] = cur
        elif cur is None:
            continue
        elif key == 'kd':
            try:
                cur['colour'] = [float(x) for x in rest.split()[:3]]
            except ValueError:
                pass
        elif key == 'map_kd' and cur['image'] is None:
            name = _tex_name(rest)
            data = resolve(name) if (resolve and name) else None
            if data is None:
                missing.append(name)
                continue
            try:
                cur['image'] = Image.open(io.BytesIO(data)).convert('RGB')
            except Exception:  # noqa: BLE001
                missing.append(name)
    return mats


def load(data, resolve=None):
    """data: the .obj file; resolve(relative path) -> bytes or None (its .mtl and textures). -> (parts, materials, info)"""
    text = data.decode('utf-8', 'replace') if isinstance(data, bytes) else data
    V, VT, VN, VC = [], [], [], []
    groups = {}            # (object, group, material) -> list of faces [(v, vt, vn) ...]
    order = []
    obj, grp, mtl = '', '', ''
    libs = []
    pending = ''
    for raw in text.splitlines():
        line = pending + raw.strip()
        pending = ''
        if line.endswith('\\'):
            pending = line[:-1] + ' '
            continue
        if not line or line[0] == '#':
            continue
        tok = line.split()
        k = tok[0]
        try:
            if k == 'v':
                V.append([float(t) for t in tok[1:4]])
                VC.append([float(t) for t in tok[4:7]] if len(tok) >= 7 else None)   # "v x y z r g b": vertex colours
            elif k == 'vt':
                VT.append([float(tok[1]), float(tok[2]) if len(tok) > 2 else 0.0])
            elif k == 'vn':
                VN.append([float(t) for t in tok[1:4]])
            elif k == 'f':
                face = []
                for t in tok[1:]:
                    a = t.split('/')
                    vi = int(a[0])
                    vti = int(a[1]) if len(a) > 1 and a[1] else 0
                    vni = int(a[2]) if len(a) > 2 and a[2] else 0
                    # 1-based; negative = counted back from the last one read so far
                    face.append((vi - 1 if vi > 0 else len(V) + vi,
                                 (vti - 1 if vti > 0 else len(VT) + vti) if vti else -1,
                                 (vni - 1 if vni > 0 else len(VN) + vni) if vni else -1))
                if len(face) >= 3:
                    key = (obj, grp, mtl)
                    if key not in groups:
                        groups[key] = []
                        order.append(key)
                    groups[key].append(face)
            elif k == 'o':
                obj = ' '.join(tok[1:])
            elif k == 'g':
                grp = ' '.join(tok[1:])
            elif k == 'usemtl':
                mtl = ' '.join(tok[1:])
            elif k == 'mtllib':
                libs.append(line[len('mtllib'):].strip())
        except (ValueError, IndexError):
            continue                    # a broken line: skip it, like other readers do
    if not V:
        raise GlbError('no vertices in this .obj file')
    V = np.array(V, np.float64)
    VT = np.array(VT, np.float64) if VT else np.zeros((0, 2))
    VN = np.array(VN, np.float64) if VN else np.zeros((0, 3))
    missing = []
    lib = {}
    for name in libs:
        d = resolve(name) if resolve else None
        found = [(name, d)] if d is not None else []
        if not found and ' ' in name:                   # "mtllib a.mtl b.mtl": several files
            found = [(n2, resolve(n2) if resolve else None) for n2 in name.split()]
            found = [(n2, d2) for n2, d2 in found if d2 is not None]
        if not found:
            missing.append(name)
        for _, d2 in found:
            lib.update(_mtl(d2.decode('utf-8', 'replace'), resolve, missing))
    mats, mindex = [], {}
    parts = []
    for key in order:
        o, g, m = key
        if m not in mindex:
            mindex[m] = len(mats)
            mats.append(dict(lib.get(m) or dict(name=m or 'default', image=None, colour=[1.0, 1.0, 1.0])))
        faces = groups[key]
        tri = []
        for f in faces:
            for i in range(1, len(f) - 1):
                tri.append((f[0], f[i], f[i + 1]))
        corners = np.array(tri, np.int64).reshape(-1, 3)          # (v, vt, vn) per corner
        ok = (corners[:, 0] >= 0) & (corners[:, 0] < len(V))
        ok = ok.reshape(-1, 3).all(1).repeat(3)
        corners = corners[ok]
        if not len(corners):
            continue
        uniq, inv = np.unique(corners, axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        P = V[uniq[:, 0]]
        has_uv = (uniq[:, 1] >= 0) & (uniq[:, 1] < len(VT))
        UV = np.zeros((len(uniq), 2))
        UV[has_uv] = VT[uniq[has_uv, 1]]
        UV[:, 1] = 1.0 - UV[:, 1]                                  # .obj: bottom-left origin; glTF: top-left
        has_n = (uniq[:, 2] >= 0) & (uniq[:, 2] < len(VN))
        N = VN[uniq[:, 2]] if has_n.all() else None
        T = inv.reshape(-1, 3)
        inf = dict(name='/'.join(x for x in (o, g) if x), mesh=g or o, material=m)
        cols = [VC[i] for i in uniq[:, 0]]
        if all(c is not None for c in cols):
            C = np.array(cols, float)
            inf['colors'] = np.clip(C / (255.0 if C.max() > 1.0 else 1.0), 0, 1)
        parts.append((mindex[m], P, N, UV, T, inf))
    if not parts:
        raise GlbError('no faces in this .obj file')
    info = dict(meshes=len(parts), materials=len(mats), vertices=sum(len(p[1]) for p in parts),
                triangles=sum(len(p[4]) for p in parts), skinned=False, animations=0, missing=sorted(set(missing)))
    return parts, mats, info
