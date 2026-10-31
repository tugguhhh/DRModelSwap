"""Write a simple .glb: named nodes, each with one or more triangle meshes, base-colour textures.

Used by "Export the original" (a reference model to build on in Blender: same size, same place, every moving part a
separate, named object) and by the tests.
"""
import io, json, struct
import numpy as np


def write(path_or_none, nodes, materials):
    """nodes: [dict(name, prims=[(P n x 3, N n x 3 or None, UV n x 2, T m x 3, material index)], translation=None)]
    materials: [dict(name, image=PIL or None, colour=[r, g, b])]. Positions in metres (Y up). -> bytes"""
    blob = bytearray()
    views, accs = [], []

    def add(data, target=None):
        while len(blob) % 4:
            blob.append(0)
        v = dict(buffer=0, byteOffset=len(blob), byteLength=len(data))
        if target:
            v['target'] = target
        blob.extend(data)
        views.append(v)
        return len(views) - 1

    def acc(arr, ctype, typ, target, minmax=False):
        a = dict(bufferView=add(arr.tobytes(), target), componentType=ctype, count=int(len(arr) if arr.ndim > 1 else arr.size), type=typ)
        if minmax:
            a['min'] = arr.min(0).tolist()
            a['max'] = arr.max(0).tolist()
        accs.append(a)
        return len(accs) - 1

    meshes, gnodes = [], []
    for nd in nodes:
        prims = []
        for P, N, UV, T, mi in nd['prims']:
            if len(T) == 0:
                continue
            P = np.asarray(P, np.float32)
            at = dict(POSITION=acc(P, 5126, 'VEC3', 34962, True))
            if N is not None:
                at['NORMAL'] = acc(np.asarray(N, np.float32), 5126, 'VEC3', 34962)
            if UV is not None:
                at['TEXCOORD_0'] = acc(np.asarray(UV, np.float32), 5126, 'VEC2', 34962)
            prims.append(dict(attributes=at, indices=acc(np.asarray(T, np.uint32).reshape(-1), 5125, 'SCALAR', 34963), material=int(mi)))
        g = dict(name=nd['name'])
        if prims:
            meshes.append(dict(name=nd['name'], primitives=prims))
            g['mesh'] = len(meshes) - 1
        if nd.get('translation') is not None:
            g['translation'] = [float(x) for x in nd['translation']]
        gnodes.append(g)
    images, textures, mats = [], [], []
    for m in materials:
        pbr = dict(baseColorFactor=list(m.get('colour', [1, 1, 1])) + [1.0], metallicFactor=0.0, roughnessFactor=0.8)
        if m.get('image') is not None:
            b = io.BytesIO()
            m['image'].convert('RGB').save(b, 'PNG')
            images.append(dict(bufferView=add(b.getvalue()), mimeType='image/png'))
            textures.append(dict(source=len(images) - 1))
            pbr['baseColorTexture'] = dict(index=len(textures) - 1)
        mats.append(dict(name=m.get('name', ''), pbrMetallicRoughness=pbr))
    js = dict(asset=dict(version='2.0', generator='DR Model Swap'), scene=0, scenes=[dict(nodes=list(range(len(gnodes))))],
              nodes=gnodes, meshes=meshes, materials=mats, accessors=accs, bufferViews=views, buffers=[dict(byteLength=len(blob))])
    if images:
        js['images'], js['textures'] = images, textures
    j = json.dumps(js).encode()
    j += b' ' * ((-len(j)) % 4)
    while len(blob) % 4:
        blob.append(0)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(j) + 8 + len(blob)) + struct.pack('<II', len(j), 0x4E4F534A) + j + \
        struct.pack('<II', len(blob), 0x004E4942) + bytes(blob)
    if path_or_none:
        with open(path_or_none, 'wb') as f:
            f.write(out)
    return out
