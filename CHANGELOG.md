# Changelog

## 1.2.0

DeadRisingEx mods, loose files, and fixes for characters that showed holes (heavier or cartoon-shaped models, zombies
far away, models that open up when they move).

- **DeadRisingEx mods**:
  - **Save as DeadRisingEx mod** writes the previewed swap into `nativeWin64\Mods` as `DRMS_<model>_<your file>.arc`,
    without touching the game's files. Switch it on or off in DeadRisingEx's launcher (Settings > Mods).
  - **Download mod to share (.zip)**: the mod `.arc` plus a readme with install steps. Players need only DeadRisingEx.
- **Save loose files (.mod / .tex)**: the converted model (rModel) and textures as plain files, not packed in an archive.
  They're written in the game's folder layout into a folder you choose (remembered), with `DRMS_files.txt` listing
  game paths and type ids, for packing with your own tools.
- **Far-away versions without cracks**: zombies' simpler far-away versions were made by thinning each piece of the
  model on its own, so a see-through line opened wherever two pieces meet (neck, waist, sleeves, two materials). All
  pieces are now thinned together, so shared edges stay closed. Models up to 12,000 vertices (was 8,000) are drawn
  whole at every distance, and thinned versions keep more detail.
- **Legs modelled touching**: where the thighs touch, a model has no inner leg surface. After the legs were cut apart,
  each leg was see-through from the side when walking. Each leg now gets an inner side that follows the cut exactly.
- **Neck: seal to the body** (new, default): a head piece whose edge lies on the body (a neckless head covering the
  shirt's collar) bends with the body along that edge instead of swinging with the head, so no gap opens at the neck.
  *Head piece all rigid* is the old way.
- **More checks before the game**:
  - The see-through check also shows the far-away versions (smaller pictures), and the Problems list offers *Whole
    model at every distance* when they'd have holes.
  - The moving parts check (walking) shows see-through spots in magenta, and the Problems list warns when the model
    opens up as it moves.
  - The see-through check no longer marks two-sided surfaces as holes.
  - The stretch warning explains stretching at the neck and offers the rigid head instead.
- Command line: `swap ... --mod mods | FILE.arc | FILE.zip`, `--loose [FOLDER]`, `--neck seal|rigid`.

## 1.1.0

Problems found ahead of time, fixed where it's safe, and tools to fix the rest yourself.

- **Problems list**: under the preview, what was found before the model goes in the game: what was fixed
  automatically (with a button to undo) and what to check (with buttons for the settings that help). The command line
  prints it too.
- **See-through check** (new picture): the model from the front and behind with the backs of faces in magenta. The game
  doesn't draw backs, so magenta that shows is a hole in the game.
- **Faces** setting: faces turned inside out are found and turned round (default); open surfaces of double-sided
  materials get a back; or draw every face from both sides.
- **Characters**:
  - Holes in arms and legs on zombies fixed. Each part of the model is now also drawn with the groups of the clothing
    variants that cover the same place (long or short sleeves, trousers or shorts).
  - Zombies' far-away versions no longer have holes: light models are drawn whole at every distance, and thinning
    keeps fronts and backs apart and triangles facing the right way.
  - **Where the legs part** is found from the triangles (baggy trousers made legs come out far too long), or set by
    hand.
  - **Between the legs**: cloth joining the two legs is cut down the middle so it doesn't stretch across when walking.
  - **Legs and arms** take weights only from their own side.
  - **Stretch check**: test poses find triangles that stretch a lot, named by joint and shown orange in the moving
    parts check.
- **Things Frank holds**:
  - **Hold: find the handle**: a gun's handle top goes in Frank's hand, front forwards (turned round if backwards),
    placed like the original holds its own.
  - **Move in the hand** sliders.
  - Frank's hand and forearm are drawn in the preview.
- Command line: `--faces`, `--grip`, `--hold X Y Z`, `--crotch`, `--webs`, `--limbs`, `--lods`.

## 1.0.1

- **Driver seat without the co-op mod**: new Riders choice *Fit my model to Frank's seat*. Your model is moved so its
  driver seat lands where the game seats Frank, and the wheels stay on the game's axles. It works in an unmodded game
  and is the default unless the Dead Rising co-op mod is installed (then riders are drawn in your model's own seats
  as before). Command line: `--seats fit|auto|game`.
- Seats are now found for vehicles whose wheels weren't recognised or are set to *Fixed to the body*.

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
