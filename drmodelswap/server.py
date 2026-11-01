"""The tool's window: a small local web page served on 127.0.0.1 and opened in your browser.

Only this computer can reach it, and every request must carry a random token that only the page knows,
so other web pages can't drive it.
"""
import io, json, os, secrets, sys, threading, traceback, webbrowser

import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from . import __version__, blender, character, convert, export, game, glb, mod, models, render, rig, seats, tex

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')


def _config_path():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'DRModelSwap', 'config.json')


def load_config():
    try:
        with open(_config_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(c):
    p = _config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(c, f, indent=1)


def detect_game_dirs():
    """Dead Rising folders in the usual Steam places (and any extra Steam libraries)."""
    roots = []
    for env in ('ProgramFiles(x86)', 'ProgramFiles'):
        if os.environ.get(env):
            roots.append(os.path.join(os.environ[env], 'Steam'))
    roots.append(os.path.expanduser('~/.steam/steam'))
    libs = []
    for r in roots:
        libs.append(r)
        vdf = os.path.join(r, 'steamapps', 'libraryfolders.vdf')
        try:
            for line in open(vdf, encoding='utf-8', errors='replace'):
                line = line.strip()
                if line.startswith('"path"'):
                    libs.append(line.split('"')[3].replace('\\\\', '\\'))
        except OSError:
            pass
    out = []
    for lib in libs:
        p = os.path.join(lib, 'steamapps', 'common', 'Dead Rising')
        if os.path.isdir(os.path.join(p, 'nativeWin64')) and p not in out:
            out.append(p)
    return out


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.game = None
        self.scanning = False
        self.scan_error = ''
        self.glb = None          # (filename, parts, materials, info)
        self.new_model = self.new_texs = self.new_box = self.new_lines = None
        self.by_key = {}         # game model key -> {name, raw, glb, settings}: what each game model was given
        self.pending = None      # (key, payload, stats, settings, log)
        self.previews = {}       # cache of original previews
        self.new_preview = None
        self.pose_preview = None
        self.check_preview = None
        self.config = load_config()
        models.BLENDER['path'] = self.config.get('blender')


S = State()
TOKEN = secrets.token_urlsafe(24)


def _start_scan(force=False):
    g = S.game

    def run():
        try:
            g.scan(force=force)
            S.scan_error = ''
        except Exception as e:  # noqa: BLE001
            S.scan_error = str(e)
            traceback.print_exc()
        S.scanning = False
    S.scanning = True
    threading.Thread(target=run, daemon=True).start()


def set_game(path):
    g = game.Game(path)
    S.game = g
    S.previews.clear()
    S.pending = None
    S.config['game_dir'] = g.dir
    save_config(S.config)
    _start_scan()
    return g


class Handler(BaseHTTPRequestHandler):
    server_version = 'DRModelSwap/' + __version__

    def log_message(self, fmt, *args):  # quiet console
        pass

    # ----- helpers
    def _send(self, code, body, ctype='application/json'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _ok_origin(self):
        host = self.headers.get('Host', '')
        return host in ('127.0.0.1:%d' % self.server.server_port, 'localhost:%d' % self.server.server_port)

    def _authed(self, q=None):
        tok = self.headers.get('X-Token') or (q or {}).get('t', [''])[0]
        return self._ok_origin() and secrets.compare_digest(tok, TOKEN)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n else b''

    def _json(self):
        try:
            return json.loads(self._body() or b'{}')
        except ValueError:
            return {}

    def _png(self, img):
        b = io.BytesIO()
        img.save(b, 'PNG')
        self._send(200, b.getvalue(), 'image/png')

    # ----- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._ok_origin():
            return self._send(403, {'error': 'forbidden'})
        if u.path in ('/', '/index.html'):
            with open(os.path.join(WEB, 'index.html'), encoding='utf-8') as f:
                html = f.read().replace('__TOKEN__', TOKEN).replace('__VERSION__', __version__)
            return self._send(200, html, 'text/html; charset=utf-8')
        if not self._authed(q):
            return self._send(403, {'error': 'forbidden'})
        try:
            if u.path == '/api/state':
                g = S.game
                prog = g.progress if g else (0, 0, '')
                return self._send(200, dict(version=__version__, game=g.dir if g else '', scanning=S.scanning, scan_error=S.scan_error,
                                            progress=list(prog), models=len(g.models) if g else 0, coop=bool(g) and game.has_coop_mod(g.dir),
                                            swaps=g.manifest() if g else {}, detected=detect_game_dirs(),
                                            glb=S.glb[3] | dict(file=S.glb[0]) if S.glb else None,
                                            pending=dict(key=S.pending[0], stats=S.pending[2]) if S.pending else None))
            if u.path == '/api/models':
                g = S.game
                return self._send(200, [dict(key=k, **v) for k, v in sorted((g.models if g else {}).items())])
            if u.path == '/api/preview/original':
                key = q.get('key', [''])[0]
                if key not in S.previews:
                    om = mod.Model(S.game.original_model_bytes(key))
                    gs = seats.game_seat(S.game.model(key)['path'])
                    ln = (_riders(dict(driver=list(gs), passenger=list(gs * ([1, 1, 1] if _bike(key) else [-1, 1, 1]))), key) if gs is not None else []) or []
                    ln += _scale_figure(om, 'character' if rig.is_character(om) else 'other')
                    if not rig.is_character(om) and not rig.is_vehicle(S.game.model(key)['path'], om):
                        ln += _hand()
                    S.previews[key] = render.render(om, S.game.original_textures(key, om), lines=ln or None)
                return self._png(S.previews[key])
            if u.path == '/api/preview/new':
                if S.new_preview is None:
                    return self._send(404, {'error': 'no preview yet'})
                hl = q.get('hl', [''])[0]
                if hl and S.pending and S.new_model is not None:
                    # the Parts list's "Show": the piece's box drawn on the preview
                    e = next((x for x in S.pending[2].get('pieces', []) if x['id'] == hl and x.get('box')), None)
                    if e:
                        a, b = np.array(e['box'][0], float), np.array(e['box'][1], float)
                        pad = (b - a) * 0.05 + 1.0
                        a, b = a - pad, b + pad
                        cs = [np.array([x, y, z]) for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])]
                        edges = [(cs[i], cs[j]) for i in range(8) for j in range(i + 1, 8) if bin(i ^ j).count('1') == 1]
                        lines = [(ed, (255, 220, 0)) for ed in edges] + (S.new_lines or [])
                        return self._png(render.render(S.new_model, S.new_texs, box=S.new_box, lines=lines))
                return self._png(S.new_preview)
            if u.path == '/api/preview/check':
                if S.check_preview is None:
                    return self._send(404, {'error': 'no preview yet'})
                return self._png(S.check_preview)
            if u.path == '/api/preview/pose':
                if S.pose_preview is None:
                    return self._send(404, {'error': 'no preview yet'})
                return self._png(S.pose_preview)
            if u.path == '/api/export':
                key = q.get('key', [''])[0]
                m = S.game.model(key)
                om = mod.Model(S.game.original_model_bytes(key))
                data = export.export(m['path'], om, S.game.original_textures(key, om, 1024))
                body = data
                self.send_response(200)
                self.send_header('Content-Type', 'model/gltf-binary')
                self.send_header('Content-Disposition', 'attachment; filename="%s_original.glb"' % m['path'].rsplit('\\', 1)[-1])
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            return self._send(404, {'error': 'not found'})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {'error': str(e)})

    # ----- POST
    def do_POST(self):
        u = urlparse(self.path)
        if not self._authed():
            return self._send(403, {'error': 'forbidden'})
        try:
            if u.path == '/api/game':
                g = set_game(self._json().get('path', ''))
                return self._send(200, {'game': g.dir})
            if u.path == '/api/rescan':
                if S.game and not S.scanning:
                    _start_scan(force=True)
                return self._send(200, {})
            if u.path == '/api/blender':
                # where blender.exe is, when it isn't where installers put it
                p = (self._json().get('path') or '').strip().strip('"')
                if p and os.path.isdir(p):
                    p = os.path.join(p, 'blender.exe' if sys.platform.startswith('win') else 'blender')
                if not os.path.isfile(p):
                    raise ValueError('no Blender program at %s' % p)
                S.config['blender'] = p
                save_config(S.config)
                models.BLENDER['path'] = p
                return self._send(200, {'blender': p})
            if u.path == '/api/glb':
                data = self._body()
                name = unquote(self.headers.get('X-Filename', 'model.glb'))
                key = unquote(self.headers.get('X-Key', ''))
                parts, mats, info = models.load_upload(name, data)
                if not parts:
                    raise glb.GlbError('no triangle meshes in this model')
                shown = info.get('file', name)
                S.glb = (shown, parts, mats, info)
                if key:
                    # each game model remembers its own file (switching back shows it again)
                    S.by_key[key.lower()] = dict(name=name, raw=data, glb=S.glb)
                S.pending = None
                return self._send(200, dict(info, file=shown))
            if u.path == '/api/use':
                # the file this game model had: picked earlier in this session, or the one its installed swap was made from
                key = self._json().get('key', '').lower()
                e = S.by_key.get(key)
                settings = None
                if e is None and S.game:
                    src = S.game.load_source(key)
                    if src:
                        name, raw, settings = src
                        parts, mats, info = models.load_upload(name, raw)
                        e = S.by_key[key] = dict(name=name, raw=raw, glb=(info.get('file', name), parts, mats, info), settings=settings)
                S.pending = None
                if e is None:
                    S.glb = None
                    return self._send(200, {})
                S.glb = e['glb']
                return self._send(200, dict(info=dict(e['glb'][3], file=e['glb'][0]), settings=e.get('settings')))
            if u.path == '/api/convert':
                j = self._json()
                key = j.get('key', '')
                if not S.glb:
                    raise ValueError('pick a model file first')
                st = convert.Settings.from_dict(j.get('settings'))
                log = []
                orig = S.game.original_model_bytes(key)
                path = S.game.model(key)['path']
                with S.lock:
                    comps = {k: S.game.model_bytes(k) for k in convert.companions_needed(path)}
                    comps = {k: v for k, v in comps.items() if v}
                    payload, stats = convert.convert(path, orig, S.glb[1], S.glb[2], st, log=log.append, companions=comps)
                    extra = {k: convert.blank_model(k, comps[k]) for k in stats.get('hide', [])}
                    om, nm = mod.Model(orig), mod.Model(payload[0].data())
                    texs = {e.name: tex.decode(e.data(), 256) for e in payload[1:]}
                    S.new_lines = (_riders(stats.get('seats'), key) or []) + _scale_figure(nm, stats.get('kind')) + \
                        (_hand() if stats.get('kind') == 'rigid' else [])
                    S.new_model, S.new_texs, S.new_box = nm, texs, (om.box_min, om.box_max)
                    S.new_preview = render.render(nm, texs, box=(om.box_min, om.box_max), lines=S.new_lines)
                    pose, cap = rig.demo_pose(path, nm)
                    hot = None
                    if stats.get('kind') == 'character':
                        # stretched spots in orange; the walking test pose shows them best
                        try:
                            _, _, hot = character.model_stretch(nm)
                            pose, cap = character.TEST_POSES['walking'], 'walking (orange = stretched a lot)'
                        except Exception:  # noqa: BLE001
                            hot = None
                    S.pose_preview = render.render(nm, texs, pose=pose, hot=hot, yaw=200, pitch=-8) if pose else None
                    stats['pose_caption'] = cap if pose else ''
                    S.check_preview, seen = _see_through(nm, texs)
                    stats['see_through'] = round(seen, 4)
                    if seen > 0.015:
                        stats.setdefault('problems', []).append(dict(
                            level='warn', text='About %.0f%% of what shows of your model is the back of faces (magenta in the '
                            'see-through check): the game doesn\'t draw those, so you\'ll see through there. Usually holes '
                            'or single-layer surfaces in the model.' % (100 * seen),
                            fixes=[dict(setting='faces', value='both', label='Draw both sides of everything')] if st.faces != 'both' else []))
                    S.pending = (key, payload, stats, st, log, extra)
                return self._send(200, dict(stats=stats, log=log))
            if u.path == '/api/install':
                key = self._json().get('key', '')
                if not S.pending or S.pending[0] != key:
                    raise ValueError('make a preview for this model first')
                k, payload, stats, st, log, extra = S.pending
                with S.lock:
                    out = S.game.install(key, payload, dict(glb=S.glb[0], settings=st.to_dict(), stats=stats), extra=extra)
                    e = S.by_key.get(key.lower())
                    if e:
                        try:
                            S.game.save_source(key, e['name'], e['raw'])
                            e['settings'] = st.to_dict()
                        except OSError:
                            pass
                return self._send(200, dict(log=out))
            if u.path == '/api/remove':
                j = self._json()
                with S.lock:
                    out = S.game.remove_all() if j.get('all') else S.game.remove(j.get('key', ''))
                return self._send(200, dict(log=out))
            if u.path == '/api/quit':
                self._send(200, {})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
            return self._send(404, {'error': 'not found'})
        except blender.BlenderMissing as e:
            return self._send(400, {'error': str(e), 'need_blender': True})
        except (game.GameError, glb.GlbError, ValueError) as e:
            return self._send(400, {'error': str(e)})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {'error': '%s: %s' % (type(e).__name__, e)})


