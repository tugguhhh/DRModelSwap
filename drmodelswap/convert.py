"""Turn a .glb into a replacement for one game model: placement, thinning, textures, MOD + TEX files."""
import math
from dataclasses import dataclass, asdict, field, replace
import numpy as np
from PIL import Image

from . import orient, pieces, seats, arc, character, mod, rig, tex, vehicle

TILTS = {'none': np.eye(3),
         'x+90': np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float),   # Z-up model -> Y-up
         'x-90': np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float),
         'z+90': np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float),
         'z-90': np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], float),
         'x180': np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], float)}


@dataclass
class Settings:
    orient: str = 'auto'       # 'auto' = turned automatically (orient.py); 'manual' = by yaw / tilt below
    yaw: int = 0               # turn around the up axis, degrees (front of the game model = +Z)
    tilt: str = 'none'         # extra turn for models that lie down (see TILTS), or 'auto' = long side to the original's long side
    size: str = 'auto'         # 'auto' = by kind (vehicles 'wheels', characters 'height', objects 'long'); 'fit' = inside the original's box, 'footprint' = original length/width (may be taller),
                               # 'long' = longest side as long as the original's longest side, 'real' = .glb metres x100
    scale: float = 1.0         # multiplier on top of that
    place: str = 'ground'      # 'ground' = standing on the original's bottom, 'center' = original's centre, 'origin' = .glb origin kept
    max_texture: int = 1024
    max_vertices: int = 60000
    wheels: str = 'auto'       # vehicles: 'auto' = find the .glb's wheels and make them spin/steer, 'off' = all fixed to the body
    wheel_size: str = 'game'   # 'game' = resize each wheel to the game wheel (it touches the ground right), 'mine' = keep
    wheel_x: str = 'mine'      # 'mine' = wheels stay where they are sideways, 'game' = moved to the game's track width too
    clothes: str = 'default'   # Frank: 'default' = his starting clothes are hidden, 'keep' = drawn on top
    wheel_fit: str = 'axles'   # 'axles' = the wheels move onto the game's axles; 'model' = they stay, the game's wheel
                               #  bones move to them (experimental: depends on how the game places wheels)
    pose: str = 'auto'         # characters: 'auto' = arms/legs straightened to the game's T-pose if needed, 'as_is' = no change
    body: str = 'fit'          # characters: 'fit' = legs / arms fitted onto the game's joints, 'as_is' = only scaled to the height
    seats: str = 'auto'        # vehicles: 'auto' = find the model's seats (riders drawn there by the co-op mod), 'game' = as the original
    seat_x: float = 0.0        # driver seat moved by hand (game units: +X left, +Y up, +Z forward), on top of what was found
    seat_y: float = 0.0
    seat_z: float = 0.0
    pass_x: float = 0.0        # passenger seat moved by hand
    pass_y: float = 0.0
    pass_z: float = 0.0
    roles: dict = field(default_factory=dict)   # Parts list: {piece id: role} chosen by hand (pieces.py)
    rot_x: float = 0.0         # fine turn by hand, degrees, on top of the turn above (game axes: X across, Y up, Z front)
    rot_y: float = 0.0
    rot_z: float = 0.0

    @classmethod
    def from_dict(cls, d):
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, type(getattr(s, k))(v))
        s.orient = s.orient if s.orient in ('auto', 'manual') else 'auto'
        ok_roles = set(pieces.VEHICLE_ROLES) | set(pieces.CHARACTER_ROLES)
        s.roles = {str(k): str(v) for k, v in (s.roles or {}).items() if str(v) in ok_roles and str(v) != 'auto'} if isinstance(s.roles, dict) else {}
        if s.tilt not in TILTS and s.tilt != 'auto':
            s.tilt = 'none'
        if s.size not in ('auto', 'footprint', 'fit', 'long', 'real', 'wheels', 'height'):
            s.size = 'auto'
        s.wheels = s.wheels if s.wheels in ('auto', 'off') else 'auto'
        s.wheel_size = s.wheel_size if s.wheel_size in ('game', 'mine') else 'game'
        s.wheel_x = s.wheel_x if s.wheel_x in ('game', 'mine') else 'mine'
        s.clothes = s.clothes if s.clothes in ('default', 'keep') else 'default'
        s.wheel_fit = s.wheel_fit if s.wheel_fit in ('axles', 'model') else 'axles'
        s.pose = s.pose if s.pose in ('auto', 'as_is') else 'auto'
        s.body = s.body if s.body in ('fit', 'as_is') else 'fit'
        s.seats = s.seats if s.seats in ('auto', 'game') else 'auto'
        for k in ('rot_x', 'rot_y', 'rot_z'):
            setattr(s, k, float(((getattr(s, k) + 180.0) % 360.0) - 180.0))
        for k in ('seat_x', 'seat_y', 'seat_z', 'pass_x', 'pass_y', 'pass_z'):
            setattr(s, k, float(min(400.0, max(-400.0, getattr(s, k)))))
        s.max_vertices = max(1000, min(65000, s.max_vertices))
        s.max_texture = max(64, min(2048, s.max_texture))
        s.scale = max(0.05, min(20.0, s.scale))
        return s

    def to_dict(self):
        return asdict(self)


def _fine(st):
    """The fine turn by hand (column-vector rotation applied after the main turn): X, then Y, then Z."""
    def m(axis, deg):
        a = math.radians(deg)
        c, s_ = math.cos(a), math.sin(a)
        if axis == 0:
            return np.array([[1, 0, 0], [0, c, -s_], [0, s_, c]])
        if axis == 1:
            return np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])
        return np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])
    return m(2, st.rot_z) @ m(1, st.rot_y) @ m(0, st.rot_x)


def _yaw(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])


def _auto_tilt(P, box):
    """The quarter-turn that makes the model's proportions most like the original's (long side to long side)."""
    want = np.log(np.maximum(box, 1e-6) / max(box.max(), 1e-6))
    best, bestR = None, np.eye(3)
    for t in ('none', 'x+90', 'z+90'):
        for y in (0, 90):
            R = _yaw(y) @ TILTS[t]
            Q = P @ R.T
            size = np.maximum(Q.max(0) - Q.min(0), 1e-9)
            score = float(((np.log(size / size.max()) - want) ** 2).sum())
            if best is None or score < best - 1e-9:
                best, bestR = score, R
    return bestR


