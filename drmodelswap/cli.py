"""Command line: python -m drmodelswap [gui|list|swap|restore] ...  (no arguments = open the window)."""
import argparse, os, sys

from . import __version__, convert, export, game, mod, models


def main(argv=None):
    ap = argparse.ArgumentParser(prog='drmodelswap', description='Replace Dead Rising (PC) object, vehicle and character models with .glb, .gltf or .obj models.')
    ap.add_argument('--version', action='version', version=__version__)
    sub = ap.add_subparsers(dest='cmd')
    g = sub.add_parser('gui', help='open the tool in your browser (default)')
    g.add_argument('--game', help='Dead Rising folder')
    g.add_argument('--port', type=int, default=0)
    g.add_argument('--no-browser', action='store_true')
    ls = sub.add_parser('list', help='list the models you can replace')
    ls.add_argument('game')
    ls.add_argument('query', nargs='?', default='')
    ls.add_argument('--all', action='store_true', help='also list models the tool cannot replace')
    sw = sub.add_parser('swap', help='replace a model with a .glb, .gltf or .obj (or a folder / .zip holding one)')
    sw.add_argument('game')
    sw.add_argument('model', help='model path (model\\om\\om0098\\om0098) or just its name (om0098)')
    sw.add_argument('glb', metavar='model', help='.glb, .gltf or .obj file (its textures are read from around it), a folder, or a .zip')
    sw.add_argument('--yaw', type=int, default=0, choices=[0, 90, 180, 270], help='turn by hand (without --yaw / --tilt the model is turned automatically)')
    sw.add_argument('--tilt', default='none', choices=list(convert.TILTS) + ['auto'])
    sw.add_argument('--size', default='auto', choices=['auto', 'footprint', 'fit', 'long', 'real', 'wheels', 'height'])
    sw.add_argument('--scale', type=float, default=1.0)
    sw.add_argument('--place', default='ground', choices=['ground', 'center', 'origin'])
    sw.add_argument('--max-texture', type=int, default=1024)
    sw.add_argument('--max-vertices', type=int, default=60000)
    sw.add_argument('--wheels', default='auto', choices=['auto', 'off'], help='vehicles: make the wheels spin and steer')
    sw.add_argument('--wheel-size', default='game', choices=['game', 'mine'])
    sw.add_argument('--wheel-x', default='mine', choices=['mine', 'game'])
    sw.add_argument('--clothes', default='default', choices=['default', 'keep'], help='Frank: hide his starting clothes')
    sw.add_argument('--wheel-fit', default='axles', choices=['axles', 'model'], help='vehicles: wheels onto the game axles, or game wheel bones moved to your wheels (test)')
    sw.add_argument('--pose', default='auto', choices=['auto', 'as_is'], help='characters: straighten A-pose models to the T-pose')
    sw.add_argument('--seats', default=None, choices=['fit', 'auto', 'game'], help='vehicles: fit = move the model so its driver seat is where the game seats Frank (any game); auto = riders drawn in your model\'s seats by the co-op mod; game = as the original. Default: auto with the co-op mod installed, else fit')
    sw.add_argument('--faces', default='fix', choices=['fix', 'both', 'as_is'], help='fix = inside-out faces turned round (they\'d be holes in the game); both = every face drawn from both sides')
    sw.add_argument('--grip', default='auto', choices=['auto', 'box'], help='hand-held items: auto = a handle found goes in Frank\'s hand; box = fill the original\'s space')
    sw.add_argument('--hold', type=float, nargs=3, default=[0.0, 0.0, 0.0], metavar=('X', 'Y', 'Z'), help='hand-held items: move it in the hand (game units: sideways, up, forward)')
    sw.add_argument('--crotch', type=float, default=0.0, help='characters: where the legs part, %% of the height (0 = automatic)')
    sw.add_argument('--webs', default='split', choices=['split', 'keep'], help='characters: cut cloth that joins the two legs down the middle')
    sw.add_argument('--neck', default='seal', choices=['seal', 'rigid'], help='characters: a head piece edge lying on the body bends with the body')
    sw.add_argument('--limbs', default='own_side', choices=['own_side', 'nearest'], help='characters: legs and arms take weights only from their own side')
    sw.add_argument('--lods', default='auto', choices=['auto', 'full', 'thin'], help='zombies: what is drawn far away')
    sw.add_argument('--mod', metavar='FILE', help='write a DeadRisingEx mod (.arc, or .zip with a readme) instead of changing the game files; FILE = mods to put it in nativeWin64\\Mods')
    sw.add_argument('--loose', metavar='FOLDER', nargs='?', const='', help='write the converted .mod / .tex files into FOLDER (default: next to your model) instead of changing the game files')
    sw.add_argument('--body', default='fit', choices=['fit', 'as_is'], help='characters: fit legs and arms onto the game skeleton\'s joints')
    ex = sub.add_parser('export', help='save a game model as .glb (a reference to build on in Blender)')
    ex.add_argument('game')
    ex.add_argument('model')
    ex.add_argument('out')
    rs = sub.add_parser('restore', help='put an original model back')
    rs.add_argument('game')
    rs.add_argument('model', nargs='?')
    rs.add_argument('--all', action='store_true')
    a = ap.parse_args(argv)

    if a.cmd in (None, 'gui'):
        from . import server
        server.serve(port=getattr(a, 'port', 0), open_browser=not getattr(a, 'no_browser', False), game_dir=getattr(a, 'game', None))
        return 0
    gm = game.Game(a.game)
    print('reading the game files (slow only the first time)...')
    gm.scan()

    resolve = gm.resolve

    if a.cmd == 'list':
        q = a.query.lower()
        for k, m in sorted(gm.models.items()):
            if (m['supported'] or a.all) and (q in k or q in (m['name'] or '').lower()):
                print('%-28s %-9s %-40s %s' % (m['path'], m['category'], (m['name'] or '')[:40], '' if m['supported'] else '(' + m['reason'] + ')'))
        return 0
    if a.cmd == 'swap':
        key = resolve(a.model)
        parts, mats, info = models.load_path(a.glb)
        print('%s: %d vertices, %d triangles, %d materials' % (info['file'], info['vertices'], info['triangles'], info['materials']))
        if info.get('missing'):
            print('  not found (drawn in plain colour): ' + ', '.join(info['missing']))
        st = convert.Settings(yaw=a.yaw, tilt=a.tilt, size=a.size, scale=a.scale, place=a.place, max_texture=a.max_texture, max_vertices=a.max_vertices,
                              wheels=a.wheels, wheel_size=a.wheel_size, wheel_x=a.wheel_x, clothes=a.clothes,
                              wheel_fit=a.wheel_fit, pose=a.pose, body=a.body, faces=a.faces, grip=a.grip, hold_x=a.hold[0], hold_y=a.hold[1],
                              hold_z=a.hold[2], crotch=a.crotch, webs=a.webs, neck=a.neck, limbs=a.limbs, lods=a.lods,
                              seats=a.seats or ('auto' if game.has_coop_mod(gm.dir) else 'fit'),
                              orient='manual' if (a.yaw or a.tilt != 'none') else 'auto')
        path = gm.model(key)['path']
        comps = {k: gm.model_bytes(k) for k in convert.companions_needed(path)}
        comps = {k: v for k, v in comps.items() if v}
        payload, stats = convert.convert(path, gm.original_model_bytes(key), parts, mats, st, companions=comps)
        for pb in stats.get('problems', []):
            print('[%s] %s%s' % (pb['level'], pb['text'], (' (try: ' + '; '.join('--%s %s' % (f['setting'].replace('_', '-'), f['value']) for f in pb['fixes']) + ')') if pb['fixes'] else ''))
        extra = {k: convert.blank_model(k, comps[k]) for k in stats.get('hide', [])}
        if a.loose is not None:
            folder = a.loose or os.path.dirname(os.path.abspath(a.glb))
            for f in game.write_loose(folder, payload, extra):
                print('wrote ' + f)
            print('(DRMS_files.txt lists their game paths and types.) The game files were not changed.')
            return 0
        if a.mod:
            name = game.mod_name(path, a.glb)
            if a.mod.lower() == 'mods':
                print('DeadRisingEx mod saved: ' + gm.save_mod(name, payload, extra))
            elif a.mod.lower().endswith('.zip'):
                with open(a.mod, 'wb') as f:
                    f.write(game.mod_zip(name, payload, extra, '%s replaced by %s' % (path, os.path.basename(a.glb)), __version__,
                                         os.path.basename(a.glb)))
                print('DeadRisingEx mod (with readme) written: ' + a.mod)
            else:
                game.write_mod(a.mod, payload, extra)
                print('DeadRisingEx mod written: ' + a.mod)
            print('Enable it in DeadRisingLauncher.exe > Settings > Mods. The game files were not changed.')
            return 0
        for line in gm.install(key, payload, dict(glb=os.path.basename(a.glb), settings=st.to_dict(), stats=stats), extra=extra):
            print(line)
        return 0
    if a.cmd == 'export':
        key = resolve(a.model)
        om = mod.Model(gm.original_model_bytes(key))
        export.export(gm.model(key)['path'], om, gm.original_textures(key, om, 1024), a.out)
        print('saved', a.out)
        return 0
    if a.cmd == 'restore':
        out = gm.remove_all() if a.all else gm.remove(resolve(a.model or ''))
        for line in out:
            print(line)
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
