"""Where the driver and the passenger sit in a vehicle model.

The game seats Frank where the original vehicle's driver seat is (its position in the car is fixed by the game), and
the co-op mod puts the passenger in the mirrored seat. A new model's seats are somewhere else, so the tool works out
how far (in the vehicle's own frame) each seat moved, and the co-op mod draws the players shifted by that much
(DRModelSwap/seats.txt). The seats are found from the shape: a cushion (a surface facing up, with room above it) with a
backrest (a surface facing forward) right behind it; on bikes the saddle (the top of the frame between the wheels).
A steering wheel or handlebar named in the model is used instead when there is one: the driver sits where he can
hold it, like in the original."""
import numpy as np

from . import character

BIKES = ('om009b', 'om009d')
# The original vehicles' driver seats (middle of the cushion, game units, the vehicle's frame), read off the models.
# The passenger seat of a car is the mirror image (the co-op mod's rule).
GAME = {'om0009': (34.0, -40.0, -12.0), 'om0098': (36.0, -36.0, 12.0), 'om0099': (47.0, -2.0, 19.0),
        'om009a': (73.0, 33.0, 24.0), 'om009b': (0.0, 38.0, -28.0), 'om009d': (0.0, 64.0, -32.0)}


def game_seat(model_path):
    name = model_path.replace('/', '\\').rsplit('\\', 1)[-1].lower()
    return np.array(GAME[name]) if name in GAME else None


class Seat:
    __slots__ = ('pos', 'how', 'score')

    def __init__(self, pos, how, score=0.0):
        self.pos, self.how, self.score = np.asarray(pos, float), how, score

    def __repr__(self):
        return 'Seat(%s, %s)' % (np.round(self.pos, 1), self.how)


def _grid_clusters(X, cell):
    """Group points by grid cells joined to their neighbours -> list of index arrays."""
    if len(X) == 0:
        return []
    q = np.floor(X / cell).astype(np.int64)
    cells = {}
    for i, c in enumerate(map(tuple, q)):
        cells.setdefault(c, []).append(i)
    seen, out = set(), []
    for c in cells:
        if c in seen:
            continue
        stack, grp = [c], []
        seen.add(c)
        while stack:
            a = stack.pop()
            grp += cells[a]
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    b = (a[0] + dx, a[1] + dz)
                    if b in cells and b not in seen:
                        seen.add(b)
                        stack.append(b)
        out.append(np.array(grp))
    return out