def _rotation(st, P=None, box=None):
    base = _auto_tilt(P, box) if st.tilt == 'auto' else TILTS[st.tilt]
    return _yaw(st.yaw) @ base


def _area(P, T):
    if len(T) == 0:
        return 0.0
    return float(np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1).sum() / 2)


def cluster(P, N, UV, T, target):
    """Rough thinning: merge the vertices that fall in the same grid cell until at most `target` are left."""
    lo, hi = P.min(0), P.max(0)
    ext = max((hi - lo).max(), 1e-9)
    cells = 512
    while True:
        q = np.floor((P - lo) / (ext / cells)).astype(np.int64)
        key = (q[:, 0] * 1000003 + q[:, 1]) * 1000003 + q[:, 2]
        uk, inv = np.unique(key, return_inverse=True)
        if len(uk) <= target or cells < 8:
            break
        cells = int(cells * 0.95)
    Tn = inv[T]
    keep = (Tn[:, 0] != Tn[:, 1]) & (Tn[:, 1] != Tn[:, 2]) & (Tn[:, 0] != Tn[:, 2])
    Tn = Tn[keep]
    cnt = np.bincount(inv, minlength=len(uk))
    Pn = np.zeros((len(uk), 3))
    Nn = np.zeros((len(uk), 3))
    np.add.at(Pn, inv, P)
    np.add.at(Nn, inv, N)
    first = np.full(len(uk), -1)
    first[inv[::-1]] = np.arange(len(P))[::-1]
    Pn /= np.maximum(cnt, 1)[:, None]
    Nn /= np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-9)
    return Pn, Nn, UV[first], Tn


def _face_normals(P, T):
    fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    acc = np.zeros_like(P)
    for k in range(3):
        np.add.at(acc, T[:, k], fn)
    return acc / np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-9)


