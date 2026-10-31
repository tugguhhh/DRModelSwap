"""Reading .gltf and .obj: the same model written several ways (separate files, embedded, absolute texture paths,
negative indices, no normals, a folder, a .zip, several files packed by the page) must read back the same as the .glb.
python tests/formats.py model.glb [more.glb ...]"""
import base64, io, json, os, struct, sys, tempfile, zipfile
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from drmodelswap import glb, models  # noqa: E402


def png(img):
    b = io.BytesIO()
    img.save(b, 'PNG')
    return b.getvalue()


def write_gltf(parts, mats, d, embed=False):
    """parts -> .gltf with positions in 'geo data.bin', the rest in a second buffer (data: URI), textures as files in
    'tex dir/' (spaces, %-encoded), or everything embedded."""
    os.makedirs(os.path.join(d, 'tex dir'), exist_ok=True)
    b0, b1 = bytearray(), bytearray()
    js = dict(asset=dict(version='2.0'), scene=0, scenes=[dict(nodes=[])], nodes=[], meshes=[], accessors=[], bufferViews=[],
              materials=[], textures=[], images=[], samplers=[{}])
    for i, m in enumerate(mats):
        pbr = dict(baseColorFactor=list(m['colour']) + [1.0])
        if m['image'] is not None:
            name = 'tex %d.png' % i
            data = png(m['image'])
            if embed:
                js['images'].append(dict(uri='data:image/png;base64,' + base64.b64encode(data).decode()))
            else:
                open(os.path.join(d, 'tex dir', name), 'wb').write(data)
                js['images'].append(dict(uri='tex%20dir/' + name.replace(' ', '%20')))
            js['textures'].append(dict(source=len(js['images']) - 1, sampler=0))
            pbr['baseColorTexture'] = dict(index=len(js['textures']) - 1)
        js['materials'].append(dict(name=m['name'], pbrMetallicRoughness=pbr))

    def acc(arr, typ, comp, buf):
        b = b0 if buf == 0 else b1
        while len(b) % 4:
            b.append(0)
        off = len(b)
        b += arr.tobytes()
        js['bufferViews'].append(dict(buffer=buf, byteOffset=off, byteLength=arr.nbytes))
        a = dict(bufferView=len(js['bufferViews']) - 1, componentType=comp, count=len(arr), type=typ)
        if typ == 'VEC3' and buf == 0:
            a['min'], a['max'] = arr.min(0).tolist(), arr.max(0).tolist()
        js['accessors'].append(a)
        return len(js['accessors']) - 1
    for k, (mi, P, N, UV, T, info) in enumerate(parts):
        at = dict(POSITION=acc(P.astype(np.float32), 'VEC3', 5126, 0), TEXCOORD_0=acc(UV.astype(np.float32), 'VEC2', 5126, 1))
        if N is not None:
            at['NORMAL'] = acc(N.astype(np.float32), 'VEC3', 5126, 1)
        ind = acc(T.astype(np.uint32).reshape(-1), 'SCALAR', 5125, 1)
        js['meshes'].append(dict(name=info.get('mesh', ''), primitives=[dict(attributes=at, indices=ind, material=mi)]))
        js['nodes'].append(dict(name=info.get('name', 'part%d' % k), mesh=k))
        js['scenes'][0]['nodes'].append(k)
    if embed:
        js['buffers'] = [dict(uri='data:application/octet-stream;base64,' + base64.b64encode(bytes(b0)).decode(), byteLength=len(b0))]
    else:
        open(os.path.join(d, 'geo data.bin'), 'wb').write(bytes(b0))
        js['buffers'] = [dict(uri='geo%20data.bin', byteLength=len(b0))]
    js['buffers'].append(dict(uri='data:application/octet-stream;base64,' + base64.b64encode(bytes(b1)).decode(), byteLength=len(b1)))
    p = os.path.join(d, 'model.gltf')
    open(p, 'w').write(json.dumps(js))
    return p