def find_seats(P, T, ground=None, bike=False):
    """Seats of a vehicle model in the game's frame (+X left, +Z front, Y up, game units). P, T: the body without its
    wheels. -> list of Seat, best first (front row first, then left before right)."""
    if len(T) == 0:
        return []
    S = character.surface_samples(P, T, 40000, seed=5)
    X, Nn = S[:, :3], S[:, 3:]
    lo, hi = P.min(0), P.max(0)
    ground = lo[1] if ground is None else ground
    H, L = hi[1] - ground, hi[2] - lo[2]
    up = Nn[:, 1] > 0.75
    fwd = Nn[:, 2] > 0.5
    if bike:
        # the saddle: the highest surface facing up over the middle half of the length, near the middle
        m = up & (np.abs(X[:, 0]) < 0.25 * (hi[0] - lo[0]) + 5) & (X[:, 2] > lo[2] + 0.2 * L) & (X[:, 2] < lo[2] + 0.75 * L) \
            & (X[:, 1] > ground + 0.3 * H)
        if m.sum() < 10:
            return []
        Y = X[m]
        # the top line along the length: every point of it is a possible saddle (a motorbike's is a dip behind the
        # tank, a bicycle's a peak on its post); the one nearest where the original has it is taken (near())
        zb = np.arange(Y[:, 2].min(), Y[:, 2].max(), 4.0)
        out = []
        for z in zb:
            m2 = (Y[:, 2] >= z) & (Y[:, 2] < z + 4)
            if m2.sum() >= 3:
                out.append(Seat([0.0, float(Y[m2, 1].max()), float(z + 2.0)], 'saddle', 1.0))
        return out
    cand = up & (X[:, 1] > ground + 0.12 * H) & (X[:, 1] < ground + 0.6 * H)
    if not cand.any():
        return []
    # counts on a 4-unit grid, summed so any box can be counted at once (backrest behind, room above, something ahead)
    # only the space the boxes below can reach from a candidate is gridded (a scaled-up model mustn't need gigabytes);
    # a very big model gets coarser cells
    cell = 4.0
    Cc = X[cand]
    o = np.maximum(X.min(0), Cc.min(0) - 140) - cell * 2
    top = np.minimum(X.max(0), Cc.max(0) + 140) + cell * 2
    n = float(np.prod(np.ceil((top - o) / cell) + 1))
    if n > 2.5e7:
        cell *= (n / 2.5e7) ** (1 / 3)
    shape = np.ceil((top - o) / cell).astype(int) + 1
    inside = np.all((X >= o) & (X <= top), 1)

    def integral(mask):
        g = np.zeros(shape, np.int32)
        idx = np.clip(np.floor((X[mask & inside] - o) / cell).astype(int), 0, shape - 1)
        np.add.at(g, tuple(idx.T), 1)
        g = g.cumsum(0, dtype=np.int32).cumsum(1, dtype=np.int32).cumsum(2, dtype=np.int32)
        return np.pad(g, ((1, 0), (1, 0), (1, 0)))

    I_all, I_fwd = integral(np.ones(len(X), bool)), integral(fwd)

    def count(I, c, lo, hi):
        """samples in the box c+lo .. c+hi (game units) for every row of c"""
        a = np.clip(np.floor((c + lo - o) / cell).astype(int), 0, np.array(shape))
        b = np.clip(np.floor((c + hi - o) / cell).astype(int), 0, np.array(shape))
        b = np.maximum(a, b)
        x0, y0, z0 = a.T
        x1, y1, z1 = b.T
        return (I[x1, y1, z1] - I[x0, y1, z1] - I[x1, y0, z1] - I[x1, y1, z0]
                + I[x0, y0, z1] + I[x0, y1, z0] + I[x1, y0, z0] - I[x0, y0, z0])
    C = X[cand]
    # a backrest: faces forward, 6-45 behind, and tall: up at 25-60 above the cushion (the front of a seat, behind a
    # footwell, only rises 15-25), at the same place sideways
    back = (count(I_fwd, C, np.array([-12, 25, -45]), np.array([12, 60, -6])) >= 4) & \
        (count(I_fwd, C, np.array([-12, 5, -45]), np.array([12, 25, -6])) >= 2)
    # room to sit: little right above (a dashboard, a hood or a roof low over it isn't a seat)
    over = count(I_all, C, np.array([-7, 8, -7]), np.array([7, 35, 7])) <= 2
    # something in front of the knees: a dashboard, a windscreen, the front of the vehicle (a bumper or a hood with a
    # grille or windscreen behind it looks like a seat, but has nothing in front)
    ahead = count(I_all, C, np.array([-25, -5, 20]), np.array([25, 70, 130])) >= 6
    keep = back & over & ahead
    strength = count(I_fwd, C, np.array([-12, 25, -45]), np.array([12, 60, -6]))[keep]
    C = C[keep]
    if len(C) < 8:
        return []
    # bigger cushions with taller, fuller backrests score higher (scraps of a footwell or a door pocket score low)
    seats = []
    for cl in _grid_clusters(C[:, [0, 2]], 7.0):
        if len(cl) < 8:
            continue
        c = C[cl]
        w = np.ptp(c[:, 0])
        if w > 70:          # a bench: a seat on each side
            for sgn in (1, -1):
                h = c[c[:, 0] * sgn > 0]
                hm = c[:, 0] * sgn > 0
                if hm.sum() >= 8:
                    h = c[hm]
                    seats.append(Seat([float(np.median(h[:, 0])), float(np.median(h[:, 1])), float(np.median(h[:, 2]))], 'bench',
                                      float(hm.sum() * np.mean(strength[cl][hm]))))
        else:
            seats.append(Seat([float(np.median(c[:, 0])), float(np.median(c[:, 1])), float(np.median(c[:, 2]))], 'seat',
                              float(len(cl) * np.mean(strength[cl]))))
    if not seats:
        return []
    # the cushion points that pass lean to its front half (near the backrest, the backrest itself is "above"): on the
    # game's own vehicles the middle of the cushion is about 10 further back
    for st_ in seats:
        st_.pos[2] -= 10.0
    seats.sort(key=lambda s: -s.score)
    return seats


def near(seats, prior, side=0.0, reach=90.0):
    """The best seat near where it's expected (prior): score, less the further away it is; on the given side (sign of x)
    when side is set. -> Seat or None."""
    best, bv = None, 0.0
    for s in seats:
        d = float(np.linalg.norm((s.pos - prior) * [1.0, 1.5, 1.0]))
        if d > reach or (side and s.pos[0] * side < 5):
            continue
        v = s.score / (1.0 + (d / 35.0) ** 2)
        if v > bv:
            best, bv = s, v
    return best


def carried_over(g, box_g, box_u):
    """The original's seat moved to the same place relative to the new model's size (box: (lo, hi))."""
    lo_g, hi_g = (np.asarray(x, float) for x in box_g)
    lo_u, hi_u = (np.asarray(x, float) for x in box_u)
    return lo_u + (np.asarray(g, float) - lo_g) / np.maximum(hi_g - lo_g, 1e-6) * (hi_u - lo_u)
