"""The game folder: finding models, keeping backups, installing and removing swaps.

Everything the tool writes lives in <game>\\DRModelSwap\\:
  backup\\<arc>        untouched copy of every game archive the tool has changed (made before the first change)
  swaps\\<model>.arc   the converted model + textures of each active swap
  manifest.json       which swaps are active and which archives they touch
  index.json          cached model list
Installing or removing a swap rebuilds each affected archive from its backup with all active swaps applied,
so several swaps can share an archive and removing one never disturbs another.
"""
import json, os, re, shutil, time, zlib

from . import arc, mod, names, rig, tex

CATEGORIES = {'om': 'Object', 'em': 'Enemy', 'npc': 'Survivor', 'pl': 'Player', 'ev': 'Cutscene'}


class GameError(Exception):
    pass


def find_game_dir(path):
    """Accepts the game folder, its nativeWin64 folder, or DeadRising.exe."""
    p = os.path.abspath(path.strip().strip('"'))
    if os.path.isfile(p):
        p = os.path.dirname(p)
    if os.path.basename(p).lower() == 'nativewin64':
        p = os.path.dirname(p)
    if not os.path.isdir(os.path.join(p, 'nativeWin64')):
        raise GameError('No nativeWin64 folder in %s. Pick the Dead Rising folder (the one with DeadRising.exe).' % p)
    return p


def has_coop_mod(game_dir):
    """The Dead Rising co-op mod is installed (it reads DRModelSwap\\seats.txt and draws the riders in a swapped vehicle's
    own seats)."""
    return os.path.isfile(os.path.join(game_dir, 'coop_probe.dll'))


def mod_name(target_path, source_file):
    """File name for a DeadRisingEx mod: DRMS_<game model>_<your file>.arc, letters / digits / _ / - only."""
    base = target_path.replace('/', '\\').rsplit('\\', 1)[-1]
    src = os.path.splitext(os.path.basename(source_file or 'model'))[0]
    clean = re.sub(r'[^A-Za-z0-9_-]+', '_', '%s_%s' % (base, src)).strip('_')[:60]
    return 'DRMS_%s.arc' % clean


def write_mod(path, payload, extra=None):
    """One .arc with the swap's files (and, for Frank, the emptied clothes models): what DeadRisingEx loads over the
    game's own files."""
    ents, seen = [], set()
    for e in list(payload) + [e for k in sorted(extra or {}) for e in extra[k]]:
        if (e.name.lower(), e.type) not in seen:
            seen.add((e.name.lower(), e.type))
            ents.append(e)
    arc.write_arc(path, 4, ents)


LOOSE_EXT = {arc.TYPE_MODEL: '.mod', arc.TYPE_TEXTURE: '.tex'}


def write_loose(folder, payload, extra=None):
    """The swap's files as plain files (not packed in an archive), in the game's folder layout:
    folder\\model\\om\\om00a6\\om00a6.mod (rModel), ..._BM.tex (rTexture)... plus DRMS_files.txt listing each file's
    game path and type id, for packing with other tools. -> list of the files written."""
    out, seen, lines = [], set(), []
    for e in list(payload) + [e for k in sorted(extra or {}) for e in extra[k]]:
        if (e.name.lower(), e.type) in seen:
            continue
        seen.add((e.name.lower(), e.type))
        rel = e.name.replace('/', '\\').split('\\')
        if any(x in ('', '.', '..') for x in rel) or ':' in e.name:
            raise ValueError('unexpected file name in the swap: %r' % e.name)
        p = os.path.join(folder, *rel) + LOOSE_EXT.get(e.type, '.%08X' % e.type)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = e.data()
        with open(p, 'wb') as f:
            f.write(data)
        out.append(p)
        lines.append('%-48s %-6s type 0x%08X  %8d bytes' % (e.name, LOOSE_EXT.get(e.type, '')[1:] or '?', e.type, len(data)))
    with open(os.path.join(folder, 'DRMS_files.txt'), 'w', encoding='utf-8') as f:
        f.write('Files made by DR Model Swap: game path, kind (mod = rModel, tex = rTexture), type id in the .arc, size.\n'
                'Pack them into the arc that holds that game path (or a DeadRisingEx mod .arc) with your own tools.\n\n')
        f.write('\n'.join(lines) + '\n')
    return out


