"""Vehicles: find the wheels (and other moving parts) of the .glb, fit the model to the game vehicle's wheels, and tie
each part to the bone that moves it in the game (wheels spin and steer, doors swing, handlebars turn).

How the game moves a wheel (see rig.py): the wheel bone turns around its own X axis, through the bone's pivot, and a
front wheel's hub bone turns around Y for steering. So a wheel only spins true if its axle lies exactly on the game
wheel's axle line. The physics (ground contact, suspension) uses the game's wheels, so a wheel of the game's size sits
on the ground correctly.
"""
import re
import numpy as np


# words in node / mesh names. Checked in this order (a "steering wheel" is not a wheel).
ROLES = [
    ('steering_wheel', r'steer|lenkrad|volante'),
    ('caliper', r'calip|brake.?cal|bremssattel|etrier'),
    ('wheel', r'wheel|tire|tyre|(^|[^a-z])rims?([^a-z]|$)|felge|reifen|rueda|llanta|neum|roue|pneu|hub.?cap|spoke|rotor|brake.?disc|disc.?brake'),
    ('door', r'door|t[uü]e?r([^a-z]|$)|puerta|porti[eè]re|porte'),
    ('hood', r'hood|bonnet|motorhaube|capot'),
    ('trunk', r'trunk|(^|[^a-z])boot([^a-z]|$)|tailgate|kofferraum|hatch|liftgate'),
    ('handlebar', r'handle.?bar|lenker|manubrio'),
    ('fork', r'fork|gabel'),
    ('swingarm', r'swing.?arm|schwinge'),
    ('crank', r'crank|pedal|kurbel'),
]
MATERIAL_WHEEL = r'tire|tyre|rubber|wheel|(^|[^a-z])rims?([^a-z]|$)|reifen|felge'


def role_of(info):
    if not info:
        return None
    if re.search(r'spare|reserve|ersatz', ((info.get('name') or '') + ' ' + (info.get('mesh') or '')).lower()):
        return None   # a spare wheel is part of the body
    for k in ('name', 'mesh'):
        s = (info.get(k) or '').lower()
        # the last path element counts most ("Car/Body/Wheel_FL" is a wheel; "Wheels/..." children too)
        for role, rx in ROLES:
            if re.search(rx, s):
                return role
    s = (info.get('material') or '').lower()
    if re.search(MATERIAL_WHEEL, s):
        return 'wheel'
    return None