def _wrap_uvs(P, N, UV, T):
    """UVs outside 0..1 can't be stored: shift each triangle by whole tiles (vertices shared by triangles that need
    different shifts are duplicated), then clamp."""
    if len(T) == 0:
        return P, N, UV, T
    sh = np.floor(UV[T].min(1))
    if not sh.any():
        return P, N, np.clip(UV, 0, 32767 / 32768), T
    key = T.reshape(-1).astype(np.int64) * 4194304 + ((np.repeat(sh[:, 0], 3) + 1024).astype(np.int64) * 2048) + (np.repeat(sh[:, 1], 3) + 1024).astype(np.int64)
    uk, inv = np.unique(key, return_inverse=True)
    src = (uk // 4194304).astype(np.int64)
    su = (uk // 2048 % 2048 - 1024).astype(np.float64)
    sv = (uk % 2048 - 1024).astype(np.float64)
    UV2 = UV[src] - np.stack([su, sv], 1)
    return P[src], N[src], np.clip(UV2, 0, 32767 / 32768), inv.reshape(-1, 3)


def place(parts, original_model, st):
    """glTF parts -> parts in the game model's space, plus the vertex box to store them in."""
    rawP = np.concatenate([p[1] for p in parts])
    R = _rotation(st, rawP, original_model.box_max - original_model.box_min)
    allP = rawP @ R.T
    lo, hi = allP.min(0), allP.max(0)
    size = np.maximum(hi - lo, 1e-9)
    bmin, bmax = original_model.box_min, original_model.box_max
    box = np.maximum(bmax - bmin, 1e-6)
    if st.size == 'real':
        s = 100.0
    elif st.size == 'fit':
        s = float(np.min(box / size))
    elif st.size == 'long':
        s = float(box.max() / size.max())
    else:  # footprint: length and width
        s = float(np.min((box / size)[[0, 2]]))
    s *= st.scale
    if st.place == 'origin':
        off = np.zeros(3)
        centre = np.zeros(3)
    else:
        centre = (lo + hi) / 2
        off = (bmin + bmax) / 2
        if st.place == 'ground':
            off = off.copy()
            off[1] = bmin[1] + size[1] * s / 2
    out = []
    for mi, P, N, UV, T in parts:
        Pw = (P @ R.T - centre) * s + off
        if N is None or not np.isfinite(N).all():
            Nw = _face_normals(Pw, T)
        else:
            Nw = N @ R.T
            ln = np.linalg.norm(Nw, axis=1, keepdims=True)
            bad = ln[:, 0] < 1e-6
            Nw = Nw / np.maximum(ln, 1e-9)
            if bad.any():
                Nw[bad] = _face_normals(Pw, T)[bad]
        out.append((mi, Pw, Nw, UV.copy(), T.copy()))
    allW = np.concatenate([p[1] for p in out])
    vmin = np.minimum(bmin, allW.min(0) - 1.0)
    vmax = np.maximum(bmax, allW.max(0) + 1.0)
    return out, vmin, vmax, s


def thin(parts, max_vertices, log):
    total = sum(len(p[1]) for p in parts)
    if total <= max_vertices:
        return parts
    light = sum(len(p[1]) for p in parts if len(p[1]) <= 3000)
    heavy = [i for i, p in enumerate(parts) if len(p[1]) > 3000]
    budget_all = max_vertices * 0.85 - light
    if budget_all < 200 * max(1, len(heavy)):
        heavy = list(range(len(parts)))  # many small parts: thin them all
        budget_all = max_vertices * 0.85
    w = {i: (_area(parts[i][1], parts[i][4]) * len(parts[i][1])) ** 0.5 for i in heavy}
    budget = {}
    for _ in range(4):
        free = [i for i in heavy if i not in budget]
        tot = sum(w[i] for i in free) or 1.0
        left = budget_all - sum(budget.values())
        for i in free:
            if left * w[i] / tot >= len(parts[i][1]):
                budget[i] = len(parts[i][1])
    free = [i for i in heavy if i not in budget]
    tot = sum(w[i] for i in free) or 1.0
    left = budget_all - sum(budget.values())
    for i in free:
        budget[i] = max(100, int(left * w[i] / tot))
    log('The model has %d vertices; the game takes at most about 65,000. Thinning the heaviest parts (rough: '
        'decimating in Blender first looks better).' % total)
    out = []
    for i, (mi, P, N, UV, T) in enumerate(parts):
        if i in budget and budget[i] < len(P):
            P2, N2, UV2, T2 = cluster(P, N, UV, T, budget[i])
            log('  part %d: %d -> %d vertices' % (i, len(P), len(P2)))
            out.append((mi, P2, N2, UV2, T2))
        else:
            out.append((mi, P, N, UV, T))
    return out


def _wrap_part(p):
    """_wrap_uvs for a part dict that also carries bone numbers / weights per vertex."""
    P, N, UV, T = p['P'], p['N'], p['UV'], p['T']
    if len(T) == 0:
        return p
    sh = np.floor(UV[T].min(1))
    if not sh.any():
        return dict(p, UV=np.clip(UV, 0, 32767 / 32768))
    key = T.reshape(-1).astype(np.int64) * 4194304 + ((np.repeat(sh[:, 0], 3) + 1024).astype(np.int64) * 2048) + (np.repeat(sh[:, 1], 3) + 1024).astype(np.int64)
    uk, inv = np.unique(key, return_inverse=True)
    src = (uk // 4194304).astype(np.int64)
    su = (uk // 2048 % 2048 - 1024).astype(np.float64)
    sv = (uk % 2048 - 1024).astype(np.float64)
    UV2 = np.clip(UV[src] - np.stack([su, sv], 1), 0, 32767 / 32768)
    return dict(p, P=P[src], N=N[src], UV=UV2, T=inv.reshape(-1, 3), J=p['J'][src], W=p['W'][src])


def _thin_part(p, target):
    """cluster() for a part dict: bone numbers / weights come from the first vertex of each merged cell."""
    P, N, UV, T = p['P'], p['N'], p['UV'], p['T']
    lo = P.min(0)
    ext = max((P.max(0) - lo).max(), 1e-9)
    cells = 512
    while True:
        q = np.floor((P - lo) / (ext / cells)).astype(np.int64)
        key = ((q[:, 0] * 1000003 + q[:, 1]) * 1000003 + q[:, 2]) * 512 + p['J'][:, 0]   # never merge across bones (wheel / body)
        uk, inv = np.unique(key, return_inverse=True)
        if len(uk) <= target or cells < 8:
            break
        cells = int(cells * 0.95)
    inv = inv.reshape(-1)
    Tn = inv[T]
    keep = (Tn[:, 0] != Tn[:, 1]) & (Tn[:, 1] != Tn[:, 2]) & (Tn[:, 0] != Tn[:, 2])
    cnt = np.bincount(inv, minlength=len(uk))
    Pn, Nn = np.zeros((len(uk), 3)), np.zeros((len(uk), 3))
    np.add.at(Pn, inv, P)
    np.add.at(Nn, inv, N)
    first = np.full(len(uk), -1)
    first[inv[::-1]] = np.arange(len(P))[::-1]
    Pn /= np.maximum(cnt, 1)[:, None]
    Nn /= np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-9)
    return dict(p, P=Pn, N=Nn, UV=UV[first], T=Tn[keep], J=p['J'][first], W=p['W'][first])


def _thin_parts(parts, max_vertices, log):
    total = sum(len(p['P']) for p in parts)
    if total <= max_vertices:
        return parts
    log('The model has %d vertices; the game takes at most about 65,000. Thinning it (rough: decimating in Blender first '
        'looks better).' % total)
    out = []
    for p in parts:
        tgt = max(60, int(len(p['P']) * max_vertices * 0.9 / total))
        out.append(_thin_part(p, tgt) if tgt < len(p['P']) else p)
    return out


def _normals(Pw, N, T, R):
    if N is None or not np.isfinite(N).all():
        return _face_normals(Pw, T)
    Nw = N @ R.T
    ln = np.linalg.norm(Nw, axis=1, keepdims=True)
    bad = ln[:, 0] < 1e-6
    Nw = Nw / np.maximum(ln, 1e-9)
    if bad.any():
        Nw[bad] = _face_normals(Pw, T)[bad]
    return Nw


def _vehicle_scale(Q_all, om, log=None):
    """A vehicle the size of the original on the ground: the same area (between its length and its width ratio), but
    not more than 1.3x the original's length or width, and not more than 1.8x its height (a tall cartoon car stays
    tall; only extreme ones are held back so they don't fill the screen)."""
    size = np.maximum(np.ptp(Q_all, 0), 1e-9)
    box = np.maximum(om.box_max - om.box_min, 1e-6)
    r = box / size
    s = float(np.sqrt(r[0] * r[2]))
    s = min(s, 1.3 * float(r[0]), 1.3 * float(r[2]))
    if s > 1.8 * float(r[1]):
        s = 1.8 * float(r[1])
        if log:
            log('Your model is very tall for this vehicle: kept to 1.8x the original\'s height (Scale makes it bigger).')
    return s


def _height_cap(Q_all, om, k=1.15):
    """Largest scale that keeps a vehicle within k x the game vehicle's height (the camera is set for the original)."""
    h = float(np.ptp(Q_all[:, 1]))
    return (k * float(om.box_max[1] - om.box_min[1]) / h) if h > 1e-9 else 1e9


def _box_fit(Q_all, om, st):
    """The original placement rules (size / place settings) -> (s, t) so that game position = s * Q + t."""
    lo, hi = Q_all.min(0), Q_all.max(0)
    size = np.maximum(hi - lo, 1e-9)
    bmin, bmax = om.box_min, om.box_max
    box = np.maximum(bmax - bmin, 1e-6)
    if st.size == 'real':
        s = 100.0
    elif st.size in ('fit', 'height'):
        s = float(np.min(box / size))
    elif st.size == 'long':
        s = float(box.max() / size.max())
    else:  # footprint (also the fallback for 'wheels' when no wheels were found)
        s = float(np.min((box / size)[[0, 2]]))
    s *= st.scale
    return s, _placed(Q_all, om, s, st.place)


def _placed(Q_all, om, s, place):
    """Offset that puts the model scaled by s where the setting says: on the original's ground, centred, or as it is."""
    lo, hi = Q_all.min(0), Q_all.max(0)
    size = np.maximum(hi - lo, 1e-9)
    bmin, bmax = om.box_min, om.box_max
    if place == 'origin':
        return np.zeros(3)
    centre = (lo + hi) / 2
    off = (bmin + bmax) / 2
    if place == 'ground':
        off = off.copy()
        off[1] = bmin[1] + size[1] * s / 2
    return off - centre * s


def _parts_in(parts):
    out = []
    for p in parts:
        mi, P, N, UV, T = p[:5]
        out.append(dict(mi=mi, P=P, N=N, UV=UV, T=np.asarray(T, np.int64), info=p[5] if len(p) > 5 else {}))
    return out


def prepare(model_path, om, parts, st, log, extra=()):
    """-> (placed part dicts with J/W/groups/lod, kind, report). Placement, rigging and LOD copies."""
    kind = 'rigid'
    if rig.is_character(om):
        kind = 'character'
    elif st.wheels != 'off' and rig.is_vehicle(model_path, om):
        kind = 'vehicle'
    ps = pieces.tag(_parts_in(parts))
    rep = dict(kind=kind, removed=[])
    gone = {k for k, v in st.roles.items() if v == 'remove'}
    if gone:
        names = {cid: pieces._name(ps[i]) for i, _, cid in pieces.all_pieces(ps) if cid in gone}
        ps, n = pieces.leave_out(ps, gone)
        rep['removed'] += [dict(id=k, ids=[k], name=names.get(k, 'piece ' + k), found='remove', chosen=True) for k in gone]
        if n:
            log('Left out %d piece(s) (Parts list).' % n)
    if not ps:
        raise ValueError('every piece of the model is left out (Parts list)')
    # a flat base under the model (a ground plane, a display plate, a shadow) is left out, unless set in the Parts list;
    # found before the turn (it would spoil it)
    base = [k for k in pieces.flat_base(ps) if k not in st.roles]
    if base:
        names = {cid: pieces._name(ps[i]) for i, _, cid in pieces.all_pieces(ps) if cid in base}
        rest, n = pieces.leave_out(ps, set(base))
        if n and rest:
            ps = rest
            rep['removed'] += [dict(id=k, ids=[k], name=names.get(k, 'base'), found='remove', chosen=False) for k in base]
            log('Left out a flat base under the model (a ground plane or stand). To keep it, set it to something else in the Parts list.')
    if st.size == 'auto':
        from dataclasses import replace
        st = replace(st, size={'vehicle': 'wheels', 'character': 'height'}.get(kind, 'long'))
    ref = character.Body(om, extra).S[:, :3] if kind == 'character' else None
    if st.orient == 'auto':
        off = np.cumsum([0] + [len(p['P']) for p in ps])
        allT = np.concatenate([p['T'] + o for p, o in zip(ps, off)]) if ps else np.zeros((0, 3), np.int64)
        R, _ = orient.auto(kind, np.concatenate([p['P'] for p in ps]), allT, om, ref=ref,
                           parts=[(p['P'], p['T'], p.get('info')) for p in ps] if kind == 'vehicle' else None)
        R = R @ TILTS['none']
        log('Turned automatically (%s, to match the original\'s shape). If it\'s wrong, pick Turn / Stand it up by hand.' % orient.describe(R))
    else:
        R = _rotation(st, np.concatenate([p['P'] for p in ps]), om.box_max - om.box_min)
    if st.rot_x or st.rot_y or st.rot_z:
        R = _fine(st) @ R
        log('Fine turn: %+.0f° around X, %+.0f° around Y, %+.0f° around Z.' % (st.rot_x, st.rot_y, st.rot_z))
    Q = [p['P'] @ R.T for p in ps]
    Qa = np.concatenate(Q)
    if kind == 'character':
        body = character.Body(om, extra)
        s, t = character.fit(Qa, body, st)
    else:
        s, t = _box_fit(Qa, om, st)
    if kind == 'vehicle':
        vr = rig.VehicleRig(model_path, om)
        comps, wg, src = vehicle.detect(ps, Q, log, roles=st.roles)
        allQ = np.concatenate(Q)
        plan = vehicle.Plan.build(wg, vr, allQ.min(0), allQ.max(0))
        log('Game vehicle: %s. %s' % ((vr.spec or {}).get('name', 'vehicle'), vr.describe()))
        if wg:
            nax = len(plan.user_axles)
            log('Wheels found in your model: %d on %d axle%s (%s)' % (len(wg), nax, '' if nax == 1 else 's', 'by their names or materials'
                if src == 'named' else 'by their shape; name them "wheel" / "tire" in Blender if this is wrong'))
        else:
            log('No wheels found in your model: they will not turn. ' + _wheel_hint(ps, st, len(vr.wheels)))
        if st.size == 'wheels' and st.wheel_fit == 'model' and wg:
            # the body at the game vehicle's size; the wheels' bottoms on the game's ground line
            s, t = _box_fit(Qa, om, Settings(size='footprint', scale=st.scale, place=st.place))
            low = min(g.lo[1] for g in wg)
            ground = float(np.mean([w.centre[1] - w.radius for w in vr.wheels]))
            t[1] = ground - s * low
        elif st.size == 'wheels':
            s0 = _vehicle_scale(Qa, om, log)
            f = plan.fit(s0, log) if wg else None
            if f:
                # the wheelbase only nudges the size (up to 15%): a cartoon car's short wheelbase mustn't shrink or blow it up
                f = min(max(f, 0.85 * s0), 1.15 * s0)
                s = f * st.scale
                t = plan.offset(s)
            else:
                if wg:
                    log('Size "Match the wheels" needs wheels on two axles; sized by length & width instead.')
                s = s0 * st.scale
                t = _placed(Qa, om, s, st.place)
            sz = np.ptp(Qa, 0) * s
            osz = om.box_max - om.box_min
            log('Size: x%.2f of the original\'s length, x%.2f its width, x%.2f its height (Scale changes it)' % tuple(sz[[2, 0, 1]] / osz[[2, 0, 1]]))
    Pw = [q * s + t for q in Q]
    for i, p in enumerate(ps):
        p['N'] = _normals(Pw[i], p['N'], p['T'], R)
    Jl = Wl = None
    if kind == 'vehicle' and wg:
        if st.wheel_x == 'game':
            xs = []
            for g in wg:
                m = g.main
                Qm = Pw[m.part][m.verts]
                xs.append((Qm[:, 0].min() + Qm[:, 0].max()) / 2)
            f = plan.track_stretch(xs)
            if f and abs(f - 1) > 0.01:
                mid = (max(xs) + min(xs)) / 2
                for q in Pw:
                    q[:, 0] = (q[:, 0] - mid) * f + mid
                log('Stretched sideways x%.2f so the wheels are as far apart as the game\'s (body included)' % f)
        Jl, Wl, vrep = vehicle.assign(ps, Pw, comps, plan, vr, dict(wheel_size=st.wheel_size, wheel_fit=st.wheel_fit), log)
        rep['vehicle'] = vrep
        rep['seats'] = _seats(model_path, ps, Pw, comps, vr, st, log)
        if vrep.get('bone_moves'):
            rep['bones'] = rig.moved_bones(om, vrep['bone_moves'])
            far = max(np.linalg.norm(v) for v in vrep['bone_moves'].values())
            log('Your wheels stay where your model has them; the game\'s wheel bones were moved to them (up to %.0f units). '
                'Experimental: if the game places its wheels by its own physics, they will show at the game\'s wheel spots '
                'instead (still spinning true).' % far)
        for w in vrep['wheels']:
            where = 'stays where it is' if vrep.get('bone_moves') else 'moved %.1f onto the axle, size x%.2f' % (w['shift'], w['size'])
            log('  %s %s wheel -> bone%s %s (%s%d piece%s, %s, radius %.1f / game %.1f)' % (
                'front' if w['front'] else 'rear', w['side'], 's' if len(w['bones']) > 1 else '', ' + '.join(str(b) for b in w['bones']),
                'steers, ' if w['steers'] else '', w['pieces'], '' if w['pieces'] == 1 else 's', where, w['radius'], w['game_radius']))
        if vrep['axle_parts']:
            log('  %d part(s) on the axles (rods, hubs) moved along with the wheels' % vrep['axle_parts'])
        if vrep['moved'] > 15:
            log('  Note: a wheel had to move %.0f units to reach the game\'s axle. Parts between it and the body may look off; '
                'compare with "Save the original as .glb" in Blender.' % vrep['moved'])
        if vrep['missing']:
            log('  no wheel of yours for the game\'s %s wheel(s): nothing is drawn there' % ', '.join(vrep['missing']))
        if vrep['static']:
            log('  %d extra wheel(s) on another axle stay fixed (the game vehicle has no bone for them)' % vrep['static'])
        for role, n in vrep['parts'].items():
            log('  %s: %d piece(s) move with the game\'s %s' % (role.replace('_', ' '), n, role.replace('_', ' ')))
    out = []
    for i, p in enumerate(ps):
        n = len(Pw[i])
        if Jl is not None:
            J, W = Jl[i], Wl[i]
        else:
            J = np.zeros((n, 4), np.int64)
            W = np.zeros((n, 4))
            W[:, 0] = 1
        out.append(dict(mi=p['mi'], P=Pw[i], N=p['N'], UV=p['UV'], T=p['T'], J=J, W=W, groups=[0], lod=None, info=p['info'],
                        pid=p['pid'], vid=p['vid']))
    if kind == 'vehicle':
        rep['pieces'] = _vehicle_pieces(ps, Pw, comps, wg) + rep['removed']
    elif kind == 'rigid':
        rep['pieces'] = pieces.entries(ps, Pw, []) + rep['removed']
    if kind == 'character':
        out = _character(out, body, st, rep, log)
        rep['pieces'] = rep.get('pieces', []) + rep['removed']
    for e in rep.get('pieces', []):
        e['chosen'] = st.roles.get(e['id'], 'auto')
    return out, kind, rep, s


def _vehicle_pieces(ps, Pw, comps, wg):
    """Parts list entries of a vehicle: each wheel found (with its rim, bolts...), each part with a moving role, then the
    other big pieces."""
    if comps is None:
        return pieces.entries(ps, Pw, [])
    allP = np.concatenate(Pw)
    mid = (allP.min(0) + allP.max(0)) / 2
    groups = []
    for g in (wg or []):
        members = [(c.part, c.verts) for c in g.comps]
        Q = np.concatenate([Pw[i][v] for i, v in members])
        c = (Q.min(0) + Q.max(0)) / 2
        side = 'middle' if abs(c[0] - mid[0]) < 0.12 * np.ptp(allP[:, 0]) else ('left' if c[0] > mid[0] else 'right')
        groups.append(('Wheel, %s %s' % ('front' if c[2] > mid[2] else 'back', side), 'wheel', members))
    for c in comps:
        if c.kind or not c.role or c.role in ('wheel', 'caliper'):
            continue
        groups.append(('%s (%s)' % (pieces._name(ps[c.part]), c.role.replace('_', ' ')), c.role, [(c.part, c.verts)]))
    return pieces.entries(ps, Pw, groups)


def _seats(model_path, ps, Pw, comps, vr, st, log):
    """Where the riders sit in the new model, as seats.Seat positions and as moves from the original's seats (what the
    co-op mod applies). -> dict or None (not one of the six drivable vehicles)."""
    g = seats.game_seat(model_path)
    if g is None:
        return None
    name = model_path.replace('/', '\\').rsplit('\\', 1)[-1].lower()
    bike = name in seats.BIKES
    gp = g.copy() if bike else g * [-1, 1, 1]          # the game's passenger: mirrored (bikes: the co-op mod's rule too)
    d = p = None
    how = 'as in the original'
    if st.seats == 'auto':
        # 1. a steering wheel / handlebar named in the model: the driver sits where he can hold it, as in the original
        spec = vr.spec or {}
        gb = spec.get('steering_wheel', spec.get('handlebar'))
        mine = [c for c in comps if c.role in ('steering_wheel', 'handlebar')]
        if gb is not None and mine:
            Q = np.concatenate([Pw[c.part][c.verts] for c in mine])
            d = g + ((Q.min(0) + Q.max(0)) / 2 - vr.bones[gb].pivot)
            how = 'from your model\'s steering wheel' if not bike else 'from your model\'s handlebar'
        # 2. the seats' shape
        body_T, body_P, off = [], [], 0
        wheel_v = {}
        for c in comps:
            if c.kind == 'wheel':
                wheel_v.setdefault(c.part, set()).update(int(v) for v in c.verts)
        for i, p_ in enumerate(ps):
            T = p_['T']
            if i in wheel_v:
                w = np.zeros(len(Pw[i]), bool)
                w[list(wheel_v[i])] = True
                T = T[~w[T].any(1)]
            body_P.append(Pw[i])
            body_T.append(T + off)
            off += len(Pw[i])
        BP, BT = np.concatenate(body_P), np.concatenate(body_T) if body_T else np.zeros((0, 3), np.int64)
        ground = float(np.mean([w.centre[1] - w.radius for w in vr.wheels])) if vr.wheels else None
        found = seats.find_seats(BP, BT, ground, bike) if len(BT) else []
        # where the original has its seats, carried over to the new model's size: the seat found nearest that wins
        # (a hood, a footwell or the back seats elsewhere can't)
        om = vr.model
        box_u = (BP.min(0), BP.max(0))
        pd = seats.carried_over(g, (om.box_min, om.box_max), box_u)
        sd = seats.near(found, pd, 0.0 if bike else 1.0)
        if d is None and sd is not None:
            d = sd.pos
            how = 'from the shape of your model\'s seats' if not bike else 'from your model\'s saddle'
        elif d is None:
            d = pd
            how = 'guessed from your model\'s size (no seat found: check the preview)'
        if not bike:
            sp = seats.near(found, seats.carried_over(gp, (om.box_min, om.box_max), box_u), -1.0)
            if sp is not None:
                p = sp.pos
    if d is None:
        d = g.copy()
        if st.seats == 'auto':
            log('Seats: none found in your model, the riders sit as in the original (move them with the Seat controls).')
    if p is None:
        p = d.copy() if bike else d * [-1, 1, 1]
    d = d + [st.seat_x, st.seat_y, st.seat_z]
    p = p + [st.pass_x, st.pass_y, st.pass_z]
    move_d, move_p = d - g, p - gp
    if st.seats == 'auto' or any([st.seat_x, st.seat_y, st.seat_z, st.pass_x, st.pass_y, st.pass_z]):
        log('Seats (%s): driver moved %.0f, passenger %.0f from the original seats. The co-op mod draws the riders there.'
            % (how, np.linalg.norm(move_d), np.linalg.norm(move_p)))
    return dict(driver=[round(float(x), 2) for x in d], passenger=[round(float(x), 2) for x in p],
                driver_move=[round(float(x), 2) for x in move_d], passenger_move=[round(float(x), 2) for x in move_p],
                game_driver=[float(x) for x in g], game_passenger=[float(x) for x in gp], how=how)


def _wheel_hint(ps, st, n_game):
    """Why no wheels were found: try the other turns / stand-up settings and say which one shows them."""
    raw = [p['P'] for p in ps]
    allP = np.concatenate(raw)
    found = []
    for tilt in ('none', 'x+90', 'x-90', 'z+90', 'z-90', 'x180'):
        for yaw in (0, 90, 180, 270):
            if tilt == st.tilt and yaw == st.yaw:
                continue
            R = _yaw(yaw) @ TILTS[tilt]
            wg = vehicle.detect(ps, [p @ R.T for p in raw], lambda *a: None)[1]
            if len(wg) >= min(3, n_game):
                found.append((len(wg), tilt, yaw))
    if found:
        n, tilt, yaw = max(found, key=lambda f: (f[0] == n_game or f[0] == 3, f[0], f[1] == st.tilt))
        names = {'none': 'as it is', 'x+90': 'tip back 90°', 'x-90': 'tip forward 90°', 'z+90': 'roll left 90°',
                 'z-90': 'roll right 90°', 'x180': 'upside down'}
        return 'With Turn %d° and Stand it up "%s" %d wheels show up: try that.' % (yaw, names[tilt], n)
    return ('Give the wheel objects names with "wheel" or "tire" in Blender (each wheel its own object), and check Turn '
            '(the yellow line is the front).')


def _character(parts, body, st, rep, log):
    allP = np.concatenate([p['P'] for p in parts])
    off = np.cumsum([0] + [len(p['P']) for p in parts])
    allT = np.concatenate([p['T'] + o for p, o in zip(parts, off)]) if parts else np.zeros((0, 3), np.int64)
    J = W = None

    def apply(fn_P, fn_N):
        nonlocal allP
        allN = np.concatenate([p['N'] for p in parts])
        P2, N2 = fn_P(allP, allN), fn_N(allP, allN)
        k = 0
        for p in parts:
            n = len(p['P'])
            p['P'], p['N'] = P2[k:k + n], N2[k:k + n]
            k += n
        allP = P2

    fit_body = st.body == 'fit' and len(allT) > 0
    if fit_body:
        # legs and height first: the pose check below compares arms at the game's shoulder height
        Lg = character.game_landmarks(body)
        pre = character.Proportions(character.landmarks(character.surface_samples(allP, allT)), Lg, body.bones, arms=False)
        apply(lambda P, N: pre(P), pre.normals)
    if st.pose == 'auto':
        pose, found = character.estimate_pose(body, allP)
        if found:
            log('Pose of your model: ' + ', '.join('%s %d° %s%s' % (k, a, 'down' if 'arm' in k else 'out',
                                                                   (', %d° %s' % (abs(f), 'forward' if f > 0 else 'back')) if abs(f) >= 8 else '')
                                                for k, (a, f) in found.items()))
        if pose:
            res = {}

            def rp(P, N):
                res['r'] = character.repose(body, P, N, allT, pose)
                return res['r'][2]
            apply(rp, lambda P, N: res['r'][3])
            J, W = res['r'][0], res['r'][1]
            log('  straightened into the game\'s T-pose (arms out to the sides), so it bends with the game\'s bones')
        else:
            log('  already in the game\'s T-pose')
    if fit_body:
        Lu = character.landmarks(character.surface_samples(allP, allT))
        # legs and height were fitted already (and an arm straightened above can disturb the hips): arms only now
        for a in ('floor', 'crotch', 'top', 'cx', 'cz'):
            setattr(Lu, a, getattr(Lg, a))
        Lu.leg_x = list(Lg.leg_x)
        fit = character.Proportions(Lu, Lg, body.bones)
        rep['_landmarks'] = (Lu, Lg)
        apply(lambda P, N: fit(P), fit.normals)
        J = W = None
        log('Body fitted onto the game skeleton (the game\'s animations set the hip height and bend at its joints): '
            + 'legs x%.2f, body above x%.2f, arms x%.2f / x%.2f' % (pre.legs * fit.legs, pre.s * fit.s, *fit.arm_len)
            + ('' if pre.Lu.crotch_found else ' (couldn\'t see where the legs part: assumed)'))
        rep['proportions'] = dict(legs=round(pre.legs * fit.legs, 3), body=round(pre.s * fit.s, 3), arms=[round(a, 3) for a in fit.arm_len])
    if J is None:
        J, W = character.transfer_weights(body, allP, T=allT)
    allpcs = pieces.all_pieces(parts)
    force_head = np.zeros(len(allP), bool)
    force_body = np.zeros(len(allP), bool)
    for i, vs, cid in allpcs:
        r = st.roles.get(cid)
        if r == 'head':
            force_head[off[i] + vs] = True
        elif r == 'body':
            force_body[off[i] + vs] = True
    nh = character.rigid_head(body, allP, allT, J, W, off, force_head, force_body)
    hw = (W * (J == character.HEAD)).sum(1)
    groups = [('%s (head)' % pieces._name(parts[i]), 'head', [(i, vs)]) for i, vs, cid in allpcs if hw[off[i] + vs].mean() > 0.9]
    rep['pieces'] = pieces.entries(parts, [p['P'] for p in parts], groups, min_size=0.03)
    if nh:
        log('  head: %d separate piece(s) (head, hair, hat...) move as one with the head' % nh)
    k = 0
    out = []
    for p in parts:
        n = len(p['P'])
        p = dict(p, J=J[k:k + n], W=W[k:k + n])
        k += n
        regs = character.regions_for(body, p['P'], p['T']) if len(p['T']) else []
        by = {}
        for ti, r in enumerate(regs):
            by.setdefault(r, []).append(ti)
        for r, tis in by.items():
            T = p['T'][np.array(tis)]
            used = np.unique(T)
            remap = np.full(n, -1)
            remap[used] = np.arange(len(used))
            out.append(dict(p, P=p['P'][used], N=p['N'][used], UV=p['UV'][used], J=p['J'][used], W=p['W'][used], T=remap[T], groups=list(r)))
    bad = character.pose_check(body, allP, J, W)
    nreg = len({tuple(p['groups']) for p in out})
    log('Character: weights taken from the game model (%d bones), body split into %d region(s) drawn in %d display group(s).' % (
        len(body.bones), nreg, len({g for p in out for g in p['groups']})))
    if bad > 0.25:
        log('  Warning: %.0f%% of your model\'s arm vertices are not near the game character\'s arms. Is it in a T-pose '
            '(arms straight out to the sides)? Check the preview.' % (100 * bad))
    rep['character'] = dict(regions=nreg, pose_mismatch=round(bad, 3), lods=body.lods)
    return out


def _lod_copies(parts, lods, max_vertices, log):
    """Zombies have near / middle / far versions: the full body for the near one, thinned copies for the others."""
    if len(lods) <= 1:
        return parts, max_vertices
    share = [1.0, 0.3, 0.12][:len(lods)]
    total = sum(len(p['P']) for p in parts)
    near_budget = int(max_vertices / sum(share))
    base = _thin_parts(parts, near_budget, lambda *a: None) if total > near_budget else parts
    btot = sum(len(p['P']) for p in base)
    out = []
    for lv, sh in zip(lods, share):
        for p in base:
            q = p if sh == 1.0 else _thin_part(p, max(24, int(len(p['P']) * sh)))
            if len(q['T']):
                out.append(dict(q, lod=lv))
    log('Levels of detail like the original: %s vertices (near / middle / far)' % ' / '.join(
        str(sum(len(p['P']) for p in out if p['lod'] == lv)) for lv in lods))
    return out, max_vertices


def texture_names(model_path, count):
    folder, base = model_path.rsplit('\\', 1)
    return ['%s\\%s_x%02d_%s' % (folder, base, k, kind) for k in range(count) for kind in ('BM', 'MM')]


FRANK = 'model\\pl\\pl00\\pl00'
# Frank's starting clothes (top + trousers, shoes, hair, glasses slot): separate models drawn on him by the game
FRANK_CLOTHES = ['model\\pl\\cos\\cos000\\cos000', 'model\\pl\\cos\\cos100\\cos100', 'model\\pl\\cos\\cos201\\cos201',
                 'model\\pl\\cos\\cos301\\cos301']
FRANK_BODY_GROUPS = (70,)   # his neck piece: a group that is on whatever he does (head and hand groups have variants)


def companions_needed(model_path):
    """Other game models a swap of this one reads (and may hide): Frank's base model only has his head and hands."""
    return list(FRANK_CLOTHES) if model_path.lower() == FRANK else []


def blank_model(path, original_bytes):
    """A model with nothing to draw (one zero-size triangle), same skeleton: hides a clothing part."""
    om = mod.Model(original_bytes)
    z = np.zeros((3, 3))
    names = texture_names(path, 1)
    mesh = dict(P=z, N=np.tile([[0.0, 1.0, 0.0]], (3, 1)), UV=np.zeros((3, 2)), T=np.array([[0, 1, 2]]), groups=[0], lod=None)
    data = mod.build(dict(model=om, texture_names=names), [dict(colour=1, mask=2, normal=0, meshes=[mesh])], om.box_min, om.box_max)
    t = tex.encode(Image.new('RGB', (8, 8), (0, 0, 0)), 8, colour_map=False)
    return [arc.Entry.from_data(path, arc.TYPE_MODEL, data), arc.Entry.from_data(names[0], arc.TYPE_TEXTURE, t),
            arc.Entry.from_data(names[1], arc.TYPE_TEXTURE, t)]


def _atlas(placed, materials, size, log):
    """All materials into one texture (characters: every material x display group x LOD is a separate draw, an atlas
    keeps that down to one per region)."""
    used = sorted({p['mi'] for p in placed})
    if len(used) <= 1:
        return placed, materials
    g = int(np.ceil(np.sqrt(len(used))))
    cell = max(16, size // g)
    pad = max(2, cell // 64)
    atlas = Image.new('RGB', (size, size), (128, 128, 128))   # a power of two: the texture keeps its exact size
    where = {}
    for k, mi in enumerate(used):
        m = materials[mi] if 0 <= mi < len(materials) else dict(image=None, colour=[0.7, 0.7, 0.7])
        col = np.clip(np.array(m.get('colour', [1, 1, 1]), float), 0, 1)
        img = m.get('image')
        if img is None:
            img = Image.new('RGB', (4, 4), tuple(int(c * 255) for c in col))
        elif (col < 0.999).any():
            img = Image.fromarray(np.clip(np.asarray(img, np.float32) * col, 0, 255).astype(np.uint8))
        cx, cy = k % g, k // g
        inner = cell - 2 * pad
        im = img.convert('RGB').resize((inner, inner), Image.LANCZOS)
        # edges repeated into the padding so mip levels don't bleed the neighbour in
        big = Image.new('RGB', (cell, cell))
        big.paste(im.resize((cell, cell), Image.NEAREST), (0, 0))
        big.paste(im, (pad, pad))
        atlas.paste(big, (cx * cell, cy * cell))
        where[mi] = (cx * cell + pad, cy * cell + pad, inner)
    W = size
    out = []
    for p in placed:
        x0, y0, inner = where[p['mi']]
        UV = np.clip(p['UV'], 0, 1) * (inner - 1) / W + np.array([x0, y0]) / W
        out.append(dict(p, UV=UV, mi=0))
    log('Textures: %d materials put into one %dx%d texture (fewer draw calls for a character)' % (len(used), W, W))
    return out, [dict(name='atlas', image=atlas, colour=[1, 1, 1])]


def _merge(placed):
    """Parts with the same material, display groups and LOD become one (fewer draw entries)."""
    by = {}
    for p in placed:
        by.setdefault((p['mi'], tuple(p['groups']), p['lod']), []).append(p)
    out = []
    for (mi, groups, lod), ps in by.items():
        if len(ps) == 1:
            out.append(ps[0])
            continue
        off = np.cumsum([0] + [len(p['P']) for p in ps])
        out.append(dict(ps[0], P=np.concatenate([p['P'] for p in ps]), N=np.concatenate([p['N'] for p in ps]),
                        UV=np.concatenate([p['UV'] for p in ps]), J=np.concatenate([p['J'] for p in ps]),
                        W=np.concatenate([p['W'] for p in ps]), T=np.concatenate([p['T'] + o for p, o in zip(ps, off)])))
    return out


def convert(model_path, original_bytes, parts, materials, st, log=print, companions=None):
    """-> (payload entries [arc.Entry], stats dict). Raises ValueError for models the tool can't write.
    companions: {model path: bytes} of the models companions_needed() names (Frank's clothes)."""
    om = mod.Model(original_bytes)
    if not om.supported():
        raise ValueError('this game model uses a vertex layout the tool does not write (some Frank outfit parts, cutscene models and level geometry)')
    if not parts:
        raise ValueError('no triangle meshes in the .glb')
    extra = []
    if model_path.lower() == FRANK and companions:
        extra = [(mod.Model(companions[k]), FRANK_BODY_GROUPS) for k in FRANK_CLOTHES if k in companions]
    placed, kind, rep, s = prepare(model_path, om, parts, st, log, extra)
    if kind == 'character':
        body_lods = rep['character']['lods']
        if len(body_lods) > 1:
            placed, _ = _lod_copies(placed, body_lods, st.max_vertices, log)
        else:
            placed = _thin_parts(placed, st.max_vertices, log)
    else:
        placed = _thin_parts(placed, st.max_vertices, log)
    allW = np.concatenate([p['P'] for p in placed])
    vmin = np.minimum(om.box_min, allW.min(0) - 1.0)
    vmax = np.maximum(om.box_max, allW.max(0) + 1.0)
    placed = [_wrap_part(p) for p in placed]
    if kind == 'character':
        atlas_size = min(2048, max(512, st.max_texture * 2))
        placed, materials = _atlas(placed, materials, atlas_size, log)
    placed = _merge(placed)
    groups = {}
    for p in placed:
        groups.setdefault(p['mi'], []).append(dict(P=p['P'], N=p['N'], UV=p['UV'], T=p['T'], J=p['J'], W=p['W'], groups=p['groups'], lod=p['lod']))
    order = sorted(groups)
    names = texture_names(model_path, len(order))
    # the copied shader settings may expect a normal map: give it a flat one (else the slot would point at a wrong texture)
    needs_nm = om.material_normal_map(om.template_material()) != 0
    if needs_nm:
        names.append(model_path + '_xNM')
    nm_idx = len(names) if needs_nm else 0
    mats = [dict(colour=2 * k + 1, mask=2 * k + 2, normal=nm_idx, meshes=groups[mi]) for k, mi in enumerate(order)]
    try:
        data = mod.build(dict(model=om, texture_names=names, bones=rep.get('bones')), mats, vmin, vmax)
    except mod.TooManyVertices:
        # splitting into the game's pieces stores some vertices twice: thin a bit more and build again
        if st.max_vertices <= 8000:
            raise
        less = int(st.max_vertices * 0.8)
        log('Over the game\'s 65,535 vertices once split into pieces: trying again with a vertex budget of %d.' % less)
        return convert(model_path, original_bytes, parts, materials, replace(st, max_vertices=less), log, companions)
    entries = [arc.Entry.from_data(model_path, arc.TYPE_MODEL, data)]
    mask = tex.encode(Image.new('RGB', (16, 16), (70, 70, 70)), 16, colour_map=False)
    for k, mi in enumerate(order):
        m = materials[mi] if 0 <= mi < len(materials) else dict(name='(no material)', image=None, colour=[0.7, 0.7, 0.7])
        img, col = m['image'], np.clip(np.array(m['colour'], float), 0, 1)
        if img is None:
            img = Image.new('RGB', (8, 8), tuple(int(c * 255) for c in col))
        elif (col < 0.999).any():
            img = Image.fromarray(np.clip(np.asarray(img, np.float32) * col, 0, 255).astype(np.uint8))
        entries.append(arc.Entry.from_data(names[2 * k], arc.TYPE_TEXTURE, tex.encode(img, max(st.max_texture, img.size[0] if m.get('name') == 'atlas' else 0), True)))
        entries.append(arc.Entry.from_data(names[2 * k + 1], arc.TYPE_TEXTURE, mask))
        log('  material %d "%s": texture %s' % (k, m.get('name', ''), '%dx%d' % img.size))
    if needs_nm:
        entries.append(arc.Entry.from_data(names[-1], arc.TYPE_TEXTURE, tex.flat_normal_map()))
    nm = mod.Model(data)
    stats = dict(vertices=nm.n_verts, triangles=nm.n_polys, pieces=nm.n_pieces, materials=nm.n_materials, scale=s, kind=kind,
                 size=[round(float(x), 1) for x in (allW.max(0) - allW.min(0))],
                 original_size=[round(float(x), 1) for x in (om.box_max - om.box_min)])
    for k in ('vehicle', 'character', 'proportions', 'seats', 'pieces'):
        if k in rep:
            stats[k] = rep[k]
    if model_path.lower() == FRANK and st.clothes == 'default' and companions:
        stats['hide'] = sorted(k for k in FRANK_CLOTHES if k in companions)
        log('Frank\'s starting clothes (%s) are hidden so they don\'t cover your model. Clothes he changes into later still show.'
            % ', '.join(k.rsplit('\\', 1)[-1] for k in stats['hide']))
    log('Built: %d vertices, %d triangles, %d materials, size %s (original %s)' % (stats['vertices'], stats['triangles'], stats['materials'], stats['size'], stats['original_size']))
    return entries, stats
