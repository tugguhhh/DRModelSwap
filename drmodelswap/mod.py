"""Dead Rising rModel files ("MOD\\0", version 0x170). See docs/FORMAT.md.

Only the layout used by objects and vehicles is written: vertex buffer 1 = 28 bytes per vertex,
vertex buffer 2 = 12 bytes, one triangle strip per piece.
"""
import struct
import numpy as np

HDR = struct.Struct('<4sHHHHIIIIIQQQQQQQ')  # magic, ver, bones, pieces, materials, verts, indices, polys, vb1 size, vb2 size,
                                            # textures, @bones, @texture names, @pieces, @vb1, @vb2, @indices
PIECE = 0x50
MATERIAL = 0xD0
STRIDE1, STRIDE2 = 28, 12


class Model:
    def __init__(self, data):
        d = self.data = bytes(data)
        if d[:4] != b'MOD\0':
            raise ValueError('not a MOD file')
        (_, self.version, self.n_bones, self.n_pieces, self.n_materials, self.n_verts, self.n_indices, self.n_polys,
         self.vb1_size, self.vb2_size, self.n_textures, self.o_bones, self.o_tex, self.o_pieces, self.o_vb1, self.o_vb2,
         self.o_idx) = HDR.unpack_from(d, 0)
        self.o_mats = self.o_tex + self.n_textures * 64
        self.box_min = np.array(struct.unpack_from('<3f', d, 0x70))
        self.box_max = np.array(struct.unpack_from('<3f', d, 0x80))
        self.textures = [d[self.o_tex + i * 64:self.o_tex + i * 64 + 64].split(b'\0')[0].decode('latin1') for i in range(self.n_textures)]
        self.pieces = []
        for i in range(self.n_pieces):
            o = self.o_pieces + i * PIECE
            group, mat = struct.unpack_from('<HH', d, o)
            vc, vs, _, _, ist, ic = struct.unpack_from('<6I', d, o + 0xC)
            self.pieces.append(dict(group=group, material=mat, flags=d[o + 4:o + 8], strides=d[o + 8:o + 12],
                                    verts=vc, first_vert=vs, first_index=ist, indices=ic))

    # --- what the tool can rewrite
    def supported(self):
        return bool(self.pieces) and all(p['strides'][0] == STRIDE1 and p['strides'][1] == STRIDE2 for p in self.pieces) \
            and self.n_bones >= 1 and self.vb1_size == self.n_verts * STRIDE1

    def material_textures(self, i):
        """1-based texture numbers of material i: (colour map, specular mask). 0 = none."""
        o = self.o_mats + i * MATERIAL
        return struct.unpack_from('<I', self.data, o + 0x20)[0], struct.unpack_from('<I', self.data, o + 0x30)[0]

    def material_normal_map(self, i):
        """1-based texture number of material i's normal map (+0x28), 0 = none."""
        return struct.unpack_from('<I', self.data, self.o_mats + i * MATERIAL + 0x28)[0]

    def template_material(self):
        """The material new pieces copy their shader settings from: the textured material that covers the most of the
        model (its main surface). Small special parts can use glowing / see-through shaders (the Beam Sword's emitter),
        so the first material is not a safe pick."""
        use = {}
        for p in self.pieces:
            use[p['material']] = use.get(p['material'], 0) + p['indices']
        textured = [m for m in use if self.material_textures(m)[0]]
        if textured:
            return max(textured, key=lambda m: (use[m], -m))
        return min(use) if use else 0

    # --- geometry
    def vertices(self):
        raw = np.frombuffer(self.data, np.uint8, count=self.n_verts * STRIDE1, offset=self.o_vb1).reshape(-1, STRIDE1)
        q = raw[:, 0:6].copy().view('<u2').astype(np.float64)
        pos = self.box_min + q / 32767.0 * (self.box_max - self.box_min)
        nrm = raw[:, 16:22].copy().view('<i2').astype(np.float64) / 32767.0
        uv = raw[:, 24:28].copy().view('<i2').astype(np.float64) / 32768.0
        return pos, nrm, uv

    def indices(self):
        n = min(self.n_indices, (len(self.data) - self.o_idx) // 2)  # the game's own files are sometimes 1 index short
        return np.frombuffer(self.data, '<u2', count=n, offset=self.o_idx)

    def triangles(self, piece, idx=None):
        """Triangles of one piece from its strip; even triangles keep their order, odd ones are flipped."""
        ix = (self.indices() if idx is None else idx)[piece['first_index']:piece['first_index'] + piece['indices']]
        out = []
        for k in range(len(ix) - 2):
            a, b, c = int(ix[k]), int(ix[k + 1]), int(ix[k + 2])
            if a == b or b == c or a == c:
                continue
            out.append((a, b, c) if k % 2 == 0 else (b, a, c))
        return out


def strip(tris):
    """Triangles -> one strip (degenerate joins). Every triangle starts at an even position, so it keeps its winding."""
    s = []
    for a, b, c in tris:
        if s:
            s += [s[-1], a]
            if len(s) % 2:
                s.append(a)
        s += [a, b, c]
    return s


def quantise_weights(W):
    """n x 4 weights (any scale) -> u8 weights that add up to exactly 255 (largest first keeps the rounding)."""
    W = np.clip(np.asarray(W, np.float64), 0, None)
    s = W.sum(1, keepdims=True)
    W = np.where(s > 1e-12, W / np.maximum(s, 1e-12), np.c_[np.ones(len(W)), np.zeros((len(W), W.shape[1] - 1))])
    q = np.floor(W * 255 + 1e-9).astype(np.int64)
    short = 255 - q.sum(1)
    frac = W * 255 - q
    order = np.argsort(-frac, axis=1)
    for k in range(W.shape[1]):
        add = short > 0
        q[np.arange(len(q))[add], order[add, k]] += 1
        short = short - add
    return q.astype(np.uint8)


def _q16(v):
    return np.clip(np.rint(v), -32767, 32767).astype('<i2')


class TooManyVertices(ValueError):
    pass


def build(original, materials, box_min, box_max, max_piece_verts=6000):
    """Write a new MOD that keeps the original's header settings, bones, and a material/piece template.

    materials: list of dicts {colour, normal (0 = none), mask: 1-based texture numbers, meshes: [(P, N, UV, T)]}
               with positions inside box_min..box_max, unit normals, UVs in [0,1), triangles (n,3).
    texture names are passed in original['texture_names'] (list of str).
    """
    orig = original['model']
    d = orig.data
    tmat = orig.template_material()
    mat_t = d[orig.o_mats + tmat * MATERIAL: orig.o_mats + (tmat + 1) * MATERIAL]
    pidx = next((i for i, p in enumerate(orig.pieces) if p['material'] == tmat and p['flags'][2] == 0),
                next(i for i, p in enumerate(orig.pieces) if p['material'] == tmat))  # a plain one (byte 2 = draw category: glass etc.)
    piece_t = d[orig.o_pieces + pidx * PIECE: orig.o_pieces + (pidx + 1) * PIECE]

    vb1, vb2, idx, pieces, out_mats = bytearray(), bytearray(), [], [], []
    n_verts = polys = 0
    span = np.maximum(box_max - box_min, 1e-6)
    for slot, m in enumerate(materials):
        mb = bytearray(mat_t)
        struct.pack_into('<I', mb, 0x20, m['colour'])
        struct.pack_into('<I', mb, 0x28, m.get('normal', 0))   # normal map slot (must not point at our other textures)
        struct.pack_into('<I', mb, 0x30, m['mask'])
        out_mats.append(bytes(mb))
        for mesh in m['meshes']:
            if isinstance(mesh, dict):
                P, N, UV, T = mesh['P'], mesh['N'], mesh['UV'], mesh['T']
                J, Wt = mesh.get('J'), mesh.get('W')
                groups, lod = list(mesh.get('groups') or [0]), mesh.get('lod')
            else:
                (P, N, UV, T), J, Wt, groups, lod = mesh, None, None, [0], None
            if J is None:
                J, Wt = np.zeros((len(P), 4), np.int64), np.c_[np.ones(len(P)), np.zeros((len(P), 3))]
            wq = quantise_weights(Wt)
            if len(T) == 0:
                continue
            # tangents from the UV directions
            e1, e2 = P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]
            d1, d2 = UV[T[:, 1]] - UV[T[:, 0]], UV[T[:, 2]] - UV[T[:, 0]]
            r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
            r = np.where(np.abs(r) < 1e-12, 1e-12, r)
            tf = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
            tg = np.zeros_like(P)
            for k in range(3):
                np.add.at(tg, T[:, k], tf)
            tg -= N * (tg * N).sum(1, keepdims=True)
            alt = np.cross(N, np.where(np.abs(N[:, 1:2]) < 0.9, [[0.0, 1.0, 0.0]], [[1.0, 0.0, 0.0]]))
            ln = np.linalg.norm(tg, axis=1, keepdims=True)
            tg = np.where(ln > 1e-6, tg / np.maximum(ln, 1e-9), alt / np.maximum(np.linalg.norm(alt, axis=1, keepdims=True), 1e-9))
            if len(P) > max_piece_verts:
                # split into pieces of neighbouring triangles: a vertex shared by two pieces is stored twice, and a
                # mesh in scattered order (after thinning) would otherwise store most of its vertices several times
                T = T[np.argsort(T.min(1), kind='stable')]
            start = 0
            while start < len(T):
                used, tris = {}, []
                while start < len(T) and len(used) < max_piece_verts:
                    t = T[start]
                    for v in t:
                        v = int(v)
                        if v not in used:
                            used[v] = len(used)
                    tris.append(t)
                    start += 1
                if n_verts + len(used) > 65535:
                    raise TooManyVertices('more than 65535 vertices: lower the vertex budget')
                inv = np.array(sorted(used, key=used.get))
                vs = n_verts
                n_verts += len(inv)
                v1 = np.zeros((len(inv), STRIDE1), np.uint8)
                pq = np.clip(np.rint((P[inv] - box_min) / span * 32767.0), 0, 32767).astype('<u2')
                v1[:, 0:6] = pq.view(np.uint8).reshape(-1, 6)
                v1[:, 8:12] = np.clip(J[inv], 0, 255).astype(np.uint8)   # bone numbers (index into the bone list)
                v1[:, 12:16] = wq[inv]                                   # weights, 255 = 1.0
                v1[:, 16:22] = _q16(N[inv] * 32767).view(np.uint8).reshape(-1, 6)
                uvq = np.clip(np.rint(UV[inv] * 32768), 0, 32767).astype('<i2').view(np.uint8).reshape(-1, 4)
                v1[:, 24:28] = uvq
                v2 = np.zeros((len(inv), STRIDE2), np.uint8)
                v2[:, 0:6] = _q16(tg[inv] * 32767).view(np.uint8).reshape(-1, 6)
                v2[:, 8:12] = uvq
                vb1 += v1.tobytes()
                vb2 += v2.tobytes()
                st = strip([(used[int(a)] + vs, used[int(b)] + vs, used[int(c)] + vs) for a, b, c in tris])
                if idx:
                    idx += [idx[-1], st[0]]
                    if len(idx) % 2:
                        idx.append(st[0])
                ist = len(idx)
                idx += st
                polys += len(tris)
                nw = int(max(1, (wq[inv] > 0).sum(1).max()))
                for g in groups:  # one draw entry per display group; they share the vertices and indices
                    pb = bytearray(piece_t)
                    struct.pack_into('<HH', pb, 0, int(g), slot)
                    if lod is not None:
                        pb[5] = int(lod) & 0xFF                      # LOD mask (1/2/4 = near/middle/far, 0x0F = all)
                    pb[7] = nw                                       # most weights any vertex of this piece uses (1-4)
                    struct.pack_into('<6I', pb, 0xC, len(inv), vs, 0, 0, ist, len(st))
                    struct.pack_into('<3f', pb, 0x30, *P[inv].min(0))
                    struct.pack_into('<3f', pb, 0x40, *P[inv].max(0))
                    pieces.append(bytes(pb))
    names = original['texture_names']
    hdr = bytearray(d[:orig.o_bones])
    bones = original.get('bones') or d[orig.o_bones:orig.o_tex]   # (wheel assemblies may have been moved: rig.moved_bones)
    texb = b''.join(n.encode('latin1') + b'\0' * (64 - len(n.encode('latin1'))) for n in names)
    o_tex = orig.o_tex
    o_pieces = o_tex + len(texb) + MATERIAL * len(out_mats)
    o_vb1 = o_pieces + PIECE * len(pieces)
    o_vb2 = o_vb1 + len(vb1)
    o_idx = o_vb2 + len(vb2)
    struct.pack_into('<HH', hdr, 8, len(pieces), len(out_mats))
    struct.pack_into('<5I', hdr, 0xC, n_verts, len(idx), polys, len(vb1), len(vb2))
    struct.pack_into('<Q', hdr, 0x20, len(names))
    struct.pack_into('<5Q', hdr, 0x30, o_tex, o_pieces, o_vb1, o_vb2, o_idx)
    struct.pack_into('<3f', hdr, 0x70, *box_min)
    struct.pack_into('<3f', hdr, 0x80, *box_max)
    centre = np.array(struct.unpack_from('<3f', d, 0x60))
    corners = np.array([[x, y, z] for x in (box_min[0], box_max[0]) for y in (box_min[1], box_max[1]) for z in (box_min[2], box_max[2])])
    struct.pack_into('<f', hdr, 0x6C, float(np.linalg.norm(corners - centre, axis=1).max()))  # bounding sphere radius
    return bytes(hdr) + bones + texb + b''.join(out_mats) + b''.join(pieces) + bytes(vb1) + bytes(vb2) + np.array(idx, '<u2').tobytes()
