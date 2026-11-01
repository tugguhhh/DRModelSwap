"""Small software renderer for previews (flat-shaded, one texture sample per triangle)."""
import math
import numpy as np
from PIL import Image, ImageDraw

from . import rig


def _view(yaw, pitch):
    a, b = math.radians(yaw), math.radians(pitch)
    Ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
    return Rx @ Ry


def render(model, textures, size=(560, 400), yaw=-140.0, pitch=-22.0, box=None, ss=2, pose=None, groups=None, marks=None, lines=None,
           see_through=False, hot=None):
    """model: mod.Model; textures: {name: PIL image or None}; box: (min, max) drawn as a thin outline
    (the original model's box when previewing a replacement). Front of the game model (+Z) faces left-forward.
    pose: {bone index: 3x3 rotation} to draw it moved (wheels spun, steering turned, arms lowered...).
    groups: only pieces of these display groups (None = all; a piece drawn in several groups is drawn once).
    marks: [(point, colour)] small dots (e.g. wheel axles). lines: [((a, b), colour)] drawn on top (seated riders).
    see_through: the back of every triangle drawn magenta: the game doesn't draw backs, so magenta that shows is a place
    you'll see through in the game. hot: per-vertex flags; triangles touching one are drawn orange (problem spots)."""
    W, H = size[0] * ss, size[1] * ss
    img = Image.new('RGB', (W, H), (38, 40, 46))
    dr = ImageDraw.Draw(img)
    P, N, UV = model.vertices()
    if pose:
        bones = rig.read_bones(model)
        J, Wt = rig.vertex_weights(model)
        P = rig.skin(P, J, Wt, bones, rig.world_matrices(bones, pose))
    idx = model.indices()
    tris, cols = [], []
    seen = set()
    lod0 = min((pc['flags'][1] for pc in model.pieces), default=15)
    for pc in model.pieces:
        k = (pc['first_index'], pc['indices'])
        if k in seen or pc['flags'][1] != lod0 or (groups is not None and pc['group'] not in groups):
            continue
        seen.add(k)
        t = model.triangles(pc, idx)
        if not t:
            continue
        t = np.array(t)
        c_i, _ = model.material_textures(pc['material'])
        timg = textures.get(model.textures[c_i - 1]) if 0 < c_i <= len(model.textures) else None
        if timg is None:
            col = np.tile([[170.0, 170.0, 170.0]], (len(t), 1))
        else:
            a = np.asarray(timg, np.float32)
            h, w = a.shape[:2]
            uv = UV[t].mean(1) % 1.0
            col = a[(uv[:, 1] * h).astype(int).clip(0, h - 1), (uv[:, 0] * w).astype(int).clip(0, w - 1)]
        tris.append(t)
        cols.append(col)
    V = _view(yaw, pitch)
    allpts = [P]
    if box is not None:
        allpts.append(np.array([[x, y, z] for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1]) for z in (box[0][2], box[1][2])]))
    if lines:
        allpts.append(np.array([p for (a, b), _ in lines for p in (a, b)], float))   # (the figure for scale stays in view)
    Q = np.concatenate(allpts) @ V.T
    lo, hi = Q.min(0), Q.max(0)
    scale = 0.86 * min(W / max(hi[0] - lo[0], 1e-6), H / max(hi[1] - lo[1], 1e-6))
    mid = (lo + hi) / 2

    def proj(X):
        R = X @ V.T
        return np.stack([(R[:, 0] - mid[0]) * scale + W / 2, H / 2 - (R[:, 1] - mid[1]) * scale], 1), R[:, 2]

    if box is not None:
        c, _ = proj(np.array([[x, y, z] for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1]) for z in (box[0][2], box[1][2])]))
        for i in range(8):
            for j in range(i + 1, 8):
                if bin(i ^ j).count('1') == 1:
                    dr.line([tuple(c[i]), tuple(c[j])], fill=(90, 150, 220), width=ss)
    if tris:
        T = np.concatenate(tris)
        C = np.concatenate(cols)
        S, Z = proj(P)
        fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]) @ V.T
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-9)
        light = np.array([0.35, 0.6, -0.72])
        light /= np.linalg.norm(light)
        shade = 0.45 + 0.55 * np.abs(fn @ light)
        C = np.clip(C * shade[:, None], 0, 255)
        if hot is not None:
            h = np.asarray(hot, bool)[T].any(1)
            C[h] = C[h] * 0.35 + np.array([255.0, 140.0, 0.0]) * 0.65
        if see_through:
            back = fn[:, 2] > 0          # facing away from the camera (front faces point at it: -z here)
            C[back] = np.array([235.0, 40.0, 200.0]) * (0.75 + 0.25 * shade[back, None])
        C = C.astype(np.uint8)
        depth = Z[T].mean(1)
        if see_through:
            # a back with a front in the same place (a two-sided surface) is hidden by that front, as in the game
            span = float(np.ptp(Z)) if len(Z) else 1.0
            depth = depth + np.where(fn[:, 2] > 0, 1e-3 * span, 0.0)
        order = np.argsort(depth)[::-1]  # far first
        pts = S[T]
        for k in order:
            p = pts[k]
            dr.polygon([(p[0, 0], p[0, 1]), (p[1, 0], p[1, 1]), (p[2, 0], p[2, 1])], fill=tuple(int(x) for x in C[k]))
    for (a, b), col in (lines or []):
        c, _ = proj(np.array([a, b]))
        dr.line([tuple(c[0]), tuple(c[1])], fill=col, width=2 * ss)
    for pt, col in (marks or []):
        c, _ = proj(np.array([pt]))
        dr.ellipse([c[0][0] - 4 * ss, c[0][1] - 4 * ss, c[0][0] + 4 * ss, c[0][1] + 4 * ss], outline=col, width=ss)
    # front marker: +Z of the model
    centre = (model.box_min + model.box_max) / 2 if box is None else (np.array(box[0]) + np.array(box[1])) / 2
    span = (model.box_max - model.box_min) if box is None else (np.array(box[1]) - np.array(box[0]))
    a, _ = proj(np.array([[centre[0], (box[0][1] if box is not None else model.box_min[1]), centre[2] + span[2] * 0.55],
                          [centre[0], (box[0][1] if box is not None else model.box_min[1]), centre[2] + span[2] * 0.75]]))
    dr.line([tuple(a[0]), tuple(a[1])], fill=(240, 200, 60), width=3 * ss)
    dr.ellipse([a[1][0] - 4 * ss, a[1][1] - 4 * ss, a[1][0] + 4 * ss, a[1][1] + 4 * ss], fill=(240, 200, 60))
    return img.resize(size, Image.LANCZOS)


