"""Characters (Frank, survivors, psychopaths, zombies): put a .glb person on the game character's skeleton.

The game's people share one skeleton in a T-pose (rig.py). A new body gets its bone weights from the game model itself:
every vertex takes the weights of the nearest points of the original body (blended over the few nearest), so it bends
where the original bends. That needs the .glb in about the same pose: standing, arms straight out to the sides
(T-pose). Its own rig, if any, is only used to read it in its rest pose.

Display groups: a character's pieces belong to numbered groups that the game switches on and off per character (zombie
clothing variants, Frank's hand poses for each held item and his first-person head, survivors' look variants). A piece
in a group that's off isn't drawn. So each region of the new body is drawn in every group the original used for that
region: whichever variant the game picks, exactly that copy is on. The copies share their vertices (no extra memory).

Level of detail: zombies have near / middle / far versions (piece LOD mask 1 / 2 / 4); the new body gets the same, thinned.
"""
import numpy as np

from . import rig


def _area(P, T):
    if len(T) == 0:
        return np.zeros(0)
    return np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1) / 2


class Body:
    """The game character, read for weights, regions and LODs."""

    def __init__(self, model, extra=()):
        """extra: [(model, groups)]: more models on the same skeleton that lend their weights (Frank's clothes: his base
        model has only his head and hands); the new body there is drawn in the given groups."""
        self.model = model
        self.bones = rig.read_bones(model)
        P, _, _ = model.vertices()
        J, W = rig.vertex_weights(model)
        idx = model.indices()
        lods = sorted({p['flags'][1] for p in model.pieces})
        self.lods = lods                    # LOD masks used ([15] = one level; [1, 2, 4] = zombies)
        near = lods[0]
        vmask = np.zeros(len(P), bool)
        vgroup = np.full(len(P), -1, np.int64)
        tris, tgroup = [], []
        self.group_area, self.group_lo, self.group_hi = {}, {}, {}
        for p in model.pieces:
            if p['flags'][1] != near:
                continue
            t = np.array(model.triangles(p, idx), np.int64).reshape(-1, 3)
            if not len(t):
                continue
            g = p['group']
            vmask[t.ravel()] = True
            vgroup[t.ravel()] = g
            tris.append(t)
            tgroup.append(np.full(len(t), g))
            a = float(_area(P, t).sum())
            Q = P[t.ravel()]
            self.group_area[g] = self.group_area.get(g, 0.0) + a
            self.group_lo[g] = np.minimum(self.group_lo.get(g, Q.min(0)), Q.min(0))
            self.group_hi[g] = np.maximum(self.group_hi.get(g, Q.max(0)), Q.max(0))
        self.P, self.J, self.W = P[vmask], J[vmask], W[vmask]
        sP, sT = [P], [np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)]
        self.vgroup = vgroup[vmask]
        nb = len(self.bones)
        for em, groups in extra:
            if em.n_bones != nb or em.vb1_size != em.n_verts * 28:
                continue
            EP, _, _ = em.vertices()
            EJ, EW = rig.vertex_weights(em)
            g0 = groups[0]
            self.P = np.r_[self.P, EP]
            self.J = np.r_[self.J, EJ]
            self.W = np.r_[self.W, EW]
            self.vgroup = np.r_[self.vgroup, np.full(len(EP), g0)]
            et = [np.array(em.triangles(p, em.indices()), np.int64).reshape(-1, 3) for p in em.pieces]
            near_e = min(p['flags'][1] for p in em.pieces)
            et_near = [t for p, t in zip(em.pieces, et) if p['flags'][1] == near_e and len(t)]
            if et_near:
                sT.append(np.concatenate(et_near) + sum(len(x) for x in sP))
                sP.append(EP)
            a = float(sum(_area(EP, t).sum() for t in et if len(t)))
            self.group_area[g0] = self.group_area.get(g0, 0.0) + a
            self.group_lo[g0] = np.minimum(self.group_lo.get(g0, EP.min(0)), EP.min(0))
            self.group_hi[g0] = np.maximum(self.group_hi.get(g0, EP.max(0)), EP.max(0))
        self.lo, self.hi = self.P.min(0), self.P.max(0)
        self.S = surface_samples(np.concatenate(sP), np.concatenate(sT))
        self.regions = self._regions()

    def _regions(self):
        """Groups that cover the same part of the body (variants of it) -> one region. -> {group: tuple of groups}."""
        gs = sorted(self.group_area)
        par = {g: g for g in gs}

        def find(g):
            while par[g] != g:
                par[g] = par[par[g]]
                g = par[g]
            return g
        for i, a in enumerate(gs):
            for b in gs[i + 1:]:
                lo = np.maximum(self.group_lo[a], self.group_lo[b])
                hi = np.minimum(self.group_hi[a], self.group_hi[b])
                inter = np.prod(np.maximum(hi - lo, 0))
                va = np.prod(np.maximum(self.group_hi[a] - self.group_lo[a], 1e-6))
                vb = np.prod(np.maximum(self.group_hi[b] - self.group_lo[b], 1e-6))
                if inter / (va + vb - inter) > 0.5:
                    par[find(a)] = find(b)
        out = {}
        for g in gs:
            out.setdefault(find(g), []).append(g)
        return {g: tuple(sorted(out[find(g)])) for g in gs}

    def core_vertices(self):
        """Vertices of regions big enough to be body parts (not small accessories that some variants leave out)."""
        tot = sum(self.group_area.values()) or 1.0
        reg_area = {}
        for g, r in self.regions.items():
            reg_area[r] = reg_area.get(r, 0.0) + self.group_area[g]
        ok = {r for r, a in reg_area.items() if a / tot >= 0.012}
        return np.array([self.regions[g] in ok for g in self.vgroup])


