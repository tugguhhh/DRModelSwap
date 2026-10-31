"""Other 3D formats (.fbx, .blend, .dae, .3ds, .stl, .ply, .x3d, .usd, .abc) through Blender, if it's installed:
Blender opens the file in the background (no window) and saves it as .glb, which the tool then reads as usual. The
textures the file refers to are found among the files given (or the folder), and Blender's export converts texture
formats the tool can't read (.psd, .tga...) on the way.

Blender is looked for where its installers put it (Program Files, Steam, the Microsoft Store's link, macOS
Applications, the PATH); a different place can be set in the tool (saved in its config)."""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

EXTS = ('.fbx', '.blend', '.dae', '.3ds', '.stl', '.ply', '.x3d', '.wrl', '.usd', '.usda', '.usdc', '.usdz', '.abc')


class BlenderMissing(Exception):
    pass


def _version_key(path):
    import re
    m = re.findall(r'(\d+)\.(\d+)', path)
    return tuple(int(x) for x in m[-1]) if m else (0, 0)


def candidates():
    """Places Blender is usually installed, most likely first."""
    out = []
    env = os.environ.get('DRMS_BLENDER')
    if env:
        out.append(env)
    w = shutil.which('blender')
    if w:
        out.append(w)
    if sys.platform.startswith('win'):
        roots = [os.environ.get(k) for k in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)', 'LOCALAPPDATA')]
        for r in [r for r in roots if r]:
            found = glob.glob(os.path.join(r, 'Blender Foundation', '*', 'blender.exe')) + \
                glob.glob(os.path.join(r, 'Programs', 'Blender Foundation', '*', 'blender.exe'))
            out += sorted(found, key=_version_key, reverse=True)
        # Steam (Blender is on Steam too), every library folder
        for steam in [os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'Steam'),
                      os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Steam')]:
            libs = [steam]
            vdf = os.path.join(steam, 'steamapps', 'libraryfolders.vdf')
            try:
                import re
                with open(vdf, encoding='utf-8', errors='replace') as f:
                    libs += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
            except OSError:
                pass
            for lib in libs:
                out.append(os.path.join(lib, 'steamapps', 'common', 'Blender', 'blender.exe'))
        la = os.environ.get('LOCALAPPDATA')
        if la:
            out.append(os.path.join(la, 'Microsoft', 'WindowsApps', 'blender.exe'))   # Microsoft Store's link
    elif sys.platform == 'darwin':
        out += ['/Applications/Blender.app/Contents/MacOS/Blender', os.path.expanduser('~/Applications/Blender.app/Contents/MacOS/Blender')]
    else:
        out += ['/usr/bin/blender', '/usr/local/bin/blender', '/snap/bin/blender', os.path.expanduser('~/blender/blender')]
    return out


def find(configured=None):
    """-> path of blender(.exe) or None."""
    for p in ([configured] if configured else []) + candidates():
        if p and os.path.isfile(p):
            return p
    return None