def rider(seat, colour, bike=False):
    """A seated person (stick figure) on a seat cushion's middle point, facing +Z: lines for render(lines=...).
    bike: astride, legs down to the sides."""
    s = np.asarray(seat, float)
    hip = s + [0, 8, -6]
    sh = hip + [0, 45, -6]
    head = sh + [0, 17, 2]
    out = [((hip, sh), colour)]
    for dx in (-9.0, 9.0):
        h = hip + [dx, 0, 0]
        k = h + ([dx * 0.6, -18, 26] if bike else [0, 2, 40])
        f = k + ([0, -32, 2] if bike else [0, -40, 8])
        e = sh + [dx * 2, -18, 20]
        out += [((h, k), colour), ((k, f), colour), ((sh + [dx * 1.8, 0, 0], e), colour), ((e, e + [-dx * 0.6, 4, 20]), colour)]
    ring = [head + [9 * np.cos(a), 9 * np.sin(a), 0] for a in np.linspace(0, 2 * np.pi, 13)]
    out += [((ring[i], ring[i + 1]), colour) for i in range(12)]
    return out


def person(box, colour=(150, 160, 175)):
    """A standing person 183 units (Frank's height, 1.83 m) beside a model's box, for scale: lines for render()."""
    lo, hi = np.asarray(box[0], float), np.asarray(box[1], float)
    size = hi - lo
    x = lo[0] - max(30.0, 0.15 * size[0]) - 25.0           # the side nearer the preview camera
    z = (lo[2] + hi[2]) / 2
    g = lo[1]
    feet = g
    hip = g + 95
    sh = g + 152
    head = g + 172
    pts = []
    for dx in (-10.0, 10.0):
        pts += [((x + dx, feet, z), (x + dx * 0.4, hip, z)), ((x + dx * 0.5, sh - 3, z), (x + dx * 2.4, hip - 5, z))]
    pts += [((x, hip, z), (x, sh, z)), ((x - 18, sh, z), (x + 18, sh, z))]
    ring = [(x + 11 * np.cos(a), head + 11 * np.sin(a), z) for a in np.linspace(0, 2 * np.pi, 13)]
    pts += [(ring[i], ring[i + 1]) for i in range(12)]
    return [((np.array(a), np.array(b)), colour) for a, b in pts]