def surface_samples(P, T, n=40000, seed=3):
    """Points spread evenly over the surface (by area), so measurements don't depend on how dense the mesh is, with the
    normal of the triangle each lies on. -> (n x 6: point, normal)."""
    if len(T) == 0 or len(P) == 0:
        return np.c_[P, np.zeros_like(P)]
    a = _area(P, T)
    if a.sum() <= 0:
        return np.c_[P, np.zeros_like(P)]
    rng = np.random.default_rng(seed)
    t = rng.choice(len(T), n, p=a / a.sum())
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    A, B, C = P[T[t, 0]], P[T[t, 1]], P[T[t, 2]]
    fn = np.cross(B - A, C - A)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    return np.c_[A + u[:, None] * (B - A) + v[:, None] * (C - A), fn]


def nearest(A, B, k, chunk=1500):
    """For each row of A the k nearest rows of B: (index n x k, distance n x k). Plain blocks: fine for ~60k x 25k."""
    k = min(k, len(B))
    Bf = B.astype(np.float32)
    bb = (Bf ** 2).sum(1)
    I = np.empty((len(A), k), np.int64)
    D = np.empty((len(A), k), np.float32)
    for s in range(0, len(A), chunk):
        a = A[s:s + chunk].astype(np.float32)
        d2 = (a ** 2).sum(1)[:, None] + bb[None, :] - 2 * a @ Bf.T
        part = np.argpartition(d2, k - 1, axis=1)[:, :k] if k < len(B) else np.tile(np.arange(len(B)), (len(a), 1))
        dd = np.take_along_axis(d2, part, 1)
        o = np.argsort(dd, 1)
        I[s:s + chunk] = np.take_along_axis(part, o, 1)
        D[s:s + chunk] = np.sqrt(np.maximum(np.take_along_axis(dd, o, 1), 0))
    return I, D


FOLD = (19, 21, 22)          # jaw, right eye, left eye -> head (10)
HEAD = 10