SCRIPT = r'''
import bpy, os, sys
argv = sys.argv[sys.argv.index('--') + 1:]
src, out = argv[0], argv[1]
ext = os.path.splitext(src)[1].lower()

def call(names, **kw):
    """The first import operator that exists in this Blender version (they were renamed over the years)."""
    last = None
    for n in names:
        mod, op = n.split('.')
        try:
            f = getattr(getattr(bpy.ops, mod), op)
            f.get_rna_type()            # raises when this Blender has no such operator
        except Exception as e:
            last = e
            continue
        try:
            return f(**kw)
        except (AttributeError, RuntimeError, TypeError, KeyError) as e:
            last = e
    raise RuntimeError('this Blender can\'t read %s files (%s)' % (ext, last))

def addon(name):
    try:
        import addon_utils
        addon_utils.enable(name, default_set=True)
    except Exception:
        pass

if ext != '.blend':
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext == '.fbx':
        call(['import_scene.fbx'], filepath=src)
    elif ext == '.dae':
        call(['wm.collada_import'], filepath=src)
    elif ext == '.3ds':
        addon('io_scene_3ds')
        call(['import_scene.max3ds', 'import_scene.autodesk_3ds'], filepath=src)
    elif ext == '.stl':
        call(['wm.stl_import', 'import_mesh.stl'], filepath=src)
    elif ext == '.ply':
        call(['wm.ply_import', 'import_mesh.ply'], filepath=src)
    elif ext in ('.x3d', '.wrl'):
        addon('io_scene_x3d')
        call(['import_scene.x3d'], filepath=src)
    elif ext in ('.usd', '.usda', '.usdc', '.usdz'):
        call(['wm.usd_import'], filepath=src)
    elif ext == '.abc':
        call(['wm.alembic_import'], filepath=src, as_background_job=False)
    else:
        raise RuntimeError('unknown format ' + ext)
# textures the file points to somewhere else (another computer's folders): look for them next to it
try:
    bpy.ops.file.find_missing_files(directory=os.path.dirname(src))
except Exception:
    pass
# rigged models in their rest pose (the tool does its own rigging)
for ob in bpy.data.objects:
    if ob.type == 'ARMATURE':
        ob.data.pose_position = 'REST'
kw = dict(filepath=out, export_format='GLB', export_apply=True, export_animations=False, export_yup=True,
          export_texcoords=True, export_normals=True, export_materials='EXPORT', export_image_format='AUTO',
          use_visible=False, export_extras=False, export_cameras=False, export_lights=False, export_draco_mesh_compression_enable=False)
for _ in range(len(kw)):
    try:
        bpy.ops.export_scene.gltf(**kw)
        break
    except TypeError as e:
        # an option this Blender version doesn't have: drop it and try again
        bad = [k for k in kw if k in str(e) and k != 'filepath']
        if not bad:
            raise
        kw.pop(bad[0])
print('DRMS: exported', out)
'''


def _safe_rel(name):
    """A file name from the upload as a path inside the temp folder (no absolute paths, no '..')."""
    parts = [p for p in name.replace('\\', '/').split('/') if p not in ('', '.', '..') and ':' not in p]
    return os.path.join(*parts) if parts else 'model'


def convert(files, main, exe, timeout=300, log=None):
    """files: {relative path: bytes} (the model and anything it uses); main: the model's path among them.
    -> .glb bytes. Runs Blender without a window."""
    with tempfile.TemporaryDirectory(prefix='drms_blender_') as d:
        for name, data in files.items():
            p = os.path.join(d, 'in', _safe_rel(name))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'wb') as f:
                f.write(data)
        return convert_path(os.path.join(d, 'in', _safe_rel(main)), exe, timeout, log)


def convert_path(src, exe, timeout=300, log=None):
    """A model file on disk (its textures are looked for next to it) -> .glb bytes."""
    main = src
    with tempfile.TemporaryDirectory(prefix='drms_blender_out_') as d:
        out = os.path.join(d, 'out.glb')
        script = os.path.join(d, 'drms_export.py')
        with open(script, 'w', encoding='utf-8') as f:
            f.write(SCRIPT)
        cmd = [exe, '-b', '--factory-startup'] + ([src] if src.lower().endswith('.blend') else []) + \
            ['--python', script, '--', src, out]
        flags = 0x08000000 if sys.platform.startswith('win') else 0      # CREATE_NO_WINDOW
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Blender took more than %d s to convert %s' % (timeout, os.path.basename(main)))
        text = (r.stdout or b'').decode('utf-8', 'replace') + (r.stderr or b'').decode('utf-8', 'replace')
        if not os.path.isfile(out):
            errs = [l for l in text.splitlines() if 'Error' in l or 'error' in l or 'can\'t' in l]
            raise RuntimeError('Blender couldn\'t convert %s: %s' % (os.path.basename(main), (errs[-1] if errs else text[-400:]).strip()))
        if log:
            log('Converted %s to .glb with Blender (%s)' % (os.path.basename(main), exe))
        with open(out, 'rb') as f:
            return f.read()
