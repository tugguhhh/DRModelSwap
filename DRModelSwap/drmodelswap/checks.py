"""Checks that find problems ahead of time (before the model is in the game) and fix what can be fixed safely:
faces turned inside out (the game draws one side of each triangle only: those show as holes), and where a hand-held
item is held."""
from collections import deque
import numpy as np


def _weld(P):
    """One id per position (vertices split at UV seams are one point of the surface)."""
    ext = max(float(np.ptp(P, 0).max()) if len(P) else 1.0, 1e-9)
    q = np.round(P / (ext * 1e-5)).astype(np.int64)
    _, wid = np.unique(q, axis=0, return_inverse=True)
    return wid.reshape(-1)


def orient_faces(P, T):
    """Faces that point the wrong way: within each connected surface, the triangles are made to agree with their
    neighbours, and a closed surface is turned so it faces outwards. -> (bool per triangle: reverse it, report).
    report: reversed (triangles), inside_out (closed pieces turned right side out), mixed (triangles that disagreed
    with their neighbours), area (share of the surface reversed)."""
    n = len(T)
    rep = dict(reversed=0, inside_out=0, mixed=0, area=0.0)
    if n == 0:
        return np.zeros(0, bool), rep
    w = _weld(P)
    W = w[T]
    ok = (W[:, 0] != W[:, 1]) & (W[:, 1] != W[:, 2]) & (W[:, 0] != W[:, 2])
    # every edge: (low id, high id) -> triangles using it, and which way round each uses it
    a = np.r_[W[:, 0], W[:, 1], W[:, 2]]
    b = np.r_[W[:, 1], W[:, 2], W[:, 0]]
    t = np.r_[np.arange(n), np.arange(n), np.arange(n)]
    keep = np.r_[ok, ok, ok]
    a, b, t = a[keep], b[keep], t[keep]
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    fwd = a < b
    key = lo.astype(np.int64) * (int(w.max()) + 1) + hi
    order = np.argsort(key, kind='stable')
    key, t, fwd = key[order], t[order], fwd[order]
    starts = np.r_[0, np.nonzero(np.diff(key))[0] + 1]
    counts = np.diff(np.r_[starts, len(key)])
    nbr = [[] for _ in range(n)]
    boundary = np.zeros(n, np.int64)
    for s, c in zip(starts, counts):
        if c == 2:
            t0, t1 = int(t[s]), int(t[s + 1])
            same = bool(fwd[s] == fwd[s + 1])   # both use the edge the same way round: they disagree
            nbr[t0].append((t1, same))
            nbr[t1].append((t0, same))
        else:
            boundary[t[s:s + c]] += 1           # an open edge, or one shared by more than two triangles
    flip = np.zeros(n, bool)
    comp = np.full(n, -1, np.int64)
    nc = 0
    for s0 in range(n):
        if comp[s0] >= 0 or not ok[s0]:
            continue
        comp[s0] = nc
        dq = deque([s0])
        while dq:
            u = dq.popleft()
            for v, same in nbr[u]:
                if comp[v] < 0:
                    comp[v] = nc
                    flip[v] = flip[u] ^ same
                    dq.append(v)
        nc += 1
    fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    area = np.linalg.norm(fn, axis=1) / 2
    tot = max(float(area.sum()), 1e-12)
    for c in range(nc):
        m = np.nonzero(comp == c)[0]
        if len(m) < 2:
            flip[m] = False
            continue
        closed = boundary[m].sum() <= 0.25 * 3 * len(m)    # (mostly) closed: inside and outside make sense
        if closed and len(m) >= 12:
            # a closed surface: is it facing out? Just outside the biggest triangles, the surface must not wrap round
            Tm = T[m].copy()
            Tm[flip[m]] = Tm[flip[m]][:, [0, 2, 1]]
            big = m[np.argsort(-area[m])[:48]]
            Tb = T[big].copy()
            Tb[flip[big]] = Tb[flip[big]][:, [0, 2, 1]]
            nb = np.cross(P[Tb[:, 1]] - P[Tb[:, 0]], P[Tb[:, 2]] - P[Tb[:, 0]])
            nb /= np.maximum(np.linalg.norm(nb, axis=1, keepdims=True), 1e-12)
            eps = 1e-3 * float(np.ptp(P[Tm.ravel()], 0).max())
            wn = winding_numbers(P, Tm, P[Tb].mean(1) + eps * nb)
            if np.median(np.abs(wn)) > 0.5:     # the point just in front of the face is inside: facing in
                flip[m] = ~flip[m]
                rep['inside_out'] += 1
        elif area[m][flip[m]].sum() > 0.5 * area[m].sum():
            flip[m] = ~flip[m]              # an open sheet: keep the way most of it already faces
    rep['reversed'] = int(flip.sum())
    rep['area'] = round(float(area[flip].sum()) / tot, 4)
    rep['mixed'] = int(sum(1 for c in range(nc) if 0 < flip[comp == c].mean() < 0.9))
    return flip, rep