def rigid_head(body, P, T, J, W, offsets=None, force_head=None, force_body=None):
    """Separate pieces of the mesh that sit on the head (the head itself when it's its own piece, hair, a hat,
    glasses, ears) move as one with the head bone. Weights blended from the game's body split a big chin or a long
    face between head and chest, which tears it whenever the head moves. A piece qualifies when it's mostly tied to
    the head already, starts above the chest and is no wider than about the shoulders. Pieces are looked for within
    each of the model's meshes (offsets: where each mesh's vertices start): a head often shares its neck edge with the
    shirt's collar, which would join everything. Changes J, W in place. -> number of pieces made rigid."""
    from .vehicle import components
    if len(T) == 0 or len(body.bones) <= HEAD:
        return 0
    offsets = list(offsets) if offsets is not None else [0, len(P)]
    tol = float(np.ptp(P, 0).max()) * 2e-5
    lab = np.zeros(len(P), np.int64)
    base = 0
    for a, b in zip(offsets[:-1], offsets[1:]):
        t = T[(T >= a).all(1) & (T < b).all(1)] - a
        if b > a:
            lab[a:b] = components(P[a:b], t, tol) + base
            base = lab[a:b].max() + 1
    chest_y = body.bones[8].pivot[1]
    sh = abs(body.bones[12].pivot[0])
    h = (W * (J == HEAD)).sum(1)
    J0, W0 = J.copy(), W.copy()
    rigid = np.zeros(len(P), bool)
    n = 0
    for c in np.unique(lab):
        m = lab == c
        Q = P[m]
        if force_body is not None and force_body[m].any():
            continue                    # the Parts list says: part of the body
        forced = force_head is not None and force_head[m].any()
        if not forced and (m.sum() < 4 or Q[:, 1].min() < chest_y or np.ptp(Q[:, 0]) > 2.5 * sh or h[m].mean() < 0.45):
            continue
        J[m] = 0
        J[m, 0] = HEAD
        W[m] = 0
        W[m, 0] = 1.0
        rigid |= m
        n += 1
    if n:
        _seal_seams(P, J, W, J0, W0, rigid, tol)
    return n


def _seal_seams(P, J, W, J0, W0, rigid, tol):
    """Where a rigid piece's edge sits on another piece (a head's neck edge on the collar: the same positions), the
    edge keeps the other piece's weights so the seam can't open, blending into the rigid head a little way up."""
    if rigid.all() or not rigid.any():
        return
    key = np.round(P / max(tol * 5, 1e-6)).astype(np.int64)
    other = {}
    for i in np.flatnonzero(~rigid):
        other.setdefault(tuple(key[i]), i)
    seam = [(i, other[tuple(key[i])]) for i in np.flatnonzero(rigid) if tuple(key[i]) in other]
    if not seam:
        return
    nb = int(max(J.max(), J0.max())) + 1

    def dense(Jx, Wx, idx):
        out = np.zeros((len(idx), nb))
        for k in range(Jx.shape[1]):
            np.add.at(out, (np.arange(len(idx)), Jx[idx, k]), Wx[idx, k])
        return out
    si = np.array([a for a, _ in seam])
    so = np.array([b for _, b in seam])
    seamW = dense(J0, W0, so)                  # the other piece's weights at each seam point
    # feather: rigid vertices near the seam blend from the seam's weights to the head over a short distance
    R = np.flatnonzero(rigid)
    span = float(np.ptp(P[R, 1])) if len(R) else 1.0
    band = 0.18 * span
    I, D = nearest(P[R], P[si], 1)
    t = np.clip(D[:, 0] / max(band, 1e-9), 0, 1)
    sel = t < 1
    if not sel.any():
        return
    rows = R[sel]
    head = np.zeros((sel.sum(), nb))
    head[:, HEAD] = 1.0
    mix = (1 - t[sel])[:, None] * seamW[I[sel, 0]] + t[sel][:, None] * head
    top = np.argsort(-mix, 1)[:, :J.shape[1]]
    tw = np.take_along_axis(mix, top, 1)
    tw /= np.maximum(tw.sum(1, keepdims=True), 1e-12)
    J[rows] = top
    W[rows] = tw


