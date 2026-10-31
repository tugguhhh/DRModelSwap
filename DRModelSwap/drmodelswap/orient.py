"""Turning a model the right way automatically: of the 24 ways a model can lie (each axis-aligned quarter turn), the
one whose shape lies best over the game's original at the same size, plus clues from the kind of model:
- vehicles: seats facing forward (a backrest behind the cushion), wheels underneath;
- characters: standing up, toes pointing forward;
- objects: just the shape (a gun's grip down and its barrel forward, like the original).
The person can always turn it by hand instead (Turn / Stand it up)."""
import itertools
import numpy as np

from . import character, seats

N = 20


def rotations():
    """The 24 rotations that map axes onto axes (proper rotations, det +1)."""
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            R = np.zeros((3, 3))
            for i in range(3):
                R[i, perm[i]] = signs[i]
            if np.linalg.det(R) > 0:
                out.append(R)
    return out


def _vox(S, lo, hi):
    g = np.zeros((N, N, N), bool)
    idx = np.floor((S - lo) / np.maximum(hi - lo, 1e-9) * N).astype(int)
    ok = ((idx >= 0) & (idx < N)).all(1)
    g[tuple(idx[ok].T)] = True
    # a little thicker, so thin shells still meet
    d = g.copy()
    for ax in range(3):
        d |= np.roll(g, 1, ax) | np.roll(g, -1, ax)
    return d


def shape_score(S, ref, lo, hi, stretch=False):
    """Overlap (IoU) of the surface samples S, scaled so their longest side is the original's (stretch: each side to
    the original's, for characters of other proportions) and centred on it, with the original's samples ref."""
    a, b = S.min(0), S.max(0)
    if stretch:
        s = (hi - lo) / np.maximum(b - a, 1e-9)
        Q = (S - (a + b) / 2) * s + (lo + hi) / 2
        pad = (hi - lo) * 0.08
        A, B = _vox(Q, lo - pad, hi + pad), _vox(ref, lo - pad, hi + pad)
        return float((A & B).sum() / max((A | B).sum(), 1))
    s = float((hi - lo).max() / max((b - a).max(), 1e-9))
    Q = (S - (a + b) / 2) * s + (lo + hi) / 2
    # cubic cells (a thin original - a sword, a rod - would otherwise get cells far longer than they're wide)
    c = (lo + hi) / 2
    half = (hi - lo).max() * 0.58
    A, B = _vox(Q, c - half, c + half), _vox(ref, c - half, c + half)
    iou = float((A & B).sum() / max((A | B).sum(), 1))
    # and the proportions: long and thin like the original, flat like it...
    want = np.log(np.maximum(hi - lo, 1e-6) / max((hi - lo).max(), 1e-6))
    got = np.log(np.maximum(np.ptp(Q, 0), 1e-6) / max(np.ptp(Q, 0).max(), 1e-6))
    # and how thick it is along its length (a sword's wide guard at the grip end like the beam sword's thick handle;
    # upside down it would be at the wrong end)
    ax = int(np.argmax(hi - lo))
    return iou + 0.25 * float(np.exp(-((got - want) ** 2).sum())) + 0.3 * _grip_end(Q, ref, lo, hi, ax)


def _profile(X, lo, hi, ax, n=16):
    other = [k for k in range(3) if k != ax]
    t = (X[:, ax] - lo[ax]) / max(hi[ax] - lo[ax], 1e-9)
    out = np.zeros(n)
    for k in range(n):
        m = (t >= k / n) & (t < (k + 1) / n)
        if m.sum() >= 3:
            out[k] = max(np.ptp(X[m][:, other[0]]), np.ptp(X[m][:, other[1]]))
    return out