def winding_numbers(P, T, Q):
    """Generalised winding number of the surface (P, T) at points Q: about 1 inside a closed surface facing out, 0
    outside (-1 inside one facing in)."""
    A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    out = np.zeros(len(Q))
    step = max(1, 2000000 // max(len(T), 1))
    for s in range(0, len(Q), step):
        q = Q[s:s + step][:, None, :]
        a, b, c = A[None] - q, B[None] - q, C[None] - q
        la, lb, lc = (np.linalg.norm(x, axis=2) for x in (a, b, c))
        det = (a * np.cross(b, c)).sum(2)
        den = la * lb * lc + (a * b).sum(2) * lc + (b * c).sum(2) * la + (c * a).sum(2) * lb
        out[s:s + step] = np.arctan2(det, den).sum(1) / (2 * np.pi)
    return out


# ----- hand-held items: the game holds an item at its origin (Frank's hand). A gun-like model (a handle under its
# body) should have the top of its handle there, front forwards; anything else is placed in the original's box.

def find_grip(S):
    """S: surface points of the item in the game's frame (Y up, Z = its length). -> dict(hand=point, front=+1 / -1 =
    which way along Z the item points, grip=(z top, z bottom, depth), why=...) or None (no handle found)."""
    if len(S) < 50:
        return None
    lo, hi = S.min(0), S.max(0)
    L, H = float(hi[2] - lo[2]), float(hi[1] - lo[1])
    if L <= 0 or H <= 0 or L < 0.6 * float(hi[0] - lo[0]):
        return None
    nb = 48
    b = np.clip(((S[:, 2] - lo[2]) / L * nb).astype(int), 0, nb - 1)
    bottom = np.full(nb, np.nan)
    for i in range(nb):
        m = b == i
        if m.any():
            bottom[i] = S[m, 1].min()
    have = ~np.isnan(bottom)
    if have.sum() < nb // 2:
        return None
    body_bottom = float(np.nanpercentile(bottom, 70))
    drop = np.where(have, body_bottom - bottom, 0.0)
    deep = drop > 0.25 * H
    runs, i = [], 0
    while i < nb:
        if deep[i]:
            j = i
            while j + 1 < nb and deep[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    # a handle is narrow (a stock or a long box isn't)
    runs = [r for r in runs if (r[1] - r[0] + 1) <= 0.45 * nb]
    if not runs:
        return None
    out = []
    for i, j in runs:
        z0, z1 = lo[2] + i * L / nb, lo[2] + (j + 1) * L / nb
        m = (S[:, 2] >= z0) & (S[:, 2] <= z1) & (S[:, 1] < body_bottom)
        if m.sum() < 10:
            continue
        G = S[m]
        depth = body_bottom - float(G[:, 1].min())
        top = G[G[:, 1] > body_bottom - 0.3 * depth]
        bot = G[G[:, 1] < body_bottom - 0.7 * depth]
        if len(top) < 3 or len(bot) < 3:
            continue
        # a hand-sized handle hangs down: its top is no longer than about its depth (a rifle's stock is long and low)
        width = float(np.percentile(top[:, 2], 95) - np.percentile(top[:, 2], 5))
        if width > 1.3 * depth:
            continue
        out.append(dict(zt=float(np.median(top[:, 2])), zb=float(np.median(bot[:, 2])), x=float(np.median(top[:, 0])),
                        depth=depth, z0=z0, z1=z1, width=width))
    if not out:
        return None
    mid = (lo[2] + hi[2]) / 2
    # which way it points: a handle rakes back (its bottom further back than its top); straight handles: the long
    # part (barrel) is in front of the handle
    g0 = out[0]
    rake = sum(g['zb'] - g['zt'] for g in out) / len(out)
    if abs(rake) > 0.03 * L:
        front = -1.0 if rake > 0 else 1.0
        why = 'the handle leans back'
    else:
        front = 1.0 if g0['zt'] < mid else -1.0
        why = 'the barrel is the longer side'
    # the hand holds the rearmost handle (a rifle's magazine is in front of its grip)
    g = min(out, key=lambda r: r['zt'] * front)
    body_top = float(hi[1])
    # the game's guns hold the hand about 10 units (a hand's width) in front of the handle's top, a little above the
    # body's underside (measured on the game's own handgun, SMG, rifles, water gun)
    hand = np.array([g['x'], min(body_bottom + 3.0, (body_bottom + body_top) / 2), g['zt'] + front * min(10.0, 1.2 * g['depth'])])
    return dict(hand=hand, front=front, grip=(g['zt'], g['zb'], g['depth']), why=why, handles=len(out))