def transfer_weights(body, P, k=6, T=None, smooth=2):
    """Bone weights for points P from the k nearest original vertices (inverse-distance blend), then smoothed along
    the new mesh's edges (T: triangles over P) so single vertices can't pick up a stray bone. -> (J n x 4, W n x 4)."""
    I, D = nearest(P, body.P, k)
    w = 1.0 / np.maximum(D, 0.5) ** 2
    nb = len(body.bones)
    acc = np.zeros((len(P), nb))
    for j in range(k):
        Jj, Wj = body.J[I[:, j]], body.W[I[:, j]]
        for s in range(4):
            np.add.at(acc, (np.arange(len(P)), Jj[:, s]), Wj[:, s] * w[:, j])
    # jaw and eyes: the game moves them (talking, groans, looking around), but a new head's mouth and eyes are never
    # where the game's are, so whatever lands there would wobble: they move with the head instead
    for b in FOLD:
        if b < nb:
            acc[:, 10] += acc[:, b]
            acc[:, b] = 0
    acc /= np.maximum(acc.sum(1, keepdims=True), 1e-12)
    if T is not None and len(T) and smooth:
        # vertices at the same spot (UV seams) are one: weld, then average with the neighbours (half own, half theirs)
        q = np.round(P * 100).astype(np.int64)
        _, wid = np.unique(q, axis=0, return_inverse=True)
        wid = wid.reshape(-1)
        m = wid.max() + 1
        e = np.r_[np.c_[T[:, 0], T[:, 1]], np.c_[T[:, 1], T[:, 2]], np.c_[T[:, 2], T[:, 0]]]
        e = np.unique(np.sort(wid[e], 1), axis=0)
        e = e[e[:, 0] != e[:, 1]]
        Aw = np.zeros((m, acc.shape[1]))
        np.add.at(Aw, wid, acc)
        cnt = np.bincount(wid, minlength=m)[:, None].astype(float)
        Aw /= np.maximum(cnt, 1)
        deg = np.bincount(e.ravel(), minlength=m)[:, None].astype(float)
        for _ in range(smooth):
            nb_sum = np.zeros_like(Aw)
            np.add.at(nb_sum, e[:, 0], Aw[e[:, 1]])
            np.add.at(nb_sum, e[:, 1], Aw[e[:, 0]])
            Aw = np.where(deg > 0, 0.5 * Aw + 0.5 * nb_sum / np.maximum(deg, 1), Aw)
        acc = Aw[wid]
    top = np.argsort(-acc, axis=1)[:, :4]
    tw = np.take_along_axis(acc, top, 1)
    tw[tw < 0.04 * tw[:, :1]] = 0          # drop tiny influences
    return top, tw / np.maximum(tw.sum(1, keepdims=True), 1e-12)


def regions_for(body, P, T):
    """Region (tuple of groups) of every triangle: the region of the nearest body-part vertex to its centre."""
    core = body.core_vertices()
    src = body.P[core] if core.any() else body.P
    grp = body.vgroup[core] if core.any() else body.vgroup
    C = P[T].mean(1)
    I, _ = nearest(C, src, 1)
    return [body.regions[int(g)] for g in grp[I[:, 0]]]


def fit(P_all, body, st):
    """Scale and offset that stand the .glb where the game character stands: same height, feet on the same floor,
    centred sideways and front-to-back. -> (s, t)."""
    lo, hi = P_all.min(0), P_all.max(0)
    h = max(hi[1] - lo[1], 1e-9)
    s = (body.hi[1] - body.lo[1]) / h
    if st.size == 'real':
        s = 100.0
    elif st.size == 'fit':
        s = float(np.min((body.hi - body.lo) / np.maximum(hi - lo, 1e-9)))
    s *= st.scale
    c = (lo + hi) / 2
    t = np.array([(body.lo[0] + body.hi[0]) / 2 - s * c[0], body.lo[1] - s * lo[1], (body.lo[2] + body.hi[2]) / 2 - s * c[2]])
    if st.place == 'center':
        t[1] = (body.lo[1] + body.hi[1]) / 2 - s * c[1]
    elif st.place == 'origin':
        t = np.zeros(3)
    return s, t


def pose_check(body, P, J, W):
    """How much a T-pose difference shows: share of the .glb's arm-height vertices (beyond the shoulders) that got
    leg or body weights."""
    shoulder = abs(body.bones[12].pivot[0]) if len(body.bones) > 12 else 20
    far = np.abs(P[:, 0]) > shoulder * 1.6
    if not far.any():
        return 0.0
    arm = np.isin(J, [12, 13, 14, 16, 17, 18, 20]) & (W > 0.3)
    return float(1.0 - arm.any(1)[far].mean())


# ----- pose: models in an A-pose (arms down at an angle) are straightened to the game's T-pose
# Each arm (and leg) of the game body is turned through a range of angles; the angle where the turned game body lies
# closest to the .glb (both ways: its points to the .glb and the .glb's to it, on that side) is the .glb's pose. The
# weights are then taken from the game body in that pose, and the .glb is turned back into the T-pose with them.
ARM = {'right': (12, 1.0), 'left': (16, -1.0)}      # upper arm bone, sign of "down" around Z
LEG = {'right': (1, -1.0), 'left': (4, 1.0)}        # thigh bone, sign of "outwards" around Z