def _grip_end(Q, ref, lo, hi, ax):
    """Hand-held things: the game holds the item at its origin (the grip). If the original's grip is at its widest end
    (the beam sword's thick handle), the new model's widest end (a sword's guard) goes there too; if it's at the other
    end (an axe: the head is far from the hand), the other way round. +1 / -1, 0 when the original isn't clearly long."""
    ext = hi - lo
    if ext[ax] < 1.5 * sorted(ext)[1]:
        return 0.0
    L = max(ext[ax], 1e-9)
    grip = (0.0 - lo[ax]) / L
    if not 0.0 <= grip <= 1.0:
        return 0.0
    po, pq = _profile(ref, lo, hi, ax), _profile(Q, lo, hi, ax)
    wo = (np.argmax(po) + 0.5) / len(po)
    wq = (np.argmax(pq) + 0.5) / len(pq)
    if abs(grip - 0.5) < 0.1 or abs(wo - 0.5) < 0.1 or abs(wq - 0.5) < 0.05:
        return 0.0                              # the middle: can't tell ends apart
    same = (grip < 0.5) == (wo < 0.5)
    want_low = (grip < 0.5) if same else (grip >= 0.5)
    return 1.0 if (wq < 0.5) == want_low else -1.0


def _gap(X):
    """Largest gap in X (sorted) relative to its spread: two feet apart -> large, one head -> small."""
    if len(X) < 8:
        return 0.0
    x = np.sort(X)
    return float(np.diff(x).max() / max(x[-1] - x[0], 1e-9))


def _feet_down(Q):
    """Characters: 1 when two feet stand at the bottom (a gap between them) and one head is at the top."""
    y = Q[:, 1]
    lo, hi = y.min(), y.max()
    H = max(hi - lo, 1e-9)
    bottom = Q[y < lo + 0.04 * H, 0]
    top = Q[y > hi - 0.04 * H, 0]
    return 1.0 if _gap(bottom) > 0.2 and _gap(top) < 0.2 else 0.0


def _toes(Q):
    """Characters: + when the feet point to +Z (toes stick out in front of the shins)."""
    y = Q[:, 1]
    lo, H = y.min(), np.ptp(y)
    feet = Q[y < lo + 0.05 * H]
    shins = Q[(y > lo + 0.12 * H) & (y < lo + 0.25 * H)]
    if len(feet) < 10 or len(shins) < 10:
        return 0.0
    return float((feet[:, 2].mean() - shins[:, 2].mean()) / max(H, 1e-9))


def _wheels_below(parts, R, centre, s):
    """Vehicles: how well wheels show up in this position: round pieces with a sideways axle in the lower half
    (vehicle.detect's test), as a share of 4. parts: [(P, T, info)] in the model's own frame."""
    from . import vehicle
    ps = [dict(T=t, info=inf) for _, t, inf in parts]
    Q = [((p_ - centre) @ R.T) * s for p_, _, _ in parts]
    try:
        _, wg, _ = vehicle.detect(ps, Q, lambda *a: None)
    except Exception:  # noqa: BLE001
        return 0.0
    return min(len(wg), 4) / 4.0


def _misalign(Nn, a, R=np.eye(3)):
    """How far the surfaces' facing directions are from the axes (area-weighted; 0 = every face square to an axis)."""
    M = np.abs(Nn @ R.T)
    return float((a * (1.0 - M.max(1))).sum() / max(a.sum(), 1e-12))


def _dominant(Nn, a, bins=90):
    """The direction most of the surface faces (either way), from a histogram on the half sphere."""
    n = Nn * np.where(Nn[:, 1:2] < 0, -1.0, 1.0)            # fold opposite directions together
    th = np.arccos(np.clip(n[:, 1], -1, 1))
    ph = np.arctan2(n[:, 2], n[:, 0])
    H, te, pe = np.histogram2d(th, ph, bins=[bins // 2, bins], range=[[0, np.pi / 2 + 1e-6], [-np.pi, np.pi]], weights=a)
    i, j = np.unravel_index(int(np.argmax(H)), H.shape)
    sel = (th >= te[i]) & (th < te[i + 1]) & (ph >= pe[j]) & (ph < pe[j + 1])
    d = (n[sel] * a[sel, None]).sum(0) if sel.any() else np.array([0.0, 1.0, 0.0])
    return d / max(np.linalg.norm(d), 1e-12)


def straighten_main(S, min_angle=12.0):
    """The rotation that lines up a model's main (longest) direction, and its second, with the axes: for things drawn
    at a slant on a square grid (a Minecraft sword is a diagonal picture made of square pixels). Identity when the
    main direction isn't clear or is within min_angle of an axis already."""
    X = S - S.mean(0)
    w, v = np.linalg.eigh(X.T @ X / max(len(X), 1))
    order = np.argsort(-w)
    w, v = w[order], v[:, order]
    if w[0] < 1.3 * w[1]:
        return np.eye(3)
    e1 = v[:, 0]
    e2 = v[:, 1] if w[1] > 1.3 * w[2] else None
    if e2 is None:
        e2 = np.cross(e1, [0, 1.0, 0]) if abs(e1[1]) < 0.9 else np.cross(e1, [1.0, 0, 0])
        e2 /= np.linalg.norm(e2)
    F = np.stack([e1, e2, np.cross(e1, e2)])
    best = None
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            T = np.zeros((3, 3))
            for i in range(3):
                T[perm[i], i] = signs[i]
            R = T @ F
            if np.linalg.det(R) > 0 and (best is None or np.trace(R) > best[0]):
                best = (np.trace(R), R)
    R = best[1]
    angle = np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))
    return R if angle >= min_angle else np.eye(3)


