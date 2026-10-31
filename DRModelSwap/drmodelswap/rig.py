"""What the bones of a game model do: wheels, steering, doors (vehicles) or the shared people skeleton (characters).

All numbers here were read from the game's own models (arcromvehicle.arc and the character archives) and checked by
drawing the vertices each bone carries:

* Bones: 0x18 bytes each (u8 id, u8 parent (255 = none), padding, f32 length, f32 x y z position relative to the parent),
  then a 4x4 local matrix per bone, then a 4x4 inverse bind matrix per bone (row vectors: v' = v @ M, translation in
  the last row). World bind matrix = local @ parent's world. Vertices are stored in model space (bind pose); each
  vertex has 4 bone numbers (index into the bone list) and 4 weights (u8, 255 = 1.0).
* Vehicles: bone 0 = body. A wheel is a bone whose vertices form a disc around the bone's X axis (the axle); it spins
  around that axis. Front wheels hang under a hub bone that turns for steering, rear wheels under a fixed one. In every
  vehicle's bind pose the wheel bones have no rotation, so the axle is the model's X axis.
* Characters: the game's people share one skeleton (21-23 bones: hips, legs, spine, neck, head, arms, weapon hand, eyes),
  in a T-pose with the hips at the origin.
"""
import struct
import numpy as np

BONE = 0x18

# Bone jobs of the game's six vehicles, from their geometry (see module doc). Wheels: (bone, hub bone that steers or
# None). Everything not listed stays with the body.
VEHICLES = {
    'model\\om\\om0009\\om0009': dict(name='2-door car', wheels=[(3, 2), (6, 5), (9, None), (12, None)], steer_hubs=[2, 5],
                                      hubs={3: 2, 6: 5, 9: 8, 12: 11}, steering_wheel=15, doors=[13, 14], hood=16, trunk=17),
    'model\\om\\om0098\\om0098': dict(name='4-door car', wheels=[(4, 3), (7, 6), (10, None), (13, None)], steer_hubs=[3, 6],
                                      hubs={4: 3, 7: 6, 10: 9, 13: 12}, steering_wheel=1, doors=[14, 15, 16, 17], hood=18, trunk=19),
    'model\\om\\om0099\\om0099': dict(name='track', wheels=[(2, 1), (4, 3), (6, None), (8, None)], steer_hubs=[1, 3],
                                      hubs={2: 1, 4: 3, 6: 5, 8: 7}, doors=[9, 10]),
    'model\\om\\om009a\\om009a': dict(name='hammer', wheels=[(3, 2), (6, 5), (15, None), (16, None)], steer_hubs=[2, 5],
                                      hubs={3: 2, 6: 5, 15: 8, 16: 10}, steering_wheel=11, turret=[12, 13, 14]),
    'model\\om\\om009b\\om009b': dict(name='bike', wheels=[(10, 2), (9, None)], handlebar=1, fork=2, swingarm=3,
                                      hubs={10: 2, 9: 3}),
    'model\\om\\om009d\\om009d': dict(name='bicycle', wheels=[(2, 1), (3, None)], handlebar=1, crank=4, pedals=[5, 6],
                                      hubs={2: 1, 3: 0}),
}


def _mat(d, o):
    return np.array(struct.unpack_from('<16f', d, o), np.float64).reshape(4, 4)


class Bone:
    __slots__ = ('index', 'id', 'parent', 'local', 'inv', 'world')

    def __init__(self, index, id_, parent, local, inv):
        self.index, self.id, self.parent, self.local, self.inv = index, id_, parent, local, inv
        self.world = np.linalg.inv(inv)

    @property
    def pivot(self):
        return self.world[3, :3].copy()


def read_bones(model):
    d, n, o = model.data, model.n_bones, model.o_bones
    out = []
    for i in range(n):
        e = d[o + i * BONE:o + (i + 1) * BONE]
        out.append(Bone(i, e[0], e[1] if e[1] != 255 else -1, _mat(d, o + n * BONE + i * 64), _mat(d, o + n * BONE + n * 64 + i * 64)))
    return out


def vertex_weights(model):
    """-> (bone numbers n x 4 int, weights n x 4 float 0..1) of the 28-byte layout."""
    raw = np.frombuffer(model.data, np.uint8, count=model.n_verts * 28, offset=model.o_vb1).reshape(-1, 28)
    return raw[:, 8:12].astype(np.int64), raw[:, 12:16].astype(np.float64) / 255.0


def world_matrices(bones, extra=None):
    """World matrices with extra local rotations {bone index: 3x3} applied before each bone's own local matrix."""
    W = [None] * len(bones)
    for b in bones:  # parents come before children in the game's files
        L = b.local.copy()
        if extra and b.index in extra:
            X = np.asarray(extra[b.index], float)
            if X.shape == (3, 3):
                R = np.eye(4)
                R[:3, :3] = X
            else:
                R = X   # 4x4: a turn around a point other than the bone's own pivot
            L = R @ L
        W[b.index] = L if b.parent < 0 else L @ W[b.parent]
    return W