def _limb_rot(kind, sign, a, f, dy=0.0):
    """The limb turned; for arms optionally around a shoulder point dy higher / lower than the game's (the model's own
    shoulder: other proportions), as a 4x4 in the bone's frame (row vectors: v' = (v - p) R + p)."""
    R = rig.rot_z(sign * a) @ rig.rot_y(f) if kind == 'arm' else rig.rot_z(sign * a)
    if not dy:
        return R
    M = np.eye(4)
    M[:3, :3] = R
    p = np.array([0.0, dy, 0.0])
    M[3, :3] = p - p @ R
    return M


def _sample(X, n, seed=1):
    if len(X) <= n:
        return X
    return X[np.random.default_rng(seed).choice(len(X), n, replace=False)]


def _score(A, B):
    if len(A) == 0 or len(B) == 0:
        return 1e9
    _, d1 = nearest(A, B, 1)
    _, d2 = nearest(B, A, 1)
    return float(np.mean(np.minimum(d1[:, 0], 40))) + float(np.mean(np.minimum(d2[:, 0], 40)))


def _arm_direction(P, body, x_sign):
    """Down / forward angle of one arm from its shape: the long axis of the points beyond the body's shoulders, above
    the waist. None if there's too little to see."""
    hip = body.bones[0].pivot[1]
    sh = abs(body.bones[12].pivot[0]) if len(body.bones) > 12 else 20.0
    x = P[:, 0] * x_sign
    m = (x > 1.4 * sh) & (P[:, 1] > hip - 0.1 * (body.hi[1] - body.lo[1]))
    if m.sum() < 30:
        return None
    Q = P[m]
    c = Q.mean(0)
    _, _, vt = np.linalg.svd(Q - c, full_matrices=False)
    d = vt[0] * (1 if vt[0][0] * x_sign > 0 else -1)
    out = abs(d[0])
    if out < 0.2:                               # hanging straight down or a blob: can't tell
        return None
    down = float(np.degrees(np.arctan2(-d[1], out)))
    fwd = float(np.degrees(np.arctan2(d[2], out)))
    return round(down / 2) * 2, round(fwd / 5) * 5


def estimate_pose(body, P):
    """-> ({bone: 3x3}, {'right arm': (down, forward), ...}) for the .glb points P (already scaled and placed).
    The shoulder may also sit higher or lower than the game's (other proportions): that's searched too (the collarbone
    turned up / down), so it doesn't show up as a wrong arm angle."""
    pose, found = {}, {}
    hip_y = body.bones[0].pivot[1]
    U = _sample(P, 2000)
    Bsub = np.arange(len(body.P))
    if len(Bsub) > 3000:
        Bsub = np.random.default_rng(2).choice(len(body.P), 3000, replace=False)
    BP, BJ, BW = body.P[Bsub], body.J[Bsub], body.W[Bsub]

    def posed(extra, sel):
        return rig.skin(BP[sel], BJ[sel], BW[sel], body.bones, rig.world_matrices(body.bones, extra))

    for side, x_sign in (('right', -1.0), ('left', 1.0)):
        for kind, table, ys in (('arm', ARM, (hip_y, 1e9)), ('leg', LEG, (-1e9, hip_y))):
            bone, sgn = table[side]
            Us = U[(U[:, 0] * x_sign > 0) & (U[:, 1] > ys[0]) & (U[:, 1] < ys[1])]
            sel_b = (BP[:, 0] * x_sign > 0) & (BP[:, 1] > ys[0] - (60 if kind == 'arm' else 0)) & (BP[:, 1] < ys[1] + (0 if kind == 'arm' else 30))
            if len(Us) < 20 or sel_b.sum() < 20:
                continue

            def trial(a, f, dy):
                ex = dict(pose)
                ex[bone] = _limb_rot(kind, sgn, a, f, dy)
                return _score(Us, posed(ex, sel_b))
            downs = np.arange(-10, 81, 10) if kind == 'arm' else np.arange(-10, 31, 5)
            fwds = np.arange(-30, 31, 15) if kind == 'arm' else [0]
            span = body.hi[1] - body.lo[1]
            dys = np.linspace(-0.07, 0.07, 5) * span if kind == 'arm' else (0.0,)
            guess = _arm_direction(P, body, x_sign) if kind == 'arm' else None
            if guess is not None:
                # the arm's own direction (its long axis beyond the body): only fine-tuned below, so an arm longer
                # or shorter than the game's can't pull the search somewhere else
                downs, fwds = [guess[0]], [guess[1]]
            best = min((trial(a, f, d), a, f, d) for a in downs for f in fwds for d in dys)
            _, a0, f0, d0 = best
            step = 0.0175 * span
            for a in np.arange(a0 - 8, a0 + 9, 2):
                for f in (np.arange(f0 - 10, f0 + 11, 5) if kind == 'arm' else [0]):
                    for d in ((d0 - step, d0, d0 + step) if kind == 'arm' else (0.0,)):
                        sc = trial(a, f, d)
                        if sc < best[0]:
                            best = (sc, a, f, d)
            _, a, f, d = best
            # only a clear A-pose is straightened (small angles are proportions, not pose)
            if (kind == 'arm' and (abs(a) >= 15 or abs(f) >= 15)) or (kind == 'leg' and abs(a) >= 10):
                pose[bone] = _limb_rot(kind, sgn, a, f, d)
            found['%s %s' % (side, kind)] = (float(a), float(f))
    return pose, found


