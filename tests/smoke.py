"""Smoke test: scan a game folder, convert a .glb into a model, render previews, install and remove."""
import sys, os, time, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from drmodelswap import game, glb, convert, mod, render, arc

gdir, key, glbfile = sys.argv[1], sys.argv[2], sys.argv[3]
out = sys.argv[4] if len(sys.argv) > 4 else __import__('tempfile').mkdtemp()
g = game.Game(gdir)
t = time.time(); ms = g.scan(force=True); print('scan', len(ms), 'models', round(time.time() - t, 1), 's')
info = g.model(key); print(info)
orig = g.original_model_bytes(key); om = mod.Model(orig)
render.render(om, g.original_textures(key, om)).save(os.path.join(out, 'orig.png'))
parts, mats, gi = glb.load(open(glbfile, 'rb').read()); print('glb', gi)
st = convert.Settings.from_dict(dict(yaw=int(sys.argv[5]) if len(sys.argv) > 5 else 0, size=sys.argv[6] if len(sys.argv) > 6 else 'footprint'))
t = time.time(); payload, stats = convert.convert(info['path'], orig, parts, mats, st); print('convert', round(time.time() - t, 1), 's', stats)
nm = mod.Model(payload[0].data())
from drmodelswap import tex
texs = {e.name: tex.decode(e.data(), 256) for e in payload[1:]}
t = time.time(); render.render(nm, texs, box=(om.box_min, om.box_max)).save(os.path.join(out, 'new.png')); print('render', round(time.time() - t, 1), 's')
before = {a: hashlib.md5(open(os.path.join(g.native, a), 'rb').read()).hexdigest() for a in info['arcs']}
print(g.install(key, payload, dict(glb=os.path.basename(glbfile), settings=st.to_dict(), stats=stats)))
for a in info['arcs']:
    d = arc.read_entry(os.path.join(g.native, a), info['path'], arc.TYPE_MODEL)
    assert d == payload[0].data(), a
print('installed ok in', info['arcs'])
print(g.remove(key))
after = {a: hashlib.md5(open(os.path.join(g.native, a), 'rb').read()).hexdigest() for a in info['arcs']}
assert before == after, 'restore mismatch'
print('restored byte-identical')