def straighten(SN, area=None, min_angle=5.0):
    """A model drawn at a slant (a Minecraft sword lies diagonally in its file): the rotation that lines its surfaces up
    with the axes, or the identity when they're square to them already (models are built from faces that line up with
    the object's own axes: box-modelled guns, voxel swords, car bodies; smooth round things have no say and are left).
    SN: surface samples with normals."""
    Nn = SN[:, 3:]
    a = np.ones(len(SN)) if area is None else area
    ok = np.linalg.norm(Nn, axis=1) > 0.5
    Nn, a = Nn[ok], a[ok]
    if len(Nn) < 50:
        return np.eye(3)
    e1 = _dominant(Nn, a)
    # the second direction: what faces square to the first (edges, sides)
    side = np.abs(Nn @ e1) < 0.15
    if side.sum() >= 20:
        q = Nn[side] - np.outer(Nn[side] @ e1, e1)
        q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
        ref = np.cross(e1, [1.0, 0, 0]) if abs(e1[0]) < 0.9 else np.cross(e1, [0, 0, 1.0])
        ref /= np.linalg.norm(ref)
        ref2 = np.cross(e1, ref)
        ang = np.mod(np.arctan2(q @ ref2, q @ ref), np.pi)        # either way along the line
        h, edges = np.histogram(ang, bins=180, range=(0, np.pi), weights=a[side])
        k = int(np.argmax(h))
        t = (edges[k] + edges[k + 1]) / 2
        e2 = np.cos(t) * ref + np.sin(t) * ref2
    else:
        e2 = np.cross(e1, [0, 1.0, 0]) if abs(e1[1]) < 0.9 else np.cross(e1, [1.0, 0, 0])
        e2 /= np.linalg.norm(e2)
    e3 = np.cross(e1, e2)
    F = np.stack([e1, e2, e3])                 # rows: the model's own directions
    # each own direction onto the coordinate axis it is nearest to (the smallest turn), keeping a proper rotation
    best = None
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            T = np.zeros((3, 3))
            for i in range(3):
                T[perm[i], i] = signs[i]
            R = T @ F
            if np.linalg.det(R) < 0:
                continue
            tr = np.trace(R)
            if best is None or tr > best[0]:
                best = (tr, R)
    R = best[1]
    angle = np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))
    if angle < min_angle:
        return np.eye(3)
    before, after = _misalign(Nn, a), _misalign(Nn, a, R)
    better = after < 0.8 * before and before > 0.02
    if not better and side.sum() >= 20:
        # a flat thing (a sword cut from a picture) is mostly its front and back, square already: what decides is
        # whether its edges line up
        sb, sa = _misalign(Nn[side], a[side]), _misalign(Nn[side], a[side], R)
        better = sa < 0.6 * sb and sb > 0.05 and after <= before + 0.01
    return R if better else np.eye(3)


