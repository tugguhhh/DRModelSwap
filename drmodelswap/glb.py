"""Minimal glTF 2.0 reader (.glb, or .gltf with its .bin and textures as separate files or embedded): triangle meshes,
node transforms, skins (rest pose), base-colour textures/colours."""
import base64, io, json, struct
from urllib.parse import unquote
import numpy as np
from PIL import Image

_CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
_NORM = {np.int8: 127.0, np.uint8: 255.0, np.int16: 32767.0, np.uint16: 65535.0}


class GlbError(Exception):
    pass


def _parse(data):
    if data[:4] != b'glTF':
        raise GlbError('not a .glb file (binary glTF). Export as "glTF Binary (.glb)" from Blender.')
    o, js, bin_ = 12, None, b''
    while o + 8 <= len(data):
        ln, typ = struct.unpack_from('<II', data, o)
        ch = data[o + 8:o + 8 + ln]
        o += 8 + ln
        if typ == 0x4E4F534A:
            js = json.loads(ch)
        elif typ == 0x004E4942:
            bin_ = ch
    if js is None:
        raise GlbError('the .glb has no JSON chunk')
    return js, bin_


def _accessor(js, bin_, i):
    a = js['accessors'][i]
    n, dt = _NC[a['type']], np.dtype(_CT[a['componentType']])
    cnt = a['count']
    if 'bufferView' not in a:
        out = np.zeros((cnt, n))
    else:
        bv = js['bufferViews'][a['bufferView']]
        off = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        stride = bv.get('byteStride', 0) or n * dt.itemsize
        buf = bin_[bv.get('buffer', 0)] if bv.get('buffer', 0) < len(bin_) else None
        if buf is None or off + stride * (cnt - 1) + n * dt.itemsize > len(buf):
            raise GlbError('the model\'s data file (.bin) is missing or too short: pick it together with the .gltf')
        raw = np.frombuffer(buf, np.uint8, count=stride * (cnt - 1) + n * dt.itemsize, offset=off)
        out = np.lib.stride_tricks.as_strided(raw, shape=(cnt, n * dt.itemsize), strides=(stride, 1)).copy().view(dt).reshape(cnt, n)
        if a.get('normalized'):
            out = out.astype(np.float64) / _NORM[_CT[a['componentType']]]
    if 'sparse' in a:
        raise GlbError('sparse accessors are not supported (apply shape keys / morphs in Blender first)')
    return out.astype(np.float64)


def _node_matrix(nd):
    if 'matrix' in nd:
        return np.array(nd['matrix'], dtype=np.float64).reshape(4, 4).T
    t = nd.get('translation', [0, 0, 0])
    x, y, z, w = nd.get('rotation', [0, 0, 0, 1])
    s = nd.get('scale', [1, 1, 1])
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4)
    M[:3, :3] = R * np.array(s)
    M[:3, 3] = t
    return M


def _image(js, bin_, src, resolve=None, missing=None):
    im = js['images'][src]
    try:
        if 'bufferView' in im:
            bv = js['bufferViews'][im['bufferView']]
            o = bv.get('byteOffset', 0)
            return Image.open(io.BytesIO(bin_[bv.get('buffer', 0)][o:o + bv['byteLength']])).convert('RGB')
        uri = im.get('uri', '')
        if uri.startswith('data:'):
            return Image.open(io.BytesIO(base64.b64decode(uri.split(',', 1)[1]))).convert('RGB')
        if uri:
            data = resolve(unquote(uri)) if resolve else None
            if data is None:
                if missing is not None:
                    missing.append(unquote(uri))
                return None
            return Image.open(io.BytesIO(data)).convert('RGB')
    except Exception:
        return None
    return None


