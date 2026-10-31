"""The model's pieces, for the Parts list: what the tool took each piece to be (a wheel, a door, the body, a head...),
and what the person says it is instead. A piece is a connected part of one mesh; its id ("part:vertex") stays the same
whatever the turn, size or other settings, so a choice sticks while the preview is redone.

Roles: auto (what the tool finds), body (fixed to the body / not a moving part), wheel, steering_wheel, door, hood,
trunk, handlebar, fork, swingarm, crank, head (characters: moves as one with the head), remove (left out)."""
import numpy as np

from .vehicle import components

VEHICLE_ROLES = ['auto', 'body', 'wheel', 'steering_wheel', 'door', 'hood', 'trunk', 'handlebar', 'fork', 'swingarm', 'crank', 'remove']
CHARACTER_ROLES = ['auto', 'head', 'body', 'remove']
OBJECT_ROLES = ['auto', 'remove']


def tag(ps):
    """Remember each part's and vertex's original number (pieces keep their ids when others are left out)."""
    for i, p in enumerate(ps):
        p.setdefault('pid', i)
        p.setdefault('vid', np.arange(len(p['P'])))
    return ps


def tolerance(ps):
    allP = np.concatenate([p['P'] for p in ps]) if ps else np.zeros((1, 3))
    return float(np.ptp(allP, 0).max()) * 2e-5


def comp_id(p, verts):
    return '%d:%d' % (p['pid'], int(p['vid'][int(np.min(verts))]))


def all_pieces(ps, tol=None):
    """-> list of (part index, vertex indices, id) of every connected piece of every part."""
    tol = tolerance(ps) if tol is None else tol
    out = []
    for i, p in enumerate(ps):
        if not len(p['T']):
            continue
        lab = components(p['P'], p['T'], tol)
        used = np.zeros(len(p['P']), bool)
        used[p['T'].ravel()] = True
        order = np.argsort(lab, kind='stable')
        cuts = np.flatnonzero(np.diff(lab[order])) + 1
        for vs in np.split(order, cuts):
            vs = vs[used[vs]]
            if len(vs):
                out.append((i, vs, comp_id(p, vs)))
    return out


def leave_out(ps, ids, tol=None):
    """Parts without the pieces whose id is in ids (their vertices and triangles dropped). -> (new parts, count)."""
    if not ids:
        return ps, 0
    n = 0
    drop = {}
    for i, vs, cid in all_pieces(ps, tol):
        if cid in ids:
            drop.setdefault(i, []).append(vs)
            n += 1
    out = []
    for i, p in enumerate(ps):
        if i not in drop:
            out.append(p)
            continue
        gone = np.zeros(len(p['P']), bool)
        gone[np.concatenate(drop[i])] = True
        T = p['T'][~gone[p['T']].any(1)]
        if not len(T):
            continue
        keep = np.unique(T)
        remap = np.full(len(p['P']), -1)
        remap[keep] = np.arange(len(keep))
        q = dict(p, P=p['P'][keep], N=None if p['N'] is None else p['N'][keep], UV=p['UV'][keep], T=remap[T], vid=p['vid'][keep])
        if 'colors' in (p.get('info') or {}):
            q['info'] = dict(p['info'], colors=p['info']['colors'][keep])
        out.append(q)
    return out, n


def flat_base(ps, Q=None):
    """Ids of a flat base the model stands on (a ground plane, a display plate, a shadow): a thin, flat piece at one end
    of the model that is itself the model's full width and length (the rest of the model fits inside it). Works before
    the model is turned (any axis can be "up"), so the base can't spoil the automatic turn. Q: positions per part
    (default the parts' own)."""
    Q = [p['P'] for p in ps] if Q is None else Q
    allQ = np.concatenate(Q)
    lo, hi = allQ.min(0), allQ.max(0)
    ext = np.maximum(hi - lo, 1e-9)
    out = []
    tol = float(ext.max()) * 2e-5
    for i, p in enumerate(ps):
        if not len(p['T']):
            continue
        lab = components(Q[i], p['T'], tol)
        used = np.zeros(len(Q[i]), bool)
        used[p['T'].ravel()] = True
        for c in np.unique(lab[used]):
            vs = np.flatnonzero((lab == c) & used)
            q = Q[i][vs]
            a, b = q.min(0), q.max(0)
            e = b - a
            ax = int(np.argmin(e))
            other = [k for k in range(3) if k != ax]
            at_end = a[ax] <= lo[ax] + 0.02 * ext[ax] or b[ax] >= hi[ax] - 0.02 * ext[ax]
            if e[ax] < 0.03 * ext.max() and at_end and all(e[k] >= 0.98 * ext[k] for k in other) and ext[ax] > 0.1 * ext.max():
                out.append(comp_id(p, vs))
    return out


def _name(p, k=None):
    inf = p.get('info') or {}
    nm = inf.get('name') or inf.get('mesh') or inf.get('material') or 'part %d' % (p['pid'] + 1)
    nm = nm.rsplit('/', 1)[-1]
    return nm if k is None else '%s #%d' % (nm, k)


def entries(ps, Pw, groups, min_size=0.04, cap=60):
    """The list shown to the person. groups: [(name, role found, [(part index, verts)])] for things already known (each
    wheel with its rim and bolts, named parts, head pieces); other pieces bigger than min_size of the model are listed
    one by one, the biggest first. Pw: placed positions per part (for the box drawn when a piece is shown)."""
    allP = np.concatenate(Pw)
    ext = float(np.ptp(allP, 0).max()) or 1.0
    lo_all, hi_all = allP.min(0), allP.max(0)
    out, seen = [], set()

    def box(members):
        Q = np.concatenate([Pw[i][vs] for i, vs in members])
        return [Q.min(0).round(2).tolist(), Q.max(0).round(2).tolist()]

    def where(b):
        """Roughly where the piece is: height, side, front / back (the game's frame: +X left, +Z front)."""
        c = (np.array(b[0]) + np.array(b[1])) / 2
        f = np.clip((c - lo_all) / np.maximum(hi_all - lo_all, 1e-9), 0, 1)
        words = [['bottom', 'lower', 'middle', 'upper', 'top'][min(4, int(f[1] * 5))]]
        if abs(f[0] - 0.5) > 0.12:
            words.append('left' if f[0] > 0.5 else 'right')
        if abs(f[2] - 0.5) > 0.2:
            words.append('front' if f[2] > 0.5 else 'back')
        return ', '.join(words)
    for name, role, members in groups:
        ids = [comp_id(ps[i], vs) for i, vs in members]
        seen.update(ids)
        b = box(members)
        out.append(dict(id=ids[0], ids=ids, name=name, where=where(b), found=role, box=b, size=round(float(np.max(np.subtract(b[1], b[0]))), 1)))
    rest = []
    counts = {}
    for i, vs, cid in all_pieces(ps):
        if cid in seen:
            continue
        Q = Pw[i][vs]
        sz = float(np.ptp(Q, 0).max())
        if sz >= min_size * ext:
            rest.append((sz, i, vs, cid))
    rest.sort(key=lambda r: -r[0])
    for sz, i, vs, cid in rest[:max(0, cap - len(out))]:
        counts[i] = counts.get(i, 0) + 1
        b = box([(i, vs)])
        out.append(dict(id=cid, ids=[cid], name=_name(ps[i], counts[i]), where=where(b), found='body', box=b, size=round(sz, 1)))
    return out