def repose(body, P, N, T, pose, k=6):
    """Weights from the game body turned into the .glb's pose, then the .glb turned back into the T-pose with them.
    -> (J, W, P in T-pose, N in T-pose)."""
    posedW = rig.world_matrices(body.bones, pose)
    PP = rig.skin(body.P, body.J, body.W, body.bones, posedW)

    class _B:
        pass
    b2 = _B()
    b2.P, b2.J, b2.W, b2.bones = PP, body.J, body.W, body.bones
    J, W = transfer_weights(b2, P, k=k, T=T)
    M = np.zeros((len(P), 4, 4))
    for i, b in enumerate(body.bones):
        Mb = b.inv @ posedW[i]
        for s in range(4):
            sel = (J[:, s] == i) & (W[:, s] > 0)
            if sel.any():
                M[sel] += W[sel, s, None, None] * Mb
    Minv = np.linalg.inv(M)
    Pb = np.einsum('ni,nij->nj', np.c_[P, np.ones(len(P))], Minv)[:, :3]
    Nb = np.einsum('ni,nji->nj', N, M[:, :3, :3])      # normals: n @ M3^T
    Nb /= np.maximum(np.linalg.norm(Nb, axis=1, keepdims=True), 1e-9)
    return J, W, Pb, Nb


# ----- proportions: the game's animations set the hip height and reach the feet and hands with IK, and the joints bend
# where the game skeleton has them. A model with other proportions (short legs, long arms, wide body) is fitted onto the
# skeleton: its crotch, knees and ankles, shoulder line, elbows and wrists are moved onto the game's, piece by piece.

class Landmarks:
    __slots__ = ('floor', 'top', 'crotch', 'crotch_found', 'arm_y', 'arm_z', 'arm_r', 'pit', 'tip', 'leg_x', 'cx', 'cz')

    def __repr__(self):
        return 'Landmarks(%s)' % ', '.join('%s=%s' % (k, np.round(np.asarray(getattr(self, k), float), 1)) for k in self.__slots__)


