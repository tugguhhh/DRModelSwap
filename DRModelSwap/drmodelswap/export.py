"""Export a game model as a .glb to build on in Blender: same size and place as in the game, every moving part a separate,
named object (wheels, doors, steering wheel...). For your own use as a reference; don't share game assets."""
import numpy as np

from . import glbwrite, rig

ROLE_NAMES = {'steering_wheel': 'Steering wheel', 'hood': 'Hood', 'trunk': 'Trunk', 'handlebar': 'Handlebar', 'fork': 'Fork',
              'swingarm': 'Swingarm', 'crank': 'Crank'}


def bone_names(path, model):
    names = {0: 'Body'}
    if rig.is_character(model):
        h = ['Hips', 'Right thigh', 'Right shin', 'Right foot', 'Left thigh', 'Left shin', 'Left foot', 'Spine', 'Chest', 'Neck', 'Head',
             'Right shoulder', 'Right upper arm', 'Right forearm', 'Right hand', 'Left shoulder', 'Left upper arm', 'Left forearm',
             'Left hand', 'Jaw', 'Weapon', 'Right eye', 'Left eye']
        return {i: h[i] if i < len(h) else 'Bone %d' % i for i in range(model.n_bones)}
    try:
        vr = rig.VehicleRig(path, model)
    except Exception:  # noqa: BLE001
        return names
    for w in vr.wheels:
        names[w.bone] = 'Wheel %s %s' % ('front' if w.front else 'rear', w.side)
        if w.hub is not None and w.hub > 0 and w.hub not in names:
            names[w.hub] = 'Hub %s %s' % ('front' if w.front else 'rear', w.side)
    s = vr.spec or {}
    for role, nm in ROLE_NAMES.items():
        b = s.get(role)
        if b is not None and b not in names:
            names[b] = nm
    for k, b in enumerate(s.get('doors', [])):
        c = vr.P[vr.J[:, 0] == b]
        c = c.mean(0) if len(c) else vr.bones[b].pivot
        row = '' if len(s['doors']) <= 2 else ('front ' if c[2] > 0 else 'rear ')
        names[b] = 'Door %s%s' % (row, 'left' if c[0] > 0 else 'right')
    for k, b in enumerate(s.get('turret', [])):
        names[b] = 'Turret %d' % (k + 1)
    for k, b in enumerate(s.get('pedals', [])):
        names[b] = 'Pedal %d' % (k + 1)
    return names


def export(path, model, textures, out_path=None):
    """textures: {name: PIL image}. -> glb bytes"""
    P, N, UV = model.vertices()
    J, _ = rig.vertex_weights(model)
    idx = model.indices()
    names = bone_names(path, model)
    lods = sorted({p['flags'][1] for p in model.pieces})
    mats = []
    for i in range(model.n_materials):
        c, _ = model.material_textures(i)
        img = textures.get(model.textures[c - 1]) if 0 < c <= len(model.textures) else None
        mats.append(dict(name='material %d' % i, image=img, colour=[1, 1, 1] if img is not None else [0.6, 0.6, 0.6]))
    by = {}
    for p in model.pieces:
        if p['flags'][1] != lods[0]:
            continue
        t = np.array(model.triangles(p, idx), np.int64).reshape(-1, 3)
        if not len(t):
            continue
        if rig.is_character(model):
            key = 0
            bt = np.zeros(len(t), np.int64)
        else:
            bt = J[t[:, 0], 0]
        for b in np.unique(bt):
            by.setdefault(int(b) if not rig.is_character(model) else 0, []).append((p['material'], t[bt == b]))
    nodes = []
    for b, lst in sorted(by.items()):
        prims = []
        for mi, t in lst:
            used = np.unique(t)
            remap = np.full(len(P), -1)
            remap[used] = np.arange(len(used))
            prims.append((P[used] / 100.0, N[used], UV[used], remap[t], mi))
        nodes.append(dict(name=names.get(b, 'Part %d' % b) if not rig.is_character(model) else 'Body', prims=prims))
    return glbwrite.write(out_path, nodes, mats)