def skin(P, J, Wt, bones, posed):
    """Model-space positions after skinning with posed world matrices."""
    out = np.zeros_like(P)
    Ph = np.c_[P, np.ones(len(P))]
    for b in bones:
        M = b.inv @ posed[b.index]
        for k in range(J.shape[1]):
            sel = (J[:, k] == b.index) & (Wt[:, k] > 0)
            if sel.any():
                out[sel] += (Ph[sel] @ M)[:, :3] * Wt[sel, k:k + 1]
    return out


def rot_x(deg):
    a = np.radians(deg)
    return np.array([[1, 0, 0], [0, np.cos(a), np.sin(a)], [0, -np.sin(a), np.cos(a)]])


def rot_y(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), 0, -np.sin(a)], [0, 1, 0], [np.sin(a), 0, np.cos(a)]])


def rot_z(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), np.sin(a), 0], [-np.sin(a), np.cos(a), 0], [0, 0, 1]])


class Wheel:
    """One of the game model's wheels: the bone it spins with, where its tyre is, how big it is."""
    __slots__ = ('bone', 'hub', 'steers', 'centre', 'radius', 'width', 'front', 'side')

    def to_dict(self):
        return dict(bone=self.bone, hub=self.hub, steers=self.steers, centre=[round(float(x), 2) for x in self.centre],
                    radius=round(float(self.radius), 2), width=round(float(self.width), 2), front=self.front, side=self.side)


class VehicleRig:
    def __init__(self, path, model):
        self.path = path.lower()
        self.model = model
        self.bones = read_bones(model)
        self.spec = VEHICLES.get(self.path)
        P, _, _ = model.vertices()
        J, Wt = vertex_weights(model)
        self.P, self.J = P, J
        pairs = self.spec['wheels'] if self.spec else self._find_wheels(P, J)
        steer = set(self.spec.get('steer_hubs', []) + ([self.spec['handlebar']] if self.spec and 'handlebar' in self.spec else [])) if self.spec else set()
        self.wheels = []
        for bone, hub in pairs:
            sel = J[:, 0] == bone
            if not sel.any():
                continue
            w = Wheel()
            w.bone, w.hub = bone, hub if hub is not None else self.bones[bone].parent
            w.steers = self._steers(bone, steer)
            piv = self.bones[bone].pivot
            Q = P[sel]
            r = np.hypot(Q[:, 1] - piv[1], Q[:, 2] - piv[2])
            w.radius = float(np.percentile(r, 99.5))
            w.width = float(Q[:, 0].max() - Q[:, 0].min())
            w.centre = np.array([(Q[:, 0].max() + Q[:, 0].min()) / 2, piv[1], piv[2]])
            self.wheels.append(w)
        if self.wheels:
            zs = [w.centre[2] for w in self.wheels]
            zmid = (max(zs) + min(zs)) / 2
            for w in self.wheels:
                w.front = bool(w.centre[2] > zmid)
                w.side = 'left' if w.centre[0] > 1.0 else 'right' if w.centre[0] < -1.0 else 'middle'  # +X = the car's left

    def _steers(self, bone, steer):
        b = self.bones[bone].parent
        while b >= 0:
            if b in steer:
                return True
            b = self.bones[b].parent
        return False

    @staticmethod
    def _find_wheels(P, J):
        """Vehicles the table doesn't know: bones carrying a disc around their X axis, at least 60% of the biggest."""
        cand = []
        for b in np.unique(J[:, 0]):
            Q = P[J[:, 0] == b]
            if len(Q) < 24:
                continue
            e = Q.max(0) - Q.min(0)
            if e[1] > 1 and abs(e[1] - e[2]) < 0.12 * max(e[1], e[2]) and e[0] < 0.75 * e[1]:
                cand.append((int(b), e[1] / 2))
        if not cand:
            return []
        big = max(r for _, r in cand)
        return [(b, None) for b, r in cand if r >= 0.6 * big]

    # ----- bones for named parts of the user's model
    def bone_for(self, role, centre):
        s = self.spec or {}
        if role == 'steering_wheel':
            return s.get('steering_wheel')
        if role in ('hood', 'trunk'):
            return s.get(role)
        if role == 'handlebar':
            return s.get('handlebar')
        if role == 'fork':
            return s.get('fork', s.get('handlebar'))
        if role == 'swingarm':
            return s.get('swingarm')
        if role in ('crank', 'pedal'):
            return s.get('crank')
        if role == 'door':
            doors = s.get('doors') or []
            if not doors:
                return None
            # the door bone whose geometry is nearest (same side, same row)
            best, bd = None, None
            for b in doors:
                Q = self.P[self.J[:, 0] == b]
                c = (Q.max(0) + Q.min(0)) / 2 if len(Q) else self.bones[b].pivot
                dd = float(np.linalg.norm((c - centre) * [1.0, 0.3, 1.0]))
                if bd is None or dd < bd:
                    best, bd = b, dd
            return best
        return None

    def describe(self):
        if not self.wheels:
            return 'no wheels found in the game model'
        out = []
        for w in sorted(self.wheels, key=lambda w: (not w.front, w.side)):
            out.append('%s %s wheel (bone %d%s): radius %.1f, width %.1f' % ('front' if w.front else 'rear', w.side, w.bone,
                                                                              ', steers' if w.steers else '', w.radius, w.width))
        return '; '.join(out)