def landmarks(SN, crotch_frac=None):
    """Measure a standing T-pose body from surface samples SN (point + normal; Y up, X sideways, centred). pit / tip /
    leg_x / arm_* are per side: index 0 = right (-X), 1 = left (+X)."""
    L = Landmarks()
    S, Nn = SN[:, :3], SN[:, 3:]
    y, x = S[:, 1], S[:, 0]
    L.floor, L.top = float(y.min()), float(y.max())
    H = L.top - L.floor
    if not H > 1e-6:
        raise ValueError('your model has no height (it is flat or its triangles are broken), so it can\'t be fitted to a character')
    L.cx = float((x.min() + x.max()) / 2)
    L.cz = float(np.median(S[:, 2]))
    ax = x - L.cx
    # crotch: coming down from the chest, the first height where nothing crosses the middle (the legs part). Legs that
    # touch lower down (knees) don't matter; a skirt or long coat gives its hem (the legs can't be seen above it).
    # Where thighs touch, the surface in the middle faces sideways: that isn't the body crossing, so it doesn't count.
    strip = (np.abs(ax) < 0.015 * H) & (np.abs(Nn[:, 0]) < 0.6)
    step = 0.005 * H
    L.crotch_found = False
    L.crotch = L.floor + (crotch_frac if crotch_frac is not None else 0.47) * H
    for yb in np.arange(L.floor + 0.7 * H, L.floor + 0.12 * H, -step):
        if not (strip & (y >= yb - step) & (y < yb)).any() and not (strip & (y >= yb - 2 * step) & (y < yb - step)).any():
            L.crotch, L.crotch_found = float(yb), True
            break
    # arms (T-pose): the outer part of each side, its height and thickness; armpit = how far the body below reaches
    L.pit, L.tip, L.arm_y, L.arm_z, L.arm_r, L.leg_x = [], [], [], [], [], []
    up = y > L.crotch
    for sgn in (-1.0, 1.0):
        side = up & (ax * sgn > 0)
        sx = ax[side] * sgn
        tip = float(np.percentile(sx, 99.9)) if len(sx) else 0.0
        band = side & (ax * sgn > 0.55 * tip) & (ax * sgn < 0.8 * tip)
        if band.sum() < 10:
            band = side & (ax * sgn > 0.5 * tip)
        arm_y = float(np.median(y[band])) if band.any() else L.crotch + 0.4 * H
        arm_z = float(np.median(S[band, 2])) if band.any() else L.cz
        r = float(np.percentile(y[band], 90) - np.percentile(y[band], 10)) / 2 if band.sum() > 10 else 0.03 * H
        # armpit: walking in along the arm from the hand, the first slice that reaches well below the arm (the body)
        low = arm_y - max(3.0 * r, 0.08 * H)
        pit = 0.35 * tip
        for xb in np.arange(tip, 0.0, -0.01 * H):
            sl = side & (ax * sgn >= xb - 0.01 * H) & (ax * sgn < xb)
            if sl.sum() >= 3 and np.percentile(y[sl], 3) < low:
                pit = float(xb)
                break
        pit = min(max(pit, 0.12 * tip), 0.75 * tip)
        legs = (y < L.crotch - 0.1 * (L.crotch - L.floor)) & (y > L.floor + 0.3 * (L.crotch - L.floor)) & (ax * sgn > 0)
        leg_x = float(np.median(ax[legs] * sgn)) if legs.sum() > 10 else 0.06 * H
        L.pit.append(pit), L.tip.append(tip), L.arm_y.append(arm_y), L.arm_z.append(arm_z), L.arm_r.append(r)
        L.leg_x.append(leg_x)
    return L


def game_landmarks(body):
    """The game body's landmarks, measured like the .glb's (so the same body maps onto itself unchanged); the crotch
    falls back to the skeleton's hips if the mesh doesn't show it."""
    L = landmarks(body.S)
    hip = body.bones[0].pivot[1]
    est = float(hip - 0.18 * (hip - L.floor))
    if not L.crotch_found or abs(L.crotch - est) > 0.25 * (hip - L.floor):
        L.crotch = est
    L.crotch_found = True
    return L