def write_obj(parts, mats, d, tricky=False):
    """parts -> .obj + .mtl + textures. tricky: negative (relative) indices, no normals, textures referred to by an
    absolute Windows path with options before it, Windows line ends, a material file name with a space."""
    os.makedirs(os.path.join(d, 'textures'), exist_ok=True)
    mtl_name = 'my materials.mtl'
    ml = []
    for i, m in enumerate(mats):
        ml.append('newmtl %s' % (m['name'] or 'mat%d' % i))
        ml.append('Kd %.6f %.6f %.6f' % tuple(m['colour']))
        if m['image'] is not None:
            open(os.path.join(d, 'textures', 'Diffuse %d.png' % i), 'wb').write(png(m['image']))
            ml.append(('map_Kd -s 1 1 1 -o 0 0 0 C:\\Users\\someone\\Desktop\\export\\Diffuse %d.png' if tricky else 'map_Kd textures/Diffuse %d.png') % i)
    open(os.path.join(d, mtl_name), 'w').write('\n'.join(ml) + '\n')
    L = ['# test', 'mtllib ' + mtl_name]
    nv = nt = nn = 0
    for k, (mi, P, N, UV, T, info) in enumerate(parts):
        L.append('o %s' % (info.get('name') or 'part%d' % k).replace(' ', '_'))
        L += ['v %.7f %.7f %.7f' % tuple(p) for p in P]
        L += ['vt %.7f %.7f' % (u, 1 - v) for u, v in UV]
        if N is not None and not tricky:
            L += ['vn %.7f %.7f %.7f' % tuple(n) for n in N]
        L.append('usemtl %s' % (mats[mi]['name'] or 'mat%d' % mi))
        for t in T:
            if tricky:
                L.append('f ' + ' '.join('%d/%d' % (int(i) - len(P), int(i) - len(P)) for i in t))
            elif N is not None:
                L.append('f ' + ' '.join('%d/%d/%d' % (i + 1 + nv, i + 1 + nt, i + 1 + nn) for i in t))
            else:
                L.append('f ' + ' '.join('%d/%d' % (i + 1 + nv, i + 1 + nt) for i in t))
        nv += len(P)
        nt += len(UV)
        nn += len(P) if (N is not None and not tricky) else 0
    p = os.path.join(d, 'model.obj')
    open(p, 'w', newline='\r\n' if tricky else '\n').write('\n'.join(L) + '\n')
    return p


def corners(parts):
    """Every triangle corner (position, UV) and per material: what must survive any re-encoding."""
    out = {}
    for mi, P, N, UV, T, info in parts:
        c = np.c_[P[T.reshape(-1)], UV[T.reshape(-1)]].reshape(-1, 15)
        out.setdefault(mi, []).append(c)
    return {k: np.round(np.concatenate(v), 4) for k, v in out.items()}


def same(a, b, what):
    pa, ma, _ = a
    pb, mb, ib = b
    ca, cb = corners(pa), corners(pb)
    errs = []
    if sorted(ca) != sorted(cb):
        errs.append('materials used %s vs %s' % (sorted(ca), sorted(cb)))
    for k in ca:
        if k in cb:
            x = ca[k][np.lexsort(ca[k].T[::-1])]
            y = cb[k][np.lexsort(cb[k].T[::-1])]
            if x.shape != y.shape or np.abs(x - y).max() > 2e-3:
                errs.append('material %d triangles differ' % k)
    for i, (m1, m2) in enumerate(zip(ma, mb)):
        if (m1['image'] is None) != (m2['image'] is None):
            errs.append('material %d texture %s vs %s' % (i, m1['image'] is not None, m2['image'] is not None))
        elif m1['image'] is not None and not np.array_equal(np.asarray(m1['image']), np.asarray(m2['image'])):
            errs.append('material %d texture pixels differ' % i)
        if np.abs(np.array(m1['colour']) - np.array(m2['colour'])).max() > 1e-4:
            errs.append('material %d colour differs' % i)
    if ib.get('missing'):
        errs.append('missing: %s' % ib['missing'])
    print('  %-44s %s' % (what, 'OK' if not errs else 'PROBLEM: ' + '; '.join(errs)))
    return len(errs) > 0


def pack(files):
    head = json.dumps([dict(name=n, size=len(v)) for n, v in files.items()]).encode()
    return b'DRMSPACK' + struct.pack('<I', len(head)) + head + b''.join(files.values())


def tree(d):
    out = {}
    for r, _, fs in os.walk(d):
        for f in fs:
            out[os.path.relpath(os.path.join(r, f), d).replace(os.sep, '/')] = open(os.path.join(r, f), 'rb').read()
    return out


