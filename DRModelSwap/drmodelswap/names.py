"""Readable names for object models, taken from the game's own item table in DeadRising.exe.

The table is found by content (the entry whose name is "Pylon"), not by a fixed address. Entries are
0x18 bytes: (u64 id/flags, char* name, char* archive), e.g. "Baseball Bat" -> "arc\\rom\\om\\om0004\\om0004".
"""
import struct

# Objects that are not items (no name in the item table). Add more as people find them.
EXTRA = {
    'om0098': 'White sedan (4-door car, drivable)',
    'om0009': '2-door car (drivable)',
    'om0099': 'Track (drivable)',
    'om009a': 'Hammer (drivable, with mounted gun)',
    'om009b': 'Bike (drivable)',
    'om009d': 'Bicycle (drivable)',
    # people (survivors, psychopaths, zombies, Frank): from the game's people table and notebook
    'em00': 'Zombie (man)',
    'em01': 'Zombie (woman)',
    'em02': 'Zombie',
    'em03': 'Zombie',
    'em04': 'Zombie',
    'em08': 'Zombie',
    'em09': 'Zombie (propane tank)',
    'em0a': 'Zombie (shopping cart)',
    'em10': 'Zombie',
    'em40': 'Special Forces soldier',
    'em43': 'Cultist',
    'em4c': 'Convict',
    'em4e': 'Convict',
    'pl00': 'Frank West',
    'em41g': 'Brock (rough)',
    'em44g': 'Sean',
    'em45g': 'Kent',
    'em46g': 'Cliff',
    'em47g': 'Paul',
    'em48g': 'Adam',
    'em49g': 'Larry (rough)',
    'em4ag': 'Steven',
    'em4bg': 'Jo (rough)',
    'em4c': 'Convict 1',
    'em4dg': 'Convict 2',
    'em4e': 'Convict 3',
    'em4fg': 'Cletus',
    'em57g': 'Roger Hall',
    'em58g': 'Jack Hall',
    'em59g': 'Thomas Hall',
    'npc00': 'Burt',
    'npc01': 'Heather',
    'npc02': 'Natalie',
    'npc03': 'Gordon',
    'npc04': 'Aaron',
    'npc05': 'Jeff',
    'npc06': 'Pamela',
    'npc07': 'Kindell',
    'npc08': 'Jolie',
    'npc09': 'Rachel',
    'npc0a': 'Susan',
    'npc0b': 'Ronald',
    'npc0c': 'Leah',
    'npc0d': 'David',
    'npc0e': 'Floyd',
    'npc0f': 'Yuu',
    'npc10': 'Shinji',
    'npc11': 'Tonya',
    'npc12': 'Ross',
    'npc13': 'Wayne',
    'npc14': 'Bill',
    'npc15': 'Sally',
    'npc16': 'Nick',
    'npc17': 'Leroy',
    'npc18': 'Simone',
    'npc19': 'Gil',
    'npc1a': 'Brett',
    'npc1b': 'Jonathan',
    'npc1d': 'Alyssa',
    'npc1fg': 'Sophie',
    'npc20g': 'Jennifer',
    'npc40g': 'Ray',
    'npc42g': 'Nathan',
    'npc44g': 'Michelle',
    'npc45': 'Cheryl',
    'npc46g': 'Beth',
    'npc4c': 'Josh',
    'npc4d': 'Barbara',
    'npc4e': 'Rich',
    'npc4fg': 'Mindy',
    'npc50g': 'Debbie',
    'npc52g': 'Tad',
    'npc54': 'Greg',
    'npc56g': 'Kay',
    'npc57': 'Lilly',
    'npc59': 'Kelly',
    'npc5a': 'Janet',
    'npc80g': 'Brad',
    'npc81g': 'Jessie',
    'npc82g': 'Carlito',
    'npc83g': 'Isabela',
    'npc84g': 'Barnaby',
    'npc85g': 'Lindsay',
    'npc86g': 'Otis',
    'npc90g': 'Ryan',
    'npc91g': 'Chris',
    'npc92': 'Todd',
    'npc93': 'Brian',
    'npc94': 'Dana',
    'npc95': 'Verlene',
    'npc96': 'Mark',
    'npc97': 'Kathy',
    'npc98': 'Alan',
    'npc99': 'James',
    'npc9a': 'Sid Carmack',
    'npc9cg': 'Freddie',
}


def _sections(d):
    pe = struct.unpack_from('<I', d, 0x3C)[0]
    ns = struct.unpack_from('<H', d, pe + 6)[0]
    so = struct.unpack_from('<H', d, pe + 20)[0]
    base = struct.unpack_from('<Q', d, pe + 24 + 24)[0]
    secs = []
    for i in range(ns):
        o = pe + 24 + so + i * 40
        vs, va, rs, ra = struct.unpack_from('<IIII', d, o + 8)
        secs.append((va, max(vs, rs), ra, rs))
    return base, secs


def item_names(exe_path):
    """{'om0004': ['Baseball Bat', ...], ...}; empty if the table isn't found."""
    try:
        d = open(exe_path, 'rb').read()
        base, secs = _sections(d)
    except Exception:
        return {}

    def off(va):
        r = va - base
        for sva, sz, ra, rs in secs:
            if sva <= r < sva + sz:
                o = ra + r - sva
                return o if r - sva < rs else None
        return None

    def va_of(o):
        for sva, sz, ra, rs in secs:
            if ra <= o < ra + rs:
                return base + sva + o - ra
        return None

    def cstr(va):
        o = off(va) if base <= va < base + 0x10000000 else None
        if o is None:
            return None
        return d[o:o + 80].split(b'\0')[0].decode('latin1', 'replace')

    s = d.find(b'\0Pylon\0')
    if s < 0:
        return {}
    pv = va_of(s + 1)
    loc = d.find(struct.pack('<Q', pv))
    if loc < 0 or pv is None:
        return {}
    table = loc - 8 - 0x18  # "Pylon" is item 1
    out, misses = {}, 0
    for i in range(2000):
        o = table + i * 0x18
        if o + 0x18 > len(d):
            break
        nm, ar = struct.unpack_from('<QQ', d, o + 8)
        name, arcp = cstr(nm), cstr(ar)
        if name is None:
            misses += 1
            if misses > 8:
                break
            continue
        misses = 0
        if arcp and name and not name.startswith('---'):
            key = arcp.rsplit('\\', 1)[-1].lower()
            if name not in out.setdefault(key, []):
                out[key].append(name)
    return out
