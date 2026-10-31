# Changelog

## 1.0.0 (first public release)

Everything in the README; the main pieces:

- **Models**: every object, vehicle and character model in the game, listed with the game's own names.
- **Files**: `.glb`, `.gltf` and `.obj` read directly (several files, a folder or a `.zip`, textures found by path or
  name, vertex colours turned into a texture). `.fbx`, `.blend`, `.dae`, `.3ds`, `.stl`, `.ply`, `.x3d`, `.usd` and `.abc`
  are converted through Blender in the background when it's installed.
- **Automatic turn**:
  - picks the best of 24 orientations by shape;
  - straightens models drawn at a slant;
  - uses clues per kind: wheels under vehicles, feet down for characters, the held end for weapons.
  - Manual 90° turns, *Stand it up* and a free *Fine turn* on three axes are there to correct it.
- **Automatic size by kind**: wheels and ground area for vehicles, height for characters, length for objects. The size
  is shown in metres next to a 1.83 m figure.
- **Vehicles**:
  - wheels spin and steer (4, 3 or 2 wheels);
  - named doors, hood, trunk, steering wheel, handlebars, fork, swingarm and pedals move with the game's parts;
  - driver and passenger seats are found in the model and can be moved by hand; the Dead Rising co-op mod v31i+ reads
    them from `seats.txt`.
- **Characters**:
  - automatic rigging to the game skeleton;
  - A-pose straightening;
  - legs and arms fitted to the game's joints;
  - the head and hair move as one rigid piece, sealed at the neck.
- **Parts list**: shows what each piece was taken for. Any piece can be changed or left out, and flat bases are
  left out automatically.
- **Memory**: each game model remembers its file and settings, and installed swaps keep their source so they can be
  edited later.
- **Safety**: backups before the first change; *Restore* brings back the original files byte for byte.
- **Interfaces**: a command line (`list`, `swap`, `restore`, `export`), and an export of the original as `.glb` to use
  as a reference.