def auto(kind, P, T, om, ref=None, parts=None):
    """-> (3x3 rotation, score) for the model (P: points, T: triangles over P) to stand like the original model om.
    ref: the original's surface points (default: from om). parts: [(P, T, info)] per part, for the wheel clue."""
    SN = character.surface_samples(P, T, 12000, seed=7)
    S = SN[:, :3]
    # drawn at a slant: also tried lined up with the axes (two ways of telling), then the usual quarter turns; the
    # shape decides which start wins (a gun's lopsided grip mustn't tilt it, a diagonal sword must be straightened)
    starts = [np.eye(3)]
    if kind != 'character':
        for R0 in (straighten(SN), straighten_main(S)):
            if not any(np.allclose(R0, x, atol=1e-3) for x in starts):
                starts.append(R0)
    centre_raw = (P.min(0) + P.max(0)) / 2
    S_raw, P_raw = S, P
    if ref is None:
        oP, _, _ = om.vertices()
        near = min(p['flags'][1] for p in om.pieces)
        idx = om.indices()
        oT = np.concatenate([np.array(om.triangles(p, idx), np.int64).reshape(-1, 3) for p in om.pieces if p['flags'][1] == near])
        ref = character.surface_samples(oP, oT, 12000, seed=7)[:, :3]
    lo, hi = ref.min(0), ref.max(0)
    best_all = None
    for R0 in starts:
        S = S_raw @ R0.T
        P = P_raw @ R0.T
        sc_, R_ = _search(kind, S, P, T, ref, lo, hi, parts, R0, centre_raw)
        sc_ -= 0.0 if np.allclose(R0, np.eye(3)) else 0.03          # straightening has to win clearly
        if best_all is None or sc_ > best_all[0]:
            best_all = (sc_, R_)
    return best_all[1], best_all[0]


def _search(kind, S, P, T, ref, lo, hi, parts, R0, centre_raw):
    scored = []
    for R in rotations():
        Q = S @ R.T
        if kind == 'character':
            ext = np.ptp(Q, 0)
            # a body is thinnest front to back; toes point forward
            sc = shape_score(Q, ref, lo, hi, stretch=True) + 4.0 * np.clip(_toes(Q), -0.05, 0.05) + 0.3 * (ext[2] <= ext.min() + 1e-9) + 0.2 * _feet_down(Q)
        else:
            sc = shape_score(Q, ref, lo, hi)
            if kind == 'vehicle':
                # a vehicle is longer (front to back) than wide, and not standing on end
                ext = np.ptp(Q, 0)
                sc += 0.3 * (ext[2] >= ext[0]) + 0.2 * (ext[1] < ext[2])
        # the file's own way (glTF: +Y up, facing +Z) wins a near tie: front / back is often ambiguous from shape alone
        sc += 0.05 * np.allclose(R, np.eye(3)) + 0.03 * (R[1, 1] > 0.5)
        scored.append([sc, R])
    scored.sort(key=lambda x: -x[0])
    if kind == 'vehicle':
        # of the best few: wheels underneath (with sideways axles) and seats facing forward (backrest behind)
        top = [x for x in scored[:12] if x[0] >= scored[0][0] - 0.3]
        centre = (P.min(0) + P.max(0)) / 2
        Pa = P - centre
        for x in top:
            R = x[1]
            Q = Pa @ R.T
            s = float((hi - lo).max() / max(np.ptp(Q, 0).max(), 1e-9))
            Qs = Q * s
            if parts:
                x[0] += 0.4 * _wheels_below(parts, R @ R0, centre_raw, s)
            found = seats.find_seats(Qs, T, Qs[:, 1].min(), bike=False)
            x[0] += 0.12 * min(max([f.score for f in found] or [0.0]) / 3000.0, 1.0)
        scored = sorted(top, key=lambda x: -x[0]) + scored[len(top):]
    return scored[0][0], scored[0][1] @ R0


def describe(R):
    """In words, for the log: which way the model's own axes ended up (roughly, when it was straightened too)."""
    names = {0: 'X', 1: 'Y', 2: 'Z'}
    up = int(np.argmax(np.abs(R[1])))
    fwd = int(np.argmax(np.abs(R[2])))
    return 'its %s%s axis up, %s%s forward' % ('+' if R[1, up] > 0 else '-', names[up], '+' if R[2, fwd] > 0 else '-', names[fwd])
