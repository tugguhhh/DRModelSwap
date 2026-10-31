# Dead Rising (PC, 2016) file formats used by DR Model Swap

Everything here was worked out from the game's own files (and checked by drawing the original models back
from the decoded data). All numbers are little-endian. "Units" are the game's world units (about 1 cm).

## .arc packages (`nativeWin64\*.arc`)

| offset | size | meaning |
|---|---|---|
| 0x00 | 4 | `ARC\0` |
| 0x04 | 2 | version (4) |
| 0x06 | 2 | number of files |
| 0x08 | 80 × n | entries: name (64 bytes, game path without extension, `\` separators), type (u32), compressed size, real size, offset |

File data is zlib-compressed and starts at the first 0x8000 boundary after the entry table.
Types used here: `0x1041BD9E` rModel (MOD), `0x3CAD8076` rTexture (TEX).

A model normally has its own package (`model\om\om0098\om0098` → `arcromomom0098om0098.arc`), but **copies of the
same model are also packed into bigger bundles** (for example the white sedan is also in `arcromvehicle.arc`,
`arcromarea600.arc` and two cutscene packs). The game uses whichever copy is loaded first, so a replacement has to go
into every package that holds the model.

## TEX (textures)

| offset | size | meaning |
|---|---|---|
| 0x00 | 4 | `TEX\0` |
| 0x04 | 1 | 0x56 |
| 0x05 | 1 | type (2 = 2D, 1 seen on some) |
| 0x06 | 1 | flags; 4 = an average colour (4 floats) follows the header |
| 0x07 | 1 | mip count |
| 0x08 | 4+4 | width, height |
| 0x10 | 4 | 0 |
| 0x14 | 4 | format fourcc: `DXT1`, `DXT5`, or a D3D format number |
| 0x18 | (16) | average colour (only with flag 4) |

Then the mip levels, largest first, in 4×4 blocks (1×1 and 2×2 levels still take one block).
Colour maps (`*_BM`) are DXT1/DXT5 with the average colour; `*_MM` maps are grey specular masks.

## MOD (models), version 0x170

Header (0xA0 bytes):

| offset | type | meaning |
|---|---|---|
| 0x00 | char[4] | `MOD\0` |
| 0x04 | u16 | version 0x170 |
| 0x06 | u16 | bones |
| 0x08 | u16 | pieces (draw calls) |
| 0x0A | u16 | materials |
| 0x0C | u32 | vertices |
| 0x10 | u32 | indices |
| 0x14 | u32 | triangles |
| 0x18 | u32 | vertex buffer 1 size |
| 0x1C | u32 | vertex buffer 2 size |
| 0x20 | u64 | texture names |
| 0x28 | u64 | offset of the bones |
| 0x30 | u64 | offset of the texture names (64 bytes each); the materials (0xD0 bytes each) follow them |
| 0x38 | u64 | offset of the pieces (0x50 bytes each) |
| 0x40 | u64 | offset of vertex buffer 1 |
| 0x48 | u64 | offset of vertex buffer 2 |
| 0x50 | u64 | offset of the indices |
| 0x60 | float[4] | bounding sphere: centre x y z, radius |
| 0x70 | float[4] | box minimum (x y z, w) |
| 0x80 | float[4] | box maximum |
| 0x90 | ... | LOD distances and other settings (kept as they are) |

**Bones**: 0x18 bytes each (u8 id, u8 parent (255 = none), padding, f32 length, f32 x y z position relative to the
parent), then a 4x4 local matrix per bone, then a 4x4 inverse bind matrix per bone (64 bytes each). Matrices use row
vectors (v' = v @ M, translation in the last row); world = local @ parent's world; parents come before children.
Vertices are stored in model space (the bind pose); skinned position = Σ weight × (v @ inverse bind @ posed world).

Vehicles (from their geometry; `drmodelswap/rig.py`):

| model | | body | wheels (spin around X) | steering hubs | other |
|---|---|---|---|---|---|
| om0009 | 2-door car | 0 | 3 6 (front) 9 12 (rear) | 2 5 | doors 13 14, steering wheel 15, hood 16, trunk 17 |
| om0098 | 4-door car | 0 | 4 7 / 10 13 | 3 6 | steering wheel 1, doors 14-17, hood 18, trunk 19 |
| om0099 | track | 0 | 2 4 / 6 8 | 1 3 | doors 9 10 |
| om009a | hammer | 0 | 3 6 / 15 16 | 2 5 | steering wheel 11, gun turret 12-14 |
| om009b | bike | 0 | 10 (front) 9 (rear) | handlebar 1, fork 2 | swingarm 3 |
| om009d | bicycle | 0 | 2 / 3 | handlebar 1 | crank 4, pedals 5 6 |

Every wheel bone has no rotation in the bind pose, so its axle is the model's X axis through the bone's pivot; the tyre's
centre lies on that line. +X is the vehicle's left, +Z its front.

A wheel centred between the two game wheels of an axle (a trike) is weighted 0.5 / 0.5 to both wheel bones: the average
of two rotations about the same X axis is that rotation (exact for spin, and for steering the two hubs turn alike), so it
spins and steers around the midpoint. Moving a wheel elsewhere (*Where my model has them*) moves its assembly root (the
ancestor whose parent is bone 0) and rewrites the local and inverse bind matrices and the record's position at +12.

People (Frank pl00, survivors npc.., psychopaths em4..g, zombies em00..) share one skeleton, T-pose, hips at the origin:
0 hips, 1-3 right thigh / shin / foot, 4-6 left, 7 spine, 8 chest, 9 neck, 10 head, 11-14 right shoulder / upper arm /
forearm / hand, 15-18 left, 19 jaw, 20 weapon (right hand), 21 22 eyes (not on zombies). Some models add bones after these.

Animations (rMotionList, type 329180445, "LMT" version 22; e.g. `motion\pl\pl00\pl0000` in system_common.arc): per
motion a 64-bit offset to a header (u64 track list offset, u32 track count, u32 frame count, ...); tracks are 24 bytes
(u8 buffer type, u8 usage, u8 joint type, u8 bone, f32 weight, u32 buffer size, pad, u64 buffer offset). Usage 0 =
local rotation, 1 = local position, 4 = root position (bone 255). Besides rotations, people's motions carry a local
position for the hips (bone 0: about 102-104 above the ground, so the hip height is fixed by the animation) and IK
positions (joint types 2, 5-8) for the feet (3, 6), hands (14, 18), upper arms and thighs. Bone lengths come from the
model: survivors with shorter limbs (npc01) share the same motions.

**Material** (0xD0 bytes): +0x00 flags; +0x20 colour map (`*_BM`) texture number, 1-based (0 = none);
+0x30 specular mask (`*_MM`) texture number; the rest are shader settings.

**Piece** (0x50 bytes):

| offset | meaning |
|---|---|
| 0x00 | u16 display group (see below) |
| 0x02 | u16 material |
| 0x04 | 4 bytes: 01, LOD mask (0x0F = all distances; zombies 1 / 2 / 4 = near / middle / far, the header's 0x90 / 0x94 hold 1000 / 3000, likely the switch distances), a draw category (0 normal; glass and similar use others), the most bone weights any vertex in the piece uses (1-4) |
| 0x08 | u8 vertex stride 1 (0x1C), u8 vertex stride 2 (0x0C), 2 flag bytes |
| 0x0C | u32 vertex count |
| 0x10 | u32 first vertex |
| 0x1C | u32 first index |
| 0x20 | u32 index count |
| 0x30 | float[4] box min |
| 0x40 | float[4] box max |

**Display groups**: each unit has a 512-bit mask at unit +0xBC4 (all on when it's made); a piece is drawn only while the
bit of its group is on. Vehicles: the damage code (e.g. 0x1401C6520 for the 2-door car) turns lists of groups on and off
per damaged part; a list ends at group 0, so group 0 is never switched (the tool's vehicle pieces use it). People: the mask
is copied from a 256-bit set at unit +0x1264 (mPartsDispFlag) that the game fills per character: zombie clothing variants,
Frank's hand poses (groups 20-28 right, 30-39 left; one is on for the item he holds), heads (0 / 80 / 84 / 88), face
parts, and zombies' blinking eyes (groups 0, 15, 16 against 18, 19). The tool draws each region of a new body in all the
groups the original used there (pieces can share vertices and indices, so this costs no memory).

**Indices**: u16, absolute (so a model has at most 65,536 vertices). Each piece is one triangle strip; strips are
joined with repeated indices. Triangle *k* of a strip is (s[k], s[k+1], s[k+2]) for even *k* and (s[k+1], s[k], s[k+2])
for odd *k*; that order is counter-clockwise seen from the front (the normal side).

**Vertex buffer 1** (28 bytes per vertex, objects and vehicles):

| offset | type | meaning |
|---|---|---|
| 0 | u16 × 3 (+2 pad) | position: box min + value / 32767 × (box max − box min) |
| 8 | u8 × 4 | bone numbers (index into the bone list) |
| 12 | u8 × 4 | bone weights (255 = 1.0, they add up to 255) |
| 16 | s16 × 3 (+2 pad) | normal / 32767 |
| 24 | s16 × 2 | UV / 32768 (0..1, top-left origin like glTF) |

**Vertex buffer 2** (12 bytes per vertex): tangent s16 × 3 (+2 pad), UV again.

Other layouts exist and are not written by this tool: level geometry uses a 28-byte second buffer, and cutscene models
and some of Frank's outfit parts a 36-byte first buffer (more bone weights). The game's people use the 28-byte layout.

## Item names

`DeadRising.exe` has an item table: 0x18-byte entries (id/flags, pointer to the name, pointer to the archive path),
e.g. "Baseball Bat" → `arc\rom\om\om0004\om0004`. The tool finds it by looking for the entry whose name is "Pylon"
(item 1) instead of a fixed address.