def _buffers(js, chunk, resolve):
    """The model's binary buffers: the .glb's own chunk, embedded data: URIs, or separate files (.bin)."""
    out = []
    for i, b in enumerate(js.get('buffers', [])):
        uri = b.get('uri')
        if uri is None:
            out.append(chunk if i == 0 else b'')
        elif uri.startswith('data:'):
            out.append(base64.b64decode(uri.split(',', 1)[1]))
        else:
            d = resolve(unquote(uri)) if resolve else None
            if d is None:
                raise GlbError('"%s" (the model\'s data) wasn\'t found: pick it together with the .gltf (or drop the whole '
                               'folder / a .zip)' % unquote(uri))
            out.append(d)
    return out or [chunk]


def _tex_ref(mat):
    """The material's colour texture: base colour; failing that the KHR spec-gloss diffuse, or the emissive texture
    (some exports put the look there)."""
    pbr = mat.get('pbrMetallicRoughness', {})
    if 'baseColorTexture' in pbr:
        return pbr['baseColorTexture']
    sg = mat.get('extensions', {}).get('KHR_materials_pbrSpecularGlossiness', {})
    if 'diffuseTexture' in sg:
        return sg['diffuseTexture']
    return mat.get('emissiveTexture')


def load_gltf(data, resolve=None):
    """A .gltf (JSON) file; resolve(relative path) -> bytes or None for the files it refers to."""
    try:
        js = json.loads(data.decode('utf-8-sig') if isinstance(data, bytes) else data)
    except ValueError:
        raise GlbError('this .gltf file isn\'t valid JSON')
    return _load(js, b'', resolve)


def load(data, resolve=None):
    """-> (parts, materials, info). parts: [(material index, P, N or None, UV, T)] in world space (glTF metres, Y up).
    materials: [{name, image (PIL or None), colour [r,g,b]}]."""
    js, chunk = _parse(data)
    return _load(js, chunk, resolve)