def is_vehicle(path, model):
    if path.lower() in VEHICLES:
        return True
    return model.n_bones >= 3 and bool(VehicleRig._find_wheels(*_pj(model))) and path.lower().split('\\')[1:2] == ['om']


def _pj(model):
    P, _, _ = model.vertices()
    J, _ = vertex_weights(model)
    return P, J


# ----- characters
HUMAN_IDS = list(range(21))  # hips 0, right leg 1-3, left leg 4-6, spine 7-8, neck 9, head 10, right arm 11-14, left arm 15-18, jaw 19, weapon 20


def is_character(model):
    """The people skeleton: 21+ bones whose first 21 match the game's layout (hips, legs, spine, arms)."""
    if model.n_bones < 21:
        return False
    try:
        bones = read_bones(model)
    except Exception:  # noqa: BLE001
        return False
    want_parent = [-1, 0, 1, 2, 0, 4, 5, 0, 7, 8, 9, 8, 11, 12, 13, 8, 15, 16, 17, 10, 14]
    return all(bones[i].parent == want_parent[i] for i in range(21))


def demo_pose(path, model):
    """A pose that shows whether the moving parts are tied to the right bones: wheels turned a quarter round and
    the front ones steered, doors / hood / trunk opened a little; people: arms down, an elbow and a knee bent, head
    turned. -> ({bone: 3x3}, caption)"""
    if is_character(model):
        return ({12: rot_z(55), 16: rot_z(-55), 13: rot_y(-50), 1: rot_x(-35), 2: rot_x(55), 10: rot_y(30)},
                'moved: arms down, right elbow and left knee bent, head turned')
    try:
        vr = VehicleRig(path, model)
    except Exception:  # noqa: BLE001
        return {}, ''
    if not vr.wheels:
        return {}, ''
    pose = {}
    for w in vr.wheels:
        pose[w.bone] = rot_x(60)
        if w.steers and w.hub is not None and w.hub > 0:
            pose[w.hub] = rot_y(25)
    s = vr.spec or {}
    if 'handlebar' in s:
        pose[s['handlebar']] = rot_y(25)
    for b in s.get('doors', []):
        c = vr.P[vr.J[:, 0] == b]
        pose[b] = rot_y(-40 if (c.mean(0)[0] if len(c) else vr.bones[b].pivot[0]) > 0 else 40)
    if 'hood' in s:
        pose[s['hood']] = rot_x(-25)
    if 'trunk' in s:
        pose[s['trunk']] = rot_x(25)
    return pose, 'moved: wheels turned, front wheels steered, doors / hood / trunk opened'


def moved_bones(model, moves):
    """The model's bone section with whole wheel assemblies moved: moves = {bone: (dx, dy, dz)} for wheel bones; the
    assembly's top bone (the one hanging from the body: suspension arm / hub / handlebar) is moved, so the hub (steering
    pivot) and the wheel (spin axle) move together. -> bytes for the bone section (records, local matrices, inverse binds)."""
    import struct as _st
    bones = read_bones(model)
    n, o, d = model.n_bones, model.o_bones, bytearray(model.data[model.o_bones:model.o_tex])
    locs = [b.local.copy() for b in bones]
    roots = {}
    for b, dv in moves.items():
        r = b
        while bones[r].parent > 0:
            r = bones[r].parent
        if r == 0:
            continue
        roots.setdefault(r, []).append(np.asarray(dv, float))
    W0 = world_matrices(bones)
    for r, ds in roots.items():
        dv = np.mean(ds, 0)
        par = bones[r].parent
        dl = dv @ np.linalg.inv(W0[par][:3, :3]) if par >= 0 else dv
        locs[r][3, :3] += dl
    W = [None] * n
    for b in bones:
        W[b.index] = locs[b.index] if b.parent < 0 else locs[b.index] @ W[b.parent]
    for i in range(n):
        if i in roots:
            p = locs[i][3, :3]
            _st.pack_into('<4f', d, i * BONE + 8, float(np.linalg.norm(p)), *[float(x) for x in p])
        _st.pack_into('<16f', d, n * BONE + i * 64, *[float(x) for x in locs[i].reshape(-1)])
        _st.pack_into('<16f', d, n * BONE + n * 64 + i * 64, *[float(x) for x in np.linalg.inv(W[i]).reshape(-1)])
    return bytes(d)