class _UF:
    def __init__(self, n):
        self.p = np.arange(n)

    def find(self, a):
        p = self.p
        r = a
        while p[r] != r:
            r = p[r]
        while p[a] != r:
            p[a], a = r, p[a]
        return r

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def components(P, T, tol):
    """Vertex labels of the connected pieces of one mesh (vertices at the same spot count as joined: glTF splits
    vertices along UV seams)."""
    n = len(P)
    if n == 0:
        return np.zeros(0, np.int64)
    q = np.floor(P / max(tol, 1e-9)).astype(np.int64)
    _, weld = np.unique(q, axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    m = weld.max() + 1
    uf = _UF(m)
    if len(T):
        a, b, c = weld[T[:, 0]], weld[T[:, 1]], weld[T[:, 2]]
        e = np.unique(np.sort(np.r_[np.c_[a, b], np.c_[b, c]], axis=1), axis=0)
        # union in bulk: repeat min-label propagation (fast in numpy), finish with the union-find for long chains
        lab = np.arange(m)
        for _ in range(60):
            lo = np.minimum(lab[e[:, 0]], lab[e[:, 1]])
            new = lab.copy()
            np.minimum.at(new, e[:, 0], lo)
            np.minimum.at(new, e[:, 1], lo)
            new = new[new]
            if np.array_equal(new, lab):
                break
            lab = new
        else:
            uf.p = lab.copy()
            for x, y in e:
                uf.union(int(x), int(y))
            lab = np.array([uf.find(i) for i in range(m)])
        _, lab = np.unique(lab, return_inverse=True)
        return lab.reshape(-1)[weld]
    return weld


class Comp:
    __slots__ = ('part', 'verts', 'lo', 'hi', 'role', 'kind', 'group', 'forced', 'id')

    def __init__(self, part, verts, P, role):
        self.part, self.verts, self.role = part, verts, role
        self.forced, self.id = False, None
        Q = P[verts]
        self.lo, self.hi = Q.min(0), Q.max(0)
        self.kind = None
        self.group = -1

    @property
    def centre(self):
        return (self.lo + self.hi) / 2

    @property
    def size(self):
        return self.hi - self.lo

    @property
    def radius(self):
        return max(self.size[1], self.size[2]) / 2

    def round(self, tol=0.2):
        s = self.size
        return s[1] > 1e-6 and abs(s[1] - s[2]) <= tol * max(s[1], s[2]) and s[0] < 1.05 * max(s[1], s[2])


class WheelGroup:
    def __init__(self, comps):
        self.comps = comps
        main = max(comps, key=lambda c: c.radius if c.round(0.35) else c.radius * 0.5)
        self.main = main
        self.axis = main.centre[1:].copy()        # y, z of the axle
        self.radius = main.radius
        lo = np.min([c.lo for c in comps], 0)
        hi = np.max([c.hi for c in comps], 0)
        self.lo, self.hi = lo, hi
        self.target = None

    @property
    def centre(self):
        return np.array([(self.lo[0] + self.hi[0]) / 2, self.axis[0], self.axis[1]])


def detect(parts, Q, log, roles=None):
    """parts: list of part dicts; Q: their positions after the model's turn (before scaling). roles: {piece id: role}
    chosen in the Parts list (they win over names and shapes; a piece marked 'wheel' is a wheel even if it doesn't look
    round). -> (comps, wheel groups)."""
    allQ = np.concatenate(Q)
    lo, hi = allQ.min(0), allQ.max(0)
    ext = float((hi - lo).max())
    comps = []
    for i, p in enumerate(parts):
        lab = components(Q[i], p['T'], ext * 2e-5)
        role = role_of(p.get('info'))
        if len(lab) == 0:
            continue
        order = np.argsort(lab, kind='stable')
        cuts = np.flatnonzero(np.diff(lab[order])) + 1
        for vs in np.split(order, cuts):
            c = Comp(i, vs, Q[i], role)
            if 'pid' in p:
                c.id = '%d:%d' % (p['pid'], int(p['vid'][int(np.min(vs))]))
                r = (roles or {}).get(c.id)
                if r and r != 'auto':
                    c.role, c.forced = r, True
            comps.append(c)
    height = hi[1] - lo[1]

    def disc(c, tol):  # a wheel-like piece: round, axle across the vehicle (thin in X), in the lower half;
        # big, or small but standing on the ground (a cartoon car's little wheels)
        return c.round(tol) and c.size[0] < 0.9 * max(c.size[1], c.size[2]) and c.centre[1] < lo[1] + 0.5 * height \
            and (c.radius > 0.04 * ext or (c.radius > 0.015 * ext and c.lo[1] - lo[1] < 0.06 * height))
    named = [c for c in comps if c.role == 'wheel']
    src = 'named' if named else 'shape'
    cand = [c for c in (named or comps) if (c.forced and c.role == 'wheel') or (c.role in (None, 'wheel') and disc(c, 0.3 if named else 0.15))]
    if cand:
        big = max(c.radius for c in cand)
        cand = [c for c in cand if c.radius >= 0.55 * big or c.forced]
    if not cand:
        return comps, [], src
    # discs on the same axle close together are one wheel (tyre + rim + hub cap ...)
    cand.sort(key=lambda c: -c.radius)
    groups = []
    for c in cand:
        for g in groups:
            m = g[0]
            R = max(m.radius, c.radius)
            if np.hypot(*(c.centre[1:] - m.centre[1:])) < 0.35 * R and abs(c.centre[0] - m.centre[0]) < 1.6 * R:
                g.append(c)
                break
        else:
            groups.append([c])
    wg = [WheelGroup(g) for g in groups]
    # unnamed small parts inside a wheel (bolts, valve, rim pieces split into many meshes): on the axle -> spin with it;
    # off the axle (brake callipers) -> stay with the hub
    taken = {id(c) for g in wg for c in g.comps}
    for c in comps:
        if id(c) in taken or c.role not in (None, 'caliper', 'wheel'):
            continue
        if c.role == 'wheel' and c.kind is None:
            pass   # a named wheel part that isn't a tyre (rim, bolts, calliper under "Wheel_FL"): placed below
        for g in wg:
            R = g.radius
            d_lo = np.hypot(np.maximum(abs(c.lo[1] - g.axis[0]), abs(c.hi[1] - g.axis[0])), np.maximum(abs(c.lo[2] - g.axis[1]), abs(c.hi[2] - g.axis[1])))
            inside = d_lo <= 1.02 * R and c.lo[0] >= g.lo[0] - 0.35 * R and c.hi[0] <= g.hi[0] + 0.35 * R
            if not inside or max(c.size) > 2.05 * R:
                continue
            # around the axle (its box holds the axle: rim, hub cap, disc) or a small piece (bolt, valve): spins.
            # Off the axle and bigger (a brake calliper): turns with the steering, doesn't spin.
            holds_axle = c.lo[1] <= g.axis[0] <= c.hi[1] and c.lo[2] <= g.axis[1] <= c.hi[2]
            if c.role == 'caliper' or (not holds_axle and max(c.size[1], c.size[2]) >= 0.2 * R):
                c.kind = 'caliper'
                c.group = wg.index(g)
            else:
                g.comps.append(c)
            break
    for k, g in enumerate(wg):
        for c in g.comps:
            c.kind, c.group = 'wheel', k
    for c in comps:
        if c.kind is None and c.role == 'caliper':
            c.kind = 'caliper'
    return comps, wg, src


def _axles(z, gap):
    """Indices grouped into axles by z (front first): a new axle where the gap to the previous is bigger than gap."""
    order = np.argsort(-np.asarray(z))
    out, last = [], None
    for i in order:
        if last is None or last - z[i] > gap:
            out.append([])
        out[-1].append(int(i))
        last = z[i]
    return out


class Plan:
    """Which game wheel bone(s) each of the .glb's wheels goes on, by axle: axles are paired front to back, and on an
    axle a wheel goes left or right, or, standing in the middle (a three-wheeler's single wheel), on both: half on the
    left wheel's bone, half on the right's. Both turn the same way around the same axle line, so the blend spins and
    steers exactly like one wheel in the middle."""

    def __init__(self, wg, vr):
        self.wg, self.vr = wg, vr
        self.bones = [None] * len(wg)        # [(bone, weight), ...] or None (stays fixed to the body)
        self.axle_of = [None] * len(wg)      # index into self.user_axles
        self.user_axles, self.game_axles, self.pairs = [], [], {}   # pairs: user axle -> game axle
        if not wg or not vr.wheels:
            return
        rg = vr.wheels
        R = float(np.median([g.radius for g in wg]))
        self.user_axles = _axles([g.centre[2] for g in wg], 0.8 * R)
        Rg = float(np.median([w.radius for w in rg]))
        self.game_axles = _axles([w.centre[2] for w in rg], 0.8 * Rg)
        nu, ng = len(self.user_axles), len(self.game_axles)
        uz = np.array([np.mean([wg[i].centre[2] for i in a]) for a in self.user_axles])
        gz = np.array([np.mean([rg[i].centre[2] for i in a]) for a in self.game_axles])

        def norm(z):
            return (z - z.min()) / max(np.ptp(z), 1e-9) if len(z) > 1 else np.zeros(len(z))
        un, gn = norm(uz), norm(gz)
        if nu == ng:
            self.pairs = {i: i for i in range(nu)}
        elif nu > ng:   # more axles than the game vehicle: each game axle takes the nearest; the rest stay fixed
            for j in range(ng):
                free = [i for i in range(nu) if i not in self.pairs]
                i = min(free, key=lambda i: abs(un[i] - gn[j]))
                self.pairs[i] = j
        else:
            for i in range(nu):
                self.pairs[i] = int(np.argmin(np.abs(gn - un[i])))
        for ua, axle in enumerate(self.user_axles):
            for i in axle:
                self.axle_of[i] = ua
            if ua not in self.pairs:
                continue
            gw = [rg[j] for j in self.game_axles[self.pairs[ua]]]
            if len(gw) == 1:
                for i in axle:
                    self.bones[i] = [(gw[0].bone, 1.0)]
                continue
            left = max(gw, key=lambda w: w.centre[0])
            right = min(gw, key=lambda w: w.centre[0])
            xs = [wg[i].centre[0] for i in axle]
            mid = (max(xs) + min(xs)) / 2 if len(axle) > 1 else self._mid_x
            half = max((max(xs) - min(xs)) / 2, 1e-6)
            for i in axle:
                x = wg[i].centre[0]
                centred = abs(x - self._mid_x) < 0.15 * self._half_w if len(axle) == 1 else abs(x - mid) < 0.25 * half
                if centred:
                    self.bones[i] = [(left.bone, 0.5), (right.bone, 0.5)]
                else:
                    self.bones[i] = [((left if x > mid else right).bone, 1.0)]

    _mid_x, _half_w = 0.0, 1.0

    @classmethod
    def build(cls, wg, vr, lo, hi):
        cls._mid_x = (lo[0] + hi[0]) / 2
        cls._half_w = max((hi[0] - lo[0]) / 2, 1e-6)
        return cls(wg, vr)

    def game_wheel(self, i):
        b = self.bones[i]
        return None if b is None else next(w for w in self.vr.wheels if w.bone == b[0][0])

    def fit(self, foot_s, log):
        """Scale from the wheelbase (front-to-back distance of the axles), not more than the game vehicle's length and
        width allow (+10%). -> s or None."""
        ua = [i for i in range(len(self.user_axles)) if i in self.pairs]
        if len(ua) < 2:
            return None
        U = np.array([np.mean([self.wg[i].centre[2] for i in self.user_axles[a]]) for a in ua])
        G = np.array([np.mean([self.vr.wheels[j].centre[2] for j in self.game_axles[self.pairs[a]]]) for a in ua])
        Uc, Gc = U - U.mean(), G - G.mean()
        den = float((Uc ** 2).sum())
        if den < 1e-9:
            return None
        s = float((Uc * Gc).sum() / den)
        if s <= 0:
            return None
        if s > 1.1 * foot_s:
            log('Your model\'s wheels are close together for its size: matching the game\'s wheelbase would make it %.1fx the '
                'game vehicle\'s length or width. It is kept to the game vehicle\'s size; its wheels go out to the game\'s axles.'
                % (s / foot_s))
            s = 1.1 * foot_s
        return s

    def offset(self, s):
        """Translation that puts the wheel centres (on average) on the game's: wheelbase centre, axle height, middle."""
        ua = [i for i in range(len(self.user_axles)) if i in self.pairs]
        U = np.array([np.mean([self.wg[i].centre for i in self.user_axles[a]], 0) for a in ua])
        G = np.array([np.mean([self.vr.wheels[j].centre for j in self.game_axles[self.pairs[a]]], 0) for a in ua])
        t = G.mean(0) - s * U.mean(0)
        xs = [g.centre[0] for g in self.wg]
        gx = [w.centre[0] for w in self.vr.wheels]
        t[0] = (max(gx) + min(gx)) / 2 - s * (max(xs) + min(xs)) / 2
        return t

    def track_stretch(self, Pw_wheels_x):
        """Sideways factor that makes the .glb's widest axle as wide as the game's (for "the game's track width")."""
        best = None
        for ua, ga in self.pairs.items():
            xs = [Pw_wheels_x[i] for i in self.user_axles[ua]]
            gx = [self.vr.wheels[j].centre[0] for j in self.game_axles[ga]]
            if len(xs) > 1 and len(gx) > 1 and np.ptp(xs) > 1e-6:
                f = np.ptp(gx) / np.ptp(xs)
                best = f if best is None else max(best, f)
        return best


def assign(parts, Pw, comps, plan, vr, st, log):
    """Bone numbers / weights for every vertex; wheels moved onto the game's axle lines (and resized), the parts around
    them (axle rods, hubs, callipers) moved along. Changes Pw in place. -> (J list, W list, report)."""
    J = [np.zeros((len(p), 4), np.int64) for p in Pw]
    W = [np.c_[np.ones(len(p)), np.zeros((len(p), 3))] for p in Pw]
    wg = plan.wg
    rep = dict(wheels=[], moved=0.0, parts={}, static=0, axle_parts=0)

    def put(c, bw):
        for k in range(4):
            J[c.part][c.verts, k] = bw[k][0] if k < len(bw) else 0
            W[c.part][c.verts, k] = bw[k][1] if k < len(bw) else 0.0
    # named parts first (doors, hood, ...): the bone that moves that part of the game vehicle
    for c in comps:
        if c.kind or not c.role or c.role in ('wheel', 'caliper'):
            continue
        Q = Pw[c.part][c.verts]
        b = vr.bone_for(c.role, (Q.min(0) + Q.max(0)) / 2)
        if b is not None:
            put(c, [(b, 1.0)])
            rep['parts'][c.role] = rep['parts'].get(c.role, 0) + 1

    def axis_now(k):
        m = wg[k].main
        Q = Pw[m.part][m.verts]
        lo, hi = Q.min(0), Q.max(0)
        return (lo + hi) / 2, max(hi[1] - lo[1], hi[2] - lo[2]) / 2
    # where each axle is now, before anything moves
    before = {}
    for ua, axle in enumerate(plan.user_axles):
        cs = [axis_now(k) for k in axle]
        before[ua] = (np.mean([c for c, r in cs], 0), float(np.mean([r for c, r in cs])))
    shifts = {}
    keep = st.get('wheel_fit') == 'model'
    rep['bone_moves'] = {}
    if keep:
        # the wheels stay where the model has them; the game's wheel assemblies (hub + wheel bones) move to them instead
        per = {}
        for k, g in enumerate(wg):
            bw = plan.bones[k]
            if bw is None:
                rep['static'] += 1
                continue
            c0, rad = axis_now(k)
            for c in g.comps:
                put(c, bw)
            for c in comps:
                if c.kind == 'caliper' and c.group == k:
                    hubs = [(next(x for x in vr.wheels if x.bone == b).hub, f) for b, f in bw]
                    put(c, [(h if h is not None and h >= 0 else 0, f) for h, f in hubs])
            for b, f in bw:
                per.setdefault(b, []).append((c0, len(bw) > 1))
            w = plan.game_wheel(k)
            rep['wheels'].append(dict(bone=bw[0][0], bones=[b for b, f in bw], front=w.front, side='middle (on both wheel bones)' if len(bw) > 1 else w.side,
                                      steers=w.steers, pieces=len(g.comps), shift=0.0, size=1.0, radius=round(rad, 1), game_radius=round(w.radius, 1)))
        for b, lst in per.items():
            w = next(x for x in vr.wheels if x.bone == b)
            c = np.mean([c0 for c0, mid in lst], 0)
            dv = c - w.centre
            if all(mid for c0, mid in lst):
                dv[0] = 0.0   # a middle wheel on both bones: only up/down and forward/back
            rep['bone_moves'][int(b)] = [float(x) for x in dv]
        covered = {b for x in rep['wheels'] for b in x['bones']}
        rep['missing'] = ['%s %s' % ('front' if w.front else 'rear', w.side) for w in vr.wheels if w.bone not in covered]
        return J, W, rep
    for k, g in enumerate(wg):
        bw = plan.bones[k]
        if bw is None:
            rep['static'] += 1
            continue
        w = plan.game_wheel(k)
        c0, rad = axis_now(k)
        k_size = w.radius / rad if st.get('wheel_size', 'game') == 'game' and rad > 1e-6 else 1.0
        target = np.array([c0[0], w.centre[1], w.centre[2]])
        for c in g.comps:
            Pw[c.part][c.verts] = (Pw[c.part][c.verts] - c0) * k_size + target
            put(c, bw)
        hubs = [(next(x for x in vr.wheels if x.bone == b).hub, f) for b, f in bw]
        hubs = [(h if h is not None and h >= 0 else 0, f) for h, f in hubs]
        for c in comps:
            if c.kind == 'caliper' and c.group == k:
                Pw[c.part][c.verts] = (Pw[c.part][c.verts] - c0) * k_size + target
                put(c, hubs)
        moved = float(np.linalg.norm(target - c0))
        rep['moved'] = max(rep['moved'], moved)
        shifts.setdefault(plan.axle_of[k], []).append(target - c0)
        rep['wheels'].append(dict(bone=bw[0][0], bones=[b for b, f in bw], front=w.front, side='middle (on both wheel bones)' if len(bw) > 1 else w.side,
                                  steers=w.steers, pieces=len(g.comps), shift=round(moved, 1), size=round(k_size, 3),
                                  radius=round(rad * k_size, 1), game_radius=round(w.radius, 1)))
    # parts along an axle (axle rods, hubs, suspension right at the wheels) go with the wheels
    for ua, sh in shifts.items():
        if ua is None:
            continue
        d = np.mean(sh, 0)
        d[0] = 0.0
        if np.linalg.norm(d) < 0.5:
            continue
        ctr, R = before[ua]
        xs = [axis_now(k)[0][0] for k in plan.user_axles[ua]]
        for c in comps:
            if c.kind is not None or c.role:
                continue
            Q = Pw[c.part][c.verts]
            lo, hi = Q.min(0), Q.max(0)
            cc = (lo + hi) / 2
            near_line = np.hypot(cc[1] - ctr[1], cc[2] - ctr[2]) < 0.5 * R and max(hi[1] - lo[1], hi[2] - lo[2]) < 1.2 * R
            if near_line and lo[0] >= min(xs) - 1.5 * R and hi[0] <= max(xs) + 1.5 * R:
                Pw[c.part][c.verts] = Q + d
                c.kind = 'axle'
                rep['axle_parts'] += 1
    covered = {b for x in rep['wheels'] for b in x['bones']}
    rep['missing'] = ['%s %s' % ('front' if w.front else 'rear', w.side) for w in vr.wheels if w.bone not in covered]
    return J, W, rep