MOD_README = """{title}
{line}

A Dead Rising (PC, 2016) model swap: {what}.
Made with DR Model Swap {version}{source}.

Install (needs DeadRisingEx: https://github.com/grimdoomer/DeadRisingEx):
1. Copy {arc} into  Dead Rising\\nativeWin64\\Mods\\  (make the Mods folder if it isn't there).
2. Open DeadRisingLauncher.exe > Settings > Mods, tick {arc}.
3. Start the game from DeadRisingLauncher.exe (Steam running).

To remove it, untick it (or delete the file). The game's own files are never changed.
In co-op, everyone needs the same mods enabled.
"""


def mod_zip(arc_name, payload, extra, what, version, source=''):
    """A shareable .zip: the mod .arc and a short readme. -> bytes."""
    import io
    import tempfile
    import zipfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, arc_name)
        write_mod(p, payload, extra)
        with open(p, 'rb') as f:
            data = f.read()
    title = os.path.splitext(arc_name)[0]
    readme = MOD_README.format(title=title, line='=' * len(title), what=what, version=version, arc=arc_name,
                               source=(' from ' + source) if source else '')
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(arc_name, data)
        z.writestr('README.txt', readme.replace('\n', '\r\n'))
    return b.getvalue()


class Game:
    def __init__(self, path):
        self.dir = find_game_dir(path)
        self.native = os.path.join(self.dir, 'nativeWin64')
        self.data = os.path.join(self.dir, 'DRModelSwap')
        self.backup = os.path.join(self.data, 'backup')
        self.swaps = os.path.join(self.data, 'swaps')
        self.models = {}  # key (lower path) -> info dict
        self.progress = (0, 0, '')

    # ----- files
    def original_arc(self, name):
        b = os.path.join(self.backup, name)
        return b if os.path.exists(b) else os.path.join(self.native, name)

    def _arc_files(self):
        return sorted(f for f in os.listdir(self.native) if f.lower().endswith('.arc'))

    def manifest(self):
        try:
            with open(os.path.join(self.data, 'manifest.json'), encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_manifest(self, m):
        os.makedirs(self.data, exist_ok=True)
        tmp = os.path.join(self.data, 'manifest.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(m, f, indent=1)
        os.replace(tmp, os.path.join(self.data, 'manifest.json'))
        self._write_seats(m)

    def _write_seats(self, man):
        """DRModelSwap/seats.txt: how far the riders' seats moved in each swapped vehicle, for the co-op mod (it draws
        the driver and passenger there). Written from the installed swaps every time they change."""
        lines = ['# DR Model Swap: seats of swapped vehicles, read by the co-op mod (v31h or newer; moves over 400 need v31i).',
                 '# vehicle  driver dx dy dz  passenger dx dy dz  (game units, the vehicle\'s frame: +x its left, +y up, +z forward;',
                 '# how far each seat is from where the original vehicle has it)']
        for k, e in sorted(man.items()):
            st = (e.get('stats') or {}).get('seats')
            if not st or not st.get('driver_move'):
                continue
            name = e.get('path', k).replace('/', '\\').rsplit('\\', 1)[-1].lower()
            lines.append('%s %s %s' % (name, ' '.join('%.2f' % v for v in st['driver_move']), ' '.join('%.2f' % v for v in st['passenger_move'])))
        tmp = os.path.join(self.data, 'seats.txt.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        os.replace(tmp, os.path.join(self.data, 'seats.txt'))

    # ----- model list
    def scan(self, force=False):
        arcs = self._arc_files()
        sig = []
        for a in arcs:
            st = os.stat(self.original_arc(a))
            sig.append([a, st.st_size, int(st.st_mtime)])
        cache = os.path.join(self.data, 'index.json')
        if not force:
            try:
                with open(cache, encoding='utf-8') as f:
                    c = json.load(f)
                if c.get('sig') == sig and c.get('version') == 3:
                    self.models = c['models']
                    return self.models
            except (OSError, ValueError):
                pass
        found = {}
        vehicles = set()
        for i, a in enumerate(arcs):
            self.progress = (i, len(arcs), 'reading ' + a)
            try:
                hdr = arc.read_header(self.original_arc(a))
            except (OSError, ValueError):
                continue
            for nm, typ, cs, ds, off in hdr:
                if typ == arc.TYPE_MODEL:
                    found.setdefault(nm.lower(), dict(path=nm, arcs=[]))['arcs'].append([a, cs, off])
                    if a.lower() == 'arcromvehicle.arc':
                        vehicles.add(nm.lower())
        exe = os.path.join(self.dir, 'DeadRising.exe')
        items = names.item_names(exe) if os.path.exists(exe) else {}
        models = {}
        todo = [k for k in found if k.split('\\')[1:2] in (['om'], ['npc'], ['em'], ['pl'])]
        done = 0
        for k, v in sorted(found.items()):
            parts = v['path'].split('\\')
            folder = parts[1].lower() if len(parts) > 2 else ''
            base = parts[-1].lower()
            cat = 'Vehicle' if k in vehicles else CATEGORIES.get(folder, 'Level' if folder.startswith('stage') else 'Other')
            nice = names.EXTRA.get(base) or ', '.join(items.get(base, [])) or ("Frank's outfit part" if base.startswith('cos') else '')
            own = 'arcrom' + ''.join(parts[1:-1]).lower() + base + '.arc'
            refs = sorted(v['arcs'], key=lambda r: (r[0].lower() != own, r[1]))
            info = dict(path=v['path'], name=nice, category=cat, arcs=[r[0] for r in refs], supported=False, reason='', verts=0)
            if folder in ('om', 'npc', 'em', 'pl'):
                done += 1
                self.progress = (done, len(todo), 'checking ' + v['path'])
                try:
                    a, cs, off = refs[0]
                    with open(self.original_arc(a), 'rb') as f:
                        f.seek(off)
                        m = mod.Model(zlib.decompress(f.read(cs)))
                    info['verts'] = m.n_verts
                    info['supported'] = m.supported()
                    if folder != 'om':
                        if rig.is_character(m):
                            info['category'] = 'Character'
                            info['rig'] = 'people skeleton (%d bones)' % m.n_bones
                        else:
                            info['supported'] = False
                            info['reason'] = 'not a person on the shared skeleton (Frank\'s outfit parts, vehicles of the story...)'
                    elif cat == 'Vehicle' or k in rig.VEHICLES:
                        info['category'] = 'Vehicle'
                        info['rig'] = rig.VEHICLES.get(k, {}).get('name', 'vehicle')
                    if info['supported'] is False and not info['reason']:
                        info['reason'] = 'uses a vertex layout the tool does not write yet'
                except Exception as e:  # noqa: BLE001 - a broken file just isn't offered
                    info['reason'] = 'could not read: %s' % e
            else:
                info['reason'] = 'level geometry and cutscene models are not supported'
            models[k] = info
        self.models = models
        os.makedirs(self.data, exist_ok=True)
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(dict(version=3, sig=sig, models=models), f)
        self.progress = (1, 1, 'done')
        return models

    def resolve(self, name):
        """Full model path, or just its last part (om0098) when that is unique."""
        n = name.strip().lower()
        if n in self.models:
            return n
        hits = [k for k in self.models if k.rsplit('\\', 1)[-1] == n]
        if len(hits) == 1:
            return hits[0]
        raise GameError('model %r not found%s' % (name, (' (matches: %s)' % ', '.join(hits)) if hits else ''))

    def model(self, key):
        return self.models[self.resolve(key)]

    def original_model_bytes(self, key):
        m = self.model(key)
        data = arc.read_entry(self.original_arc(m['arcs'][0]), m['path'], arc.TYPE_MODEL)
        if data is None:
            raise GameError('model not found in %s' % m['arcs'][0])
        return data

    def original_textures(self, key, model, max_size=256):
        m = self.model(key)
        want = {n.lower() for n in model.textures}
        out = {}
        for nm, typ, data in arc.read_entries(self.original_arc(m['arcs'][0]), lambda n, t: t == arc.TYPE_TEXTURE and n.lower() in want):
            try:
                out[nm] = tex.decode(data, max_size)
            except Exception:  # noqa: BLE001
                out[nm] = None
        return {n: out.get(n) or next((v for k2, v in out.items() if k2.lower() == n.lower()), None) for n in model.textures}

    # ----- swaps
    # ----- the model files swaps were made from (so a swap can be opened again and changed without picking it again)
    def _source_file(self, key):
        return os.path.join(self.data, 'sources', key.replace('\\', '_').replace('/', '_'))

    def save_source(self, key, name, raw):
        p = self._source_file(self.resolve(key).lower())
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + '.bin', 'wb') as f:
            f.write(raw)
        with open(p + '.json', 'w', encoding='utf-8') as f:
            json.dump(dict(name=name), f)

    def load_source(self, key):
        """-> (file name, bytes as picked, settings it was installed with or None) or None."""
        k = self.resolve(key).lower()
        p = self._source_file(k)
        try:
            with open(p + '.json', encoding='utf-8') as f:
                name = json.load(f)['name']
            with open(p + '.bin', 'rb') as f:
                raw = f.read()
        except (OSError, ValueError, KeyError):
            return None
        return name, raw, (self.manifest().get(k) or {}).get('settings')

    def _swap_file(self, key):
        return os.path.join(self.swaps, key.replace('\\', '_').replace('/', '_') + '.arc')

    def model_bytes(self, path):
        """Original bytes of any model by its path (None if the game has no such model)."""
        try:
            return self.original_model_bytes(path)
        except (GameError, KeyError):
            return None

    # ----- DeadRisingEx mods: the swap as a .arc in nativeWin64\Mods, loaded by DeadRisingEx (game files untouched)
    def mods_dir(self):
        return os.path.join(self.native, 'Mods')

    def has_dead_rising_ex(self):
        return os.path.isfile(os.path.join(self.dir, 'DeadRisingEx.dll'))

    def save_mod(self, file_name, payload, extra=None):
        """Write the swap as a DeadRisingEx mod into nativeWin64\\Mods. -> its full path."""
        os.makedirs(self.mods_dir(), exist_ok=True)
        p = os.path.join(self.mods_dir(), file_name)
        write_mod(p, payload, extra)
        return p

    def install(self, key, payload, info, extra=None):
        """extra: {other model key: payload} installed with it (Frank's hidden clothes) and removed with it."""
        key = self.resolve(key)
        m = self.model(key)
        os.makedirs(self.swaps, exist_ok=True)
        man = self.manifest()
        old_man = json.loads(json.dumps(man))
        touched = [key] + [self.resolve(x) for x in (extra or {})] + [k for k, e in man.items() if e.get('companion_of') == key.lower()]
        old_files = {}
        for k in touched:
            try:
                with open(self._swap_file(k), 'rb') as f:
                    old_files[k] = f.read()
            except OSError:
                old_files[k] = None
        try:
            return self._install(key, m, man, payload, info, extra)
        except BaseException:
            # the game files couldn't be changed (the game is running, a file is locked...): leave everything as it was
            for k, data in old_files.items():
                try:
                    if data is None:
                        if os.path.exists(self._swap_file(k)):
                            os.remove(self._swap_file(k))
                    else:
                        with open(self._swap_file(k), 'wb') as f:
                            f.write(data)
                except OSError:
                    pass
            self._save_manifest(old_man)
            try:
                self._rebuild_all(sorted(set(m['arcs']) | {a for e in old_man.values() for a in e.get('arcs', [])
                                                           if e.get('companion_of') == key.lower()}))
            except Exception:  # noqa: BLE001
                pass
            raise

    def _install(self, key, m, man, payload, info, extra):
        arcs = set(m['arcs'])
        # companions of an earlier install of this swap that aren't wanted now
        for k, e in list(man.items()):
            if e.get('companion_of') == key.lower() and (not extra or k not in {self.resolve(x) for x in extra}):
                man.pop(k)
                arcs.update(e['arcs'])
                try:
                    os.remove(self._swap_file(k))
                except OSError:
                    pass
        arc.write_arc(self._swap_file(key), 4, payload)
        man[key.lower()] = dict(info, path=m['path'], arcs=m['arcs'], time=time.strftime('%Y-%m-%d %H:%M'))
        for ck, cp in (extra or {}).items():
            ck = self.resolve(ck)
            cm = self.model(ck)
            arc.write_arc(self._swap_file(ck), 4, cp)
            man[ck.lower()] = dict(glb='(hidden for %s)' % m['path'].rsplit('\\', 1)[-1], companion_of=key.lower(), path=cm['path'],
                                   arcs=cm['arcs'], time=time.strftime('%Y-%m-%d %H:%M'))
            arcs.update(cm['arcs'])
        self._save_manifest(man)
        return self._rebuild_all(sorted(arcs))

    def remove(self, key):
        man = self.manifest()
        k2 = key.strip().lower()
        if k2 not in man:
            hits = [k for k in man if k.rsplit('\\', 1)[-1] == k2]
            k2 = hits[0] if len(hits) == 1 else k2
        key = k2
        ent = man.pop(key, None)
        if not ent:
            raise GameError('no swap installed for %s' % key)
        arcs = set(ent['arcs'])
        for k in [key] + [k for k, e in man.items() if e.get('companion_of') == key]:
            if k != key:
                arcs.update(man.pop(k)['arcs'])
            try:
                os.remove(self._swap_file(k))
            except OSError:
                pass
        self._save_manifest(man)
        return self._rebuild_all(sorted(arcs))

    def remove_all(self):
        man = self.manifest()
        arcs = sorted({a for e in man.values() for a in e['arcs']})
        for k in list(man):
            try:
                os.remove(self._swap_file(k))
            except OSError:
                pass
        self._save_manifest({})
        return self._rebuild_all(arcs)

    def _rebuild_all(self, arcs):
        log = []
        for a in arcs:
            log.append(self._rebuild(a))
        return log

    def _rebuild(self, name):
        target = os.path.join(self.native, name)
        backup = os.path.join(self.backup, name)
        if not os.path.exists(backup):
            foreign = [n for n, t, cs, ds, off in arc.read_header(target) if t == arc.TYPE_TEXTURE and re.search(r'(_x\d\d_|\\cust\d\d_)(bm|mm)$', n.lower())]
            if foreign:
                raise GameError('%s already holds a swapped model that this tool did not make (%s). Put the original file back '
                                'first (or verify the game files in Steam), then try again.' % (name, foreign[0]))
            os.makedirs(self.backup, exist_ok=True)
            shutil.copy2(target, backup + '.tmp')
            os.replace(backup + '.tmp', backup)
        active = [(k, e) for k, e in self.manifest().items() if name in e['arcs']]
        try:
            if not active:
                shutil.copy2(backup, target + '.tmp')
                os.replace(target + '.tmp', target)
                return '%s: original restored' % name
            ver, ents = arc.read_arc(backup)
            for k, e in active:
                _, payload = arc.read_arc(self._swap_file(k))
                arc.patch_entries(ents, payload)
            arc.write_arc(target, ver, ents)
            return '%s: %d swap(s) applied' % (name, len(active))
        except PermissionError:
            raise GameError('Windows would not let the tool write %s. Close the game and try again.' % name)
