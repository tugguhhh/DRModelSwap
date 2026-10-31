"""Rig test against a game folder: every vehicle exported and put back onto every vehicle, the test cars / bike onto
the vehicles, and characters onto each other. Checks that each game wheel gets a wheel of the right size centred on its
axle (and that it stays in place when spun), and that characters bend with their bones.
python tests/rigs.py "C:\\...\\Dead Rising" [out folder for preview pictures]"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drmodelswap import convert, export, game, glb, mod, render, rig, tex
import make_test_car


def check_vehicle(path, orig, new, centred_ok=False):
    """Each game wheel bone carries a wheel of the game's size centred on its axle that stays in place when all wheels
    spin (a middle wheel shared by two bones counts for both)."""
    om, nm = mod.Model(orig), mod.Model(new)
    vr = rig.VehicleRig(path, om)
    bones = rig.read_bones(nm)
    P, _, _ = nm.vertices()
    J, W = rig.vertex_weights(nm)
    spin = {w.bone: rig.rot_x(90) for w in vr.wheels}
    for w in vr.wheels:
        if w.steers and w.hub is not None and w.hub > 0:
            spin[w.hub] = rig.rot_y(25)
    S = rig.skin(P, J, W, bones, rig.world_matrices(bones, spin))
    bad = []
    for w in vr.wheels:
        sel = ((J == w.bone) & (W > 0)).any(1)
        if not sel.any():
            bad.append('bone %d: no wheel' % w.bone)
            continue
        Q, piv = P[sel], bones[w.bone].pivot
        c = (Q.min(0) + Q.max(0)) / 2
        off = np.hypot(c[1] - piv[1], c[2] - piv[2])
        r = np.percentile(np.hypot(Q[:, 1] - piv[1], Q[:, 2] - piv[2]), 99.5)
        # spun (and steered): the wheel's radius around its own centre is unchanged
        Sq = S[sel]
        c2 = (Sq.min(0) + Sq.max(0)) / 2
        r2 = np.percentile(np.hypot(Sq[:, 1] - c2[1], Sq[:, 2] - c2[2]), 99.5) if not w.steers else r
        if off > 0.6 or abs(r - w.radius) > 1.5 or abs(r2 - r) > 2.0:
            bad.append('bone %d: off axle %.2f, radius %.1f/%.1f, spun radius %.1f' % (w.bone, off, r, w.radius, r2))
    return bad


def main():
    g = game.Game(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else None
    g.scan()
    vehicles = [k for k, m in g.models.items() if m['category'] == 'Vehicle' and m['supported']]
    import tempfile
    tmp = out or tempfile.mkdtemp(prefix='drms_rigs_')   # exported game models stay out of the source folder
    make_test_car.car(os.path.join(tmp, '_car.glb'))
    make_test_car.car(os.path.join(tmp, '_car_unnamed.glb'), named=False)
    make_test_car.bike(os.path.join(tmp, '_bike.glb'))
    make_test_car.trike(os.path.join(tmp, '_trike.glb'))
    make_test_car.trike(os.path.join(tmp, '_trike_back.glb'), front_single=False, named=False)
    sources = {'test car': os.path.join(tmp, '_car.glb'), 'test car (no names)': os.path.join(tmp, '_car_unnamed.glb'),
               'test bike': os.path.join(tmp, '_bike.glb'), 'test trike': os.path.join(tmp, '_trike.glb'),
               'test trike (back, no names)': os.path.join(tmp, '_trike_back.glb')}
    for k in vehicles:
        om = mod.Model(g.original_model_bytes(k))
        data = export.export(g.models[k]['path'], om, {})
        p = os.path.join(tmp, '_%s.glb' % k.rsplit('\\', 1)[-1])
        open(p, 'wb').write(data)
        sources['game ' + k.rsplit('\\', 1)[-1]] = p
    fails = 0
    for k in vehicles:
        orig = g.original_model_bytes(k)
        bike_like = len(rig.VehicleRig(k, mod.Model(orig)).wheels) == 2
        for name, p in sources.items():
            src_wheels = 2 if ('bike' in name or name.endswith(('009b', '009d'))) else 4
            if (src_wheels == 2) != bike_like:
                continue
            parts, mats, _ = glb.load(open(p, 'rb').read())
            for size in ('wheels', 'footprint'):
                payload, stats = convert.convert(g.models[k]['path'], orig, parts, mats, convert.Settings.from_dict(dict(size=size)), log=lambda *a: None)
                bad = check_vehicle(k, orig, payload[0].data())
                fails += bool(bad)
                print('%-28s <- %-22s %-9s %s' % (k, name, size, 'OK' if not bad else 'PROBLEM: ' + '; '.join(bad)))
            if out and size == 'footprint':
                nm = mod.Model(payload[0].data())
                pose, _ = rig.demo_pose(k, nm)
                render.render(nm, {e.name: tex.decode(e.data(), 64) for e in payload[1:]}, pose=pose).save(
                    os.path.join(out, '%s_from_%s.png' % (k.rsplit('\\', 1)[-1], name.replace(' ', '_'))))
    print('vehicles: %d problem(s)' % fails)
    # characters: one exported person onto every character; bent with the demo pose, no edge may stretch a lot
    chars = [k for k, m in g.models.items() if m['category'] == 'Character' and m['supported'] and '\\cos\\' not in k]
    cfails = 0
    if chars:
        src = next((k for k in chars if '\\npc\\' in k), chars[0])
        sm = mod.Model(g.original_model_bytes(src))
        tpose = glb.load(export.export(g.models[src]['path'], sm, {}))
        # the same person in an A-pose (arms 45 degrees down), like many downloaded models
        bones = rig.read_bones(sm)
        Pa, Na, UVa = sm.vertices()
        Ja, Wa = rig.vertex_weights(sm)
        Pa = rig.skin(Pa, Ja, Wa, bones, rig.world_matrices(bones, {12: rig.rot_z(45), 16: rig.rot_z(-45)}))
        tris = np.concatenate([np.array(sm.triangles(p), np.int64).reshape(-1, 3) for p in sm.pieces if p['flags'][1] & 1])
        from drmodelswap import glbwrite
        apose = glb.load(glbwrite.write(None, [dict(name='A-pose', prims=[(Pa / 100, Na, UVa, tris, 0)])], [dict(name='m')]))
        # a cartoon build (like an ogre): legs 40% shorter, arms 50% longer, body 30% wider
        Ps, _, _ = sm.vertices()
        Ps = Ps.copy()
        hip_y = bones[0].pivot[1]
        floor = Ps[:, 1].min()
        crotch = hip_y - 0.18 * (hip_y - floor)
        leg = Ps[:, 1] < crotch
        Ps[leg, 1] = crotch + (Ps[leg, 1] - crotch) * 0.6
        sh = abs(bones[12].pivot[0])
        arm = ~leg & (np.abs(Ps[:, 0]) > sh)
        Ps[arm, 0] = np.sign(Ps[arm, 0]) * (sh + (np.abs(Ps[arm, 0]) - sh) * 1.5)
        Ps[~leg, 0] *= 1.3
        stubby = glb.load(glbwrite.write(None, [dict(name='stubby', prims=[(Ps / 100, Na, UVa, tris, 0)])], [dict(name='m')]))
        poses = {'demo': None, 'walk': {12: rig.rot_z(70) @ rig.rot_y(25), 16: rig.rot_z(-70) @ rig.rot_y(25), 1: rig.rot_x(-30), 2: rig.rot_x(20), 4: rig.rot_x(25), 5: rig.rot_x(45)},
                 'elbows': {12: rig.rot_z(60), 16: rig.rot_z(-60), 13: rig.rot_y(-90), 17: rig.rot_y(90)},
                 'crouch': {1: rig.rot_x(-70), 2: rig.rot_x(100), 4: rig.rot_x(-70), 5: rig.rot_x(100), 12: rig.rot_z(50), 16: rig.rot_z(-50), 7: rig.rot_x(20)}}
        base = {}

        def stretch(m, poses, demo):
            bo = rig.read_bones(m)
            P, _, _ = m.vertices()
            J, W = rig.vertex_weights(m)
            near = min(p['flags'][1] for p in m.pieces)
            T = np.concatenate([np.array(m.triangles(p), np.int64).reshape(-1, 3) for p in m.pieces if p['flags'][1] == near])
            e = np.r_[T[:, :2], T[:, 1:]]
            l0 = np.linalg.norm(P[e[:, 0]] - P[e[:, 1]], axis=1)
            worst, p99 = 0.0, 0.0
            for pz in poses.values():
                S = rig.skin(P, J, W, bo, rig.world_matrices(bo, pz or demo))
                l1 = np.linalg.norm(S[e[:, 0]] - S[e[:, 1]], axis=1)
                r = np.maximum(l1, 0.5) / np.maximum(l0, 0.5)
                # worst single edge: stretching (a fold at the back of a deeply bent knee squashes on any model);
                # the 99th percentile counts squashing too
                worst = max(worst, float(r.max()))
                p99 = max(p99, float(np.percentile(np.maximum(r, 1 / r), 99)))
            return worst, p99
        for k, (label, (parts, mats, _)) in [(k, x) for k in chars for x in (('T-pose', tpose), ('A-pose', apose), ('stubby', stubby))]:
            path = g.models[k]['path']
            comps = {c: g.model_bytes(c) for c in convert.companions_needed(path)}
            comps = {c: v for c, v in comps.items() if v}
            log = []
            payload, stats = convert.convert(path, g.original_model_bytes(k), parts, mats, convert.Settings.from_dict(dict(size='height')),
                                             log=log.append, companions=comps)
            found = [x for x in log if x.startswith('Pose of your model')]
            nm = mod.Model(payload[0].data())
            bones = rig.read_bones(nm)
            P, _, _ = nm.vertices()
            J, W = rig.vertex_weights(nm)
            pose, _ = rig.demo_pose(path, nm)
            worst, p99 = stretch(nm, poses, pose)
            if k not in base:
                base[k] = stretch(mod.Model(g.original_model_bytes(k)), poses, pose)
            bw, b99 = base[k]
            # the game's own model bends with some stretch too (knees at 100 degrees): judged against that
            bad = p99 > max(1.5 * b99, b99 + 0.4) or worst > max(2.5 * bw, 8.0)
            cfails += bad
            prop = [x for x in log if x.startswith('Body fitted')]
            print('%-28s <- %-22s stretch max x%.1f (game x%.1f), 99%% x%.2f (game x%.2f) %s (%d pieces, %s) %s | %s' % (k, src.rsplit('\\', 1)[-1] + ' ' + label, worst, bw, p99, b99, 'PROBLEM' if bad else 'OK', nm.n_pieces,
                                                                    'hides ' + ', '.join(x.rsplit('\\', 1)[-1] for x in stats.get('hide', [])) if stats.get('hide') else 'nothing hidden',
                                                                    found[0][len('Pose of your model: '):] if found else '', prop[0].split(': ', 1)[-1] if prop else ''))
            if out:
                render.render(nm, {e2.name: tex.decode(e2.data(), 64) for e2 in payload[1:]}, pose=pose, yaw=200, pitch=-5).save(
                    os.path.join(out, '%s_%s_posed.png' % (k.rsplit('\\', 1)[-1], label)))
    print('characters: %d problem(s)' % cfails)
    return 1 if fails or cfails else 0


if __name__ == '__main__':
    sys.exit(main())