def _load(js, chunk, resolve):
    if 'asset' not in js and 'meshes' not in js:
        raise GlbError('this doesn\'t look like a glTF model')
    bin_ = _buffers(js, chunk, resolve)
    missing = []
    used = set(js.get('extensionsRequired', [])) | set(js.get('extensionsUsed', []))
    if used & {'KHR_draco_mesh_compression', 'EXT_meshopt_compression'}:
        raise GlbError('this model uses mesh compression (Draco/meshopt). Re-export it from Blender with compression off.')
    parts = []
    nodes = js.get('nodes', [])
    # global (rest pose) matrices of every node, for skins: a skinned mesh is placed by its joints, not by its node
    glob = [None] * len(nodes)
    parent = {c: i for i, nd in enumerate(nodes) for c in nd.get('children', [])}

    def gmat(i, depth=0):
        if glob[i] is None:
            m = _node_matrix(nodes[i])
            glob[i] = (gmat(parent[i], depth + 1) @ m) if i in parent and depth < 64 else m
        return glob[i]

    def skin_matrix(sk, J, Wt):
        """Per-vertex rest-pose matrix of a skinned primitive: sum of weight x (joint global @ inverse bind)."""
        skin = js['skins'][sk]
        ibm = _accessor(js, bin_, skin['inverseBindMatrices']).reshape(-1, 4, 4).transpose(0, 2, 1) if 'inverseBindMatrices' in skin \
            else np.tile(np.eye(4), (len(skin['joints']), 1, 1))
        jm = np.array([gmat(j) @ ibm[k] for k, j in enumerate(skin['joints'])])
        s = Wt.sum(1, keepdims=True)
        Wn = np.where(s > 1e-9, Wt / np.maximum(s, 1e-9), np.c_[np.ones(len(Wt)), np.zeros((len(Wt), Wt.shape[1] - 1))])
        Ji = np.clip(J.astype(np.int64), 0, len(jm) - 1)
        return np.einsum('nk,nkij->nij', Wn, jm[Ji])

    def walk(ni, M, path, depth=0):
        if depth > 64:
            return
        nd = js['nodes'][ni]
        W = M @ _node_matrix(nd)
        path = path + [nd.get('name', '')]
        if 'mesh' in nd:
            mesh = js['meshes'][nd['mesh']]
            for pr in mesh['primitives']:
                if pr.get('mode', 4) != 4 or 'POSITION' not in pr['attributes']:
                    continue
                at = pr['attributes']
                P = _accessor(js, bin_, at['POSITION'])[:, :3]
                N = _accessor(js, bin_, at['NORMAL'])[:, :3] if 'NORMAL' in at else None
                mi = pr.get('material', -1)
                mat = js['materials'][mi] if 0 <= mi < len(js.get('materials', [])) else {}
                # the UV set the base colour texture uses (often 0; some exporters put it on 1)
                tc = 'TEXCOORD_%d' % _tex_ref(mat).get('texCoord', 0) if _tex_ref(mat) else 'TEXCOORD_0'
                tc = tc if tc in at else 'TEXCOORD_0'
                UV = _accessor(js, bin_, at[tc])[:, :2] if tc in at else np.zeros((len(P), 2))
                # vertex colours (glTF: linear) -> sRGB 0..1; turned into a small texture later (models.bake_colours)
                C = None
                if 'COLOR_0' in at:
                    C = np.clip(_accessor(js, bin_, at['COLOR_0'])[:, :3], 0, 1) ** (1 / 2.2)
                T = _accessor(js, bin_, pr['indices']).astype(np.int64).reshape(-1, 3) if 'indices' in pr else np.arange(len(P) // 3 * 3).reshape(-1, 3)
                if 'skin' in nd and 'JOINTS_0' in at and 'WEIGHTS_0' in at:
                    Ms = skin_matrix(nd['skin'], _accessor(js, bin_, at['JOINTS_0']), _accessor(js, bin_, at['WEIGHTS_0']))
                    P = np.einsum('ni,nij->nj', np.c_[P, np.ones(len(P))], Ms.transpose(0, 2, 1))[:, :3]
                    if N is not None:
                        N = np.einsum('ni,nij->nj', N, np.linalg.inv(Ms[:, :3, :3]))
                    if np.linalg.det(Ms[len(Ms) // 2, :3, :3]) < 0:
                        T = T[:, [0, 2, 1]]
                else:
                    P = (np.c_[P, np.ones(len(P))] @ W.T)[:, :3]
                    if np.linalg.det(W[:3, :3]) < 0:
                        T = T[:, [0, 2, 1]]
                    if N is not None:
                        N = N @ np.linalg.inv(W[:3, :3])
                mname = mat.get('name', '')
                inf = dict(name='/'.join(x for x in path if x), mesh=mesh.get('name', ''), material=mname)
                if C is not None and len(C) == len(P):
                    inf['colors'] = C
                parts.append((mi, P, N, UV, T, inf))
        for c in nd.get('children', []):
            walk(c, W, path, depth + 1)

    scenes = js.get('scenes')
    roots = scenes[js.get('scene', 0)].get('nodes', []) if scenes else list(range(len(js.get('nodes', []))))
    for r in roots:
        walk(r, np.eye(4), [])
    mats = []
    for m in js.get('materials', []):
        pbr = m.get('pbrMetallicRoughness', {})
        img = None
        ref = _tex_ref(m)
        if ref and 0 <= ref.get('index', -1) < len(js.get('textures', [])):
            tx = js['textures'][ref['index']]
            src = tx.get('source')
            if src is None:
                src = tx.get('extensions', {}).get('KHR_texture_webp', {}).get('source')
            if src is not None:
                img = _image(js, bin_, src, resolve, missing)
        f = pbr.get('baseColorFactor', m.get('extensions', {}).get('KHR_materials_pbrSpecularGlossiness', {}).get('diffuseFactor', [1, 1, 1, 1]))
        mats.append(dict(name=m.get('name', ''), image=img, colour=[float(max(0.0, min(1.0, c))) ** (1 / 2.2) for c in list(f)[:3]]))  # linear -> sRGB
    info = dict(meshes=len(js.get('meshes', [])), materials=len(mats), vertices=sum(len(p[1]) for p in parts),
                triangles=sum(len(p[4]) for p in parts), skinned=bool(js.get('skins')), animations=len(js.get('animations', [])),
                missing=sorted(set(missing)))
    return parts, mats, info