def zipped(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        for n, v in files.items():
            z.writestr('My Model/' + n, v)
    return b.getvalue()


def edge_cases():
    """Hand-written .obj quirks: quads and an n-gon (split into triangles), relative indices, a group without a
    material, a line continued with a backslash, a missing texture (reported, not an error)."""
    bad = 0
    obj = '\n'.join([
        'mtllib cube.mtl', 'o Cube', 'v -1 -1 -1', 'v 1 -1 -1', 'v 1 1 -1', 'v -1 1 -1', 'v -1 -1 1', 'v 1 -1 1', 'v 1 1 1', 'v -1 1 1',
        'vt 0 0', 'vt 1 0', 'vt 1 1', 'vt 0 1', 'vn 0 0 -1',
        'usemtl red', 'f 1/1/1 4/4/1 3/3/1 2/2/1', 'f -4/1 -3/2 -2/3 \\', '  -1/4',
        'g wheel_FL', 'usemtl nothing_defined', 'f 1 2 6 5', 'f 2 3 7 6 2', 'usemtl tex', 'f 3 4 8 7', 'f 4 1 5 8'])
    mtl = 'newmtl red\nKd 1 0 0\nnewmtl tex\nKd 1 1 1\nmap_Kd -bm 0.5 gone.png\n'
    res = {'cube.mtl': mtl.encode()}
    parts, mats, info = models.load_files(dict(res, **{'cube.obj': obj.encode()}))
    tris = sum(len(p[4]) for p in parts)
    names = [p[5]['name'] for p in parts]
    checks = [(tris == 2 + 2 + 2 + 3 + 2 + 2, 'triangles from quads / n-gon: %d' % tris),
              (mats[0]['colour'] == [1.0, 0.0, 0.0], 'Kd colour'),
              ('Cube/wheel_FL' in names, 'object/group names kept for wheel detection: %s' % names),
              (info['missing'] == ['gone.png'], 'missing texture reported: %s' % info['missing']),
              (all(p[2] is None or len(p[2]) == len(p[1]) for p in parts), 'normals')]
    for ok, what in checks:
        print('  %-44s %s' % (what, 'OK' if ok else 'PROBLEM'))
        bad += not ok
    # vertex colours: two triangles painted red and blue (no texture) -> each samples only its own colour
    obj2 = '\n'.join(['v 0 0 0 1 0 0', 'v 1 0 0 1 0 0', 'v 0 1 0 1 0 0', 'v 1 1 0 0 0 1', 'v 2 1 0 0 0 1', 'v 1 2 0 0 0 1',
                       'f 1 2 3', 'f 4 5 6'])
    parts, mats, info = models.load_files({'vc.obj': obj2.encode()})
    img = np.asarray(mats[parts[0][0]]['image']).astype(int)
    h, w, _ = img.shape
    seen = set()
    for t in parts[0][4]:
        cs = {tuple(img[int(parts[0][3][v, 1] * h), int(parts[0][3][v, 0] * w)]) for v in t}
        seen.add(tuple(sorted(cs)))
    ok = seen == {((0, 0, 255),), ((255, 0, 0),)}
    print('  %-44s %s' % ('vertex colours -> one colour per triangle', 'OK' if ok else 'PROBLEM %s' % seen))
    bad += not ok
    for name, data, want in [('x.gltf', b'{"asset": {"version": "2.0"}, "buffers": [{"uri": "nope.bin", "byteLength": 4}]}', 'nope.bin'),
                             ('x.zip', b'PK\x03\x04garbage', 'can\'t be opened'), ('x.txt', b'hello', 'no model file')]:
        try:
            models.load_upload(name, data)
            print('  %-44s PROBLEM: no error' % name)
            bad += 1
        except glb.GlbError as e:
            ok = want in str(e)
            print('  %-44s %s (%s)' % ('error for ' + name, 'OK' if ok else 'PROBLEM', e))
            bad += not ok
    return bad


def main():
    bad = 0
    print('edge cases')
    bad += edge_cases()
    for src in sys.argv[1:]:
        ref = glb.load(open(src, 'rb').read())
        print(os.path.basename(src), '%d parts, %d materials' % (len(ref[0]), len(ref[1])))
        with tempfile.TemporaryDirectory() as d:
            for kind in ('gltf files', 'gltf embedded', 'obj', 'obj tricky'):
                sub = os.path.join(d, kind.replace(' ', '_'))
                os.makedirs(sub)
                if kind.startswith('gltf'):
                    p = write_gltf(ref[0], ref[1], sub, embed=kind.endswith('embedded'))
                else:
                    p = write_obj(ref[0], ref[1], sub, tricky=kind.endswith('tricky'))
                files = tree(sub)
                bad += same(ref, models.load_path(p), kind + ': file on disk')
                bad += same(ref, models.load_path(sub), kind + ': folder')
                bad += same(ref, models.load_upload('m.zip', zipped(files)), kind + ': .zip')
                bad += same(ref, models.load_upload(os.path.basename(p), pack(files)), kind + ': files picked together')
    print('formats: %d problem(s)' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