def _hand():
    """Frank's hand at a held item's origin: a small fist (red) and his forearm running back from it."""
    r = 4.0
    c = [np.array([x, y, z]) for x in (-r, r) for y in (-r, r) for z in (-r, r)]
    col = (255, 70, 70)
    out = [((c[i], c[j]), col) for i in range(8) for j in range(i + 1, 8) if bin(i ^ j).count('1') == 1]
    out.append(((np.array([0.0, 0.0, -r]), np.array([0.0, 0.0, -30.0])), col))
    return out


def _see_through(nm, texs):
    """The model from the front and from behind with the backs of faces in magenta (the game doesn't draw them: where
    magenta shows, you'd see through). -> (picture, share of the model's pixels that are magenta)."""
    from PIL import Image
    ims, seen, tot = [], 0, 0
    for yaw in (-140.0, 40.0):
        im = render.render(nm, texs, size=(280, 400), yaw=yaw, pitch=-15.0, see_through=True)
        a = np.asarray(im).astype(int)
        bg = (np.abs(a - np.array([38, 40, 46])).sum(2) < 12)
        mag = (a[:, :, 0] > 150) & (a[:, :, 1] < 90) & (a[:, :, 2] > 120)
        seen += int(mag.sum())
        tot += int((~bg).sum())
        ims.append(im)
    out = Image.new('RGB', (560, 400), (38, 40, 46))
    out.paste(ims[0], (0, 0))
    out.paste(ims[1], (280, 0))
    return out, seen / max(tot, 1)


def _scale_figure(model, kind):
    """A person of Frank's height (1.83 m) beside things big enough to compare (vehicles, long weapons), for scale."""
    if kind == 'character':
        return []
    P, _, _ = model.vertices()
    lo, hi = P.min(0), P.max(0)
    if float((hi - lo).max()) < 80:
        return []
    return render.person((lo, hi))


def _bike(key):
    return key.replace('/', '\\').rsplit('\\', 1)[-1].lower() in seats.BIKES


def _riders(st, key):
    """Stick figures where the driver (blue) and passenger (pink) sit; on a bike only the rider."""
    if not st:
        return None
    out = render.rider(st['driver'], (40, 210, 255), _bike(key))
    if not _bike(key):
        out += render.rider(st['passenger'], (255, 60, 200))
    return out


def serve(port=0, open_browser=True, game_dir=None):
    if game_dir:
        set_game(game_dir)
    elif S.config.get('game_dir'):
        try:
            set_game(S.config['game_dir'])
        except game.GameError:
            pass
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = 'http://127.0.0.1:%d/' % httpd.server_port
    print('DR Model Swap %s is running at %s' % (__version__, url))
    print('Keep this window open while you use it; close it (or press Quit on the page) to stop.')
    sys.stdout.flush()
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return url