def _pl(v, xs, ys):
    """Piecewise-linear map through (xs -> ys), straight on beyond the ends."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    out = np.interp(v, xs, ys)
    lo, hi = v < xs[0], v > xs[-1]
    out[lo] = ys[0] + (v[lo] - xs[0]) * (ys[1] - ys[0]) / max(xs[1] - xs[0], 1e-9)
    out[hi] = ys[-1] + (v[hi] - xs[-1]) * (ys[-1] - ys[-2]) / max(xs[-1] - xs[-2], 1e-9)
    return out


class Proportions:
    """Moves a T-pose body (already standing where the game character stands) onto the game skeleton: legs stretched or
    shortened so crotch, knees and ankles meet the game's (the game's animations set the hip height), the body above
    scaled to the rest of the game character's height, the shoulder line onto the game's, and each arm stretched piece
    by piece so elbow and wrist land on the game's joints."""

    def __init__(self, Lu, Lg, bones, arms=True):
        """arms=False: legs and height only (before the arms are known to be straight out)."""
        self.Lu, self.Lg, self.with_arms = Lu, Lg, arms
        u, g = Lu, Lg
        self.s = (g.top - g.crotch) / max(u.top - u.crotch, 1e-9)            # body above the legs
        self.legs = (g.crotch - g.floor) / max(u.crotch - u.floor, 1e-9)     # leg length
        self.arms = []
        for k, sgn in enumerate((-1.0, 1.0)):
            ub, fb, hb = (12, 13, 14) if sgn < 0 else (16, 17, 18)
            elbow, wrist = abs(bones[fb].pivot[0]), abs(bones[hb].pivot[0])
            span_g = max(g.tip[k] - g.pit[k], 1e-9)
            fe, fw = (elbow - g.pit[k]) / span_g, (wrist - g.pit[k]) / span_g
            span_u = max(u.tip[k] - u.pit[k], 1e-9)
            xs = [u.pit[k], u.pit[k] + fe * span_u, u.pit[k] + fw * span_u, u.tip[k]]
            ys = [self.s * u.pit[k], elbow, wrist, g.tip[k]]
            if not (ys[1] > ys[0] + 1e-3):            # a body wider than the game's elbows: keep the order
                ys[1] = ys[0] + 0.5 * (ys[2] - ys[0])
            self.arms.append((xs, ys))
        self.arm_len = [(a[1][-1] - a[1][0]) / max(a[0][-1] - a[0][0], 1e-9) for a in self.arms]

    def ymap(self, y):
        u, g = self.Lu, self.Lg
        ay_u, ay_g = float(np.mean(u.arm_y)), float(np.mean(g.arm_y))
        if not self.with_arms or not (u.crotch < ay_u < u.top and g.crotch < ay_g < g.top):
            return _pl(y, [u.floor, u.crotch, u.top], [g.floor, g.crotch, g.top])
        return _pl(y, [u.floor, u.crotch, ay_u, u.top], [g.floor, g.crotch, ay_g, g.top])

    def __call__(self, P):
        u, g, s = self.Lu, self.Lg, self.s
        x, y, z = P[:, 0] - u.cx, P[:, 1], P[:, 2] - u.cz
        X, Y, Z = s * x, self.ymap(y), s * z
        legs = y < u.crotch
        # legs: centred on the game's hips (ramped in below the crotch, so the body above isn't torn)
        ramp = np.clip((u.crotch - y) / max(0.25 * (u.crotch - u.floor), 1e-9), 0, 1)
        for k, sgn in enumerate((-1.0, 1.0)):
            m = legs & (x * sgn > 0)
            X[m] += sgn * (g.leg_x[k] - s * u.leg_x[k]) * ramp[m]
        # arms: piece by piece along X, and onto the game's arm line in Z
        for k, sgn in enumerate((-1.0, 1.0) if self.with_arms else ()):
            xs, ys = self.arms[k]
            m = ~legs & (x * sgn > u.pit[k])
            X[m] = sgn * _pl(x[m] * sgn, xs, ys)
            r = np.clip((x[m] * sgn - u.pit[k]) / max(0.3 * (xs[1] - xs[0]), 1e-9), 0, 1)
            Z[m] += ((g.arm_z[k] - g.cz) - s * (u.arm_z[k] - u.cz)) * r
        return np.c_[X + g.cx, Y, Z + g.cz]

    def normals(self, P, N, eps=0.05):
        """Normals through the warp (inverse transpose of its local stretch, measured by small steps)."""
        base = self(P)
        A = np.stack([(self(P + eps * np.eye(3)[i]) - base) / eps for i in range(3)], 1)   # n x 3(in) x 3(out)
        Nn = np.einsum('ni,nij->nj', N, np.linalg.inv(A).transpose(0, 2, 1))
        return Nn / np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-9)

    def describe(self):
        u = self.Lu
        out = ['legs x%.2f' % self.legs, 'body above x%.2f' % self.s]
        if self.with_arms:
            out.append('arms x%.2f / x%.2f' % tuple(self.arm_len))
        if not u.crotch_found:
            out.append('(couldn\'t see where the legs part: assumed)')
        return ', '.join(out)
