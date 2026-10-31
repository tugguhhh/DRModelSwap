"""Two swaps in one bundle file: removing one keeps the other; removing both gives the original file back."""
import sys, os, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from drmodelswap import game, glb, convert, arc

gdir, a, b, glbfile = sys.argv[1:5]
g = game.Game(gdir); g.scan()
parts, mats, _ = glb.load(open(glbfile, 'rb').read())
shared = sorted(set(g.model(a)['arcs']) & set(g.model(b)['arcs']))
assert shared, 'the two models share no game file'
before = {f: hashlib.md5(open(os.path.join(g.native, f), 'rb').read()).hexdigest() for f in shared}
pa = {}
for k in (a, b):
    pay, _ = convert.convert(g.model(k)['path'], g.original_model_bytes(k), parts, mats, convert.Settings(), log=lambda *x: None)
    pa[k] = pay[0].data()
    g.install(k, pay, dict(glb='test'))
for f in shared:
    for k in (a, b):
        assert arc.read_entry(os.path.join(g.native, f), g.model(k)['path'], arc.TYPE_MODEL) == pa[k], (f, k)
g.remove(a)
for f in shared:
    assert arc.read_entry(os.path.join(g.native, f), g.model(b)['path'], arc.TYPE_MODEL) == pa[b]
    assert arc.read_entry(os.path.join(g.native, f), g.model(a)['path'], arc.TYPE_MODEL) == arc.read_entry(os.path.join(g.backup, f), g.model(a)['path'], arc.TYPE_MODEL)
g.remove(b)
after = {f: hashlib.md5(open(os.path.join(g.native, f), 'rb').read()).hexdigest() for f in shared}
assert before == after
print('shared bundle test passed:', shared)
