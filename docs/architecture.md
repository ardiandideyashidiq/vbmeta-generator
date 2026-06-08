# vbmeta-generator Architecture

This document describes the internal architecture of `vbmeta-generator`, covering the package structure, module responsibilities, the orchestration pipeline, and design decisions.

## Package Structure

```
vbmeta-generator/
├── orchestrator.py              # Top-level entry script
├── pyproject.toml               # uv project config
├── README.md                    # This file
├── docs/                        # Documentation
│   ├── avb-overview.md
│   ├── avbtool-reference.md
│   ├── dm-verity-and-fec.md
│   ├── super-partitions.md
│   ├── image-formats.md
│   ├── boot-image-tools.md
│   ├── filesystem-tools.md
│   ├── verity-tools.md
│   └── architecture.md          # This file
├── vbmeta_generator/
│   ├── __init__.py              # Empty
│   ├── cli.py                   # argparse CLI definition
│   ├── orchestrator.py          # 11-step Orchestrator class
│   ├── image.py                 # Image type detection
│   ├── properties.py            # build.prop extraction & AVB props
│   ├── super_partition.py       # Super partition parsing & rebuild
│   ├── avb.py                   # AVB signing operations
│   ├── utils.py                 # Tool execution helper
│   ├── bin/                     # 24 AOSP prebuilt binaries
│   └── lib64/                   # Shared library dependencies
```

### Module Dependency Graph

```
cli.py
  └── orchestrator.py
        ├── utils.py          ← (used by all modules)
        ├── image.py
        │     └── utils.py
        ├── properties.py
        │     ├── utils.py
        │     └── image.py
        ├── super_partition.py
        │     └── utils.py
        └── avb.py
              └── utils.py
```

---

## Module Details

### `cli.py` — Command-Line Interface

Defines the `argparse` parser with all CLI arguments. Creates an `Orchestrator` instance and calls `run_all()`.

```python
parser = build_parser()
args = parser.parse_args()

orch = Orchestrator(
    rom_dir=str(rom_dir),
    output_dir=str(output_dir),
    key_path=args.key,
    algorithm=args.algorithm,
    rollback_index=args.rollback_index,
    flags=args.flags,
    yes=args.yes,
    dry_run=args.dry_run,
    verbose=args.verbose,
)
sys.exit(orch.run_all())
```

Key decisions:
- If `rom_dir` doesn't exist, exits with error immediately (no need to initialize the full orchestrator)
- Default output is `rom_dir/../vbmeta_out`
- `--dry-run` and `-y` can be combined for safe previews

### `orchestrator.py` — The Pipeline

The `Orchestrator` class owns the 11-step pipeline. It manages:

- `self.images` — dict of filename → ImageType
- `self.image_infos` — dict of filename → full info dict
- `self.super_layout` — parsed `SuperLayout` (if super.img exists)
- `self.build_props` — dict of partition → parsed build.prop
- `self.active_partitions` — dict of base_name → extracted image path
- `self.work_dir` — temporary working directory

#### The 11 Steps

```
_step_keygen()        # 1  Generate RSA key pair
_step_super()         # 2  Inspect & extract super.img
_step_props()         # 3  Extract build.prop from partitions
_step_sign_boot()     # 4  Sign boot.img with hash footer
_step_sign_dtbo()     # 5  Sign dtbo.img with hash footer
_step_hashtree()      # 6  Add hashtree footers to system partitions
_step_rebuild_super() # 7  Rebuild super.img from modified partitions
_step_vbmeta_system() # 8  Create vbmeta_system.img
_step_vbmeta_vendor() # 9  Create vbmeta_vendor.img
_step_vbmeta()        # 10 Create vbmeta.img
_step_output()        # 11 Copy outputs to final directory
```

#### Spinner Animation

Long-running operations use `_spinner()` context manager for animated feedback:

```python
@contextmanager
def _spinner(self, message: str):
    with self.console.status(f"[bold blue]{message}"):
        yield
```

Used in:
- Super extraction (`lpunpack`)
- Hash footer signing
- Hashtree footer addition (per partition)
- Super rebuild (`lpmake`)

#### Temporary Directory

The working directory is created in `/home/rd/tmp/` (not `/tmp/`) to avoid filling tmpfs during super image extraction/rebuild:

```python
tmp_root = Path("/home/rd/tmp")
tmp_root.mkdir(parents=True, exist_ok=True)
self.work_dir = Path(tempfile.mkdtemp(prefix="vbmeta_", dir=str(tmp_root)))
```

Cleanup: unless `--verbose` is set, the entire work dir is deleted in the `finally` block.

### `image.py` — Image Detection

The `detect_image()` function reads magic bytes to classify images (see [image-formats.md](image-formats.md)). The `detect_avb()` function calls `avbtool info_image` to check for existing AVB footers and extract VBMeta details.

`ImageInfo` is a dataclass that accumulates detection results:

```python
class ImageInfo:
    path: str
    filename: str
    partition_name: str       # Inferred by stripping _a/_b
    type: ImageType           # BOOTIMG, SUPER, EROFS, EXT4, SPARSE, OTHER
    fstype: str | None        # "ext4", "erofs"
    is_sparse: bool
    has_avb: bool
    avb_algorithm: str | None
    avb_rollback_index: int | None
    avb_flags: int | None
    avb_descriptors: list[dict] | None
```

The `partition_name` inference is:

```python
name = Path(self.path).stem
return re.sub(r"(_a|_b)?$", "", name)
```

So `system_a.img` → `system`, `vendor_b.img` → `vendor`.

### `properties.py` — Build Properties

Extracts `build.prop` from filesystem images and converts the key-value pairs into AVB properties.

#### Extraction Strategies

| Filesystem | Tool | Method |
|------------|------|--------|
| EROFS | `dump.erofs` | `--path /system/build.prop` → get NID → `--nid NID --cat` |
| EXT4 (raw) | `debugfs` | `-R "cat /build.prop"` |
| EXT4 (sparse) | `simg2img` + `debugfs` | Unsparse first, then debugfs |

The `extract()` function auto-detects filesystem type if not provided:

```python
if fstype is None:
    info = detect_image(image_path)
    if info.type in (ImageType.EROFS,):
        fstype = "erofs"
    elif info.type in (ImageType.EXT4, ImageType.SPARSE):
        fstype = "ext4"
```

#### AVB Property Generation

Properties are mapped from build.prop keys to `com.android.build.<partition>.*`:

```python
prefix_map = {
    "system": "ro.system.build",
    "system_ext": "ro.system.build",
    "product": "ro.product.build",
    "vendor": "ro.vendor.build",
}

fingerprint = build_props.get(f"{prefix}.fingerprint") or build_props.get("ro.build.fingerprint")
os_version  = build_props.get(f"{prefix}.version.release") or build_props.get("ro.build.version.release")
security    = build_props.get(f"{prefix}.version.security_patch") or build_props.get("ro.build.version.security_patch")
```

These are embedded into each partition's hashtree footer VBMeta via `avbtool add_hashtree_footer --prop`, and also into the chained vbmeta images.

### `super_partition.py` — Super Partition Management

#### `parse_lpdump_output()`

Parses the text output of `lpdump` into a `SuperLayout` dataclass. The parser tracks three sections:

- **Partition table**: name, group, extents (sector ranges)
- **Block device table**: device size
- **Group table**: group name → maximum size

The parser is stateful, sectioning on header lines like `"Partition table:"`:

```python
if stripped == "Partition table:":
    current_section = "Partition table"
    continue
```

Each partition stores its extents as a list of sector ranges:

```python
{
    "name": "product_a",
    "group": "infinix_dynamic_partitions_a",
    "extents": [{"start": 0, "end": 5229567}]
}
```

#### `extract()`

Wraps `lpunpack --slot=0` to extract active slot partitions. The extracted files are named `<partition_name>.img` by `lpunpack` (e.g., `product_a.img`).

The orchestrator maps these to base names and stores paths in `self.active_partitions`:

```python
self.active_partitions = {}
for p in active:
    pname = p["name"]                     # e.g., "product_a"
    base = re.sub(r"_(a|b)$", "", pname)  # e.g., "product"
    if extracted.exists():
        self.active_partitions[base] = str(extracted)
```

#### `rebuild()`

Reconstructs a super image using `lpmake`. The algorithm:

1. Retains the original `device_size`, `metadata_size`, `metadata_slots`
2. Re-creates all groups with their original max sizes
3. For each partition in the original layout:
   - If modified: uses the modified image size (rounded to block)
   - If unmodified: uses the original extent-derived size
   - Zero-size partitions (inactive slots) are skipped
4. Maps modified images via `--image partition=file`

### `avb.py` — AVB Operations

Thin wrappers around `avbtool` subcommands:

| Function | avbtool subcommand | Use |
|----------|-------------------|-----|
| `generate_key()` | `openssl genpkey` + `extract_public_key` | Key generation |
| `add_hash_footer()` | `add_hash_footer` | Sign boot/dtbo |
| `add_hashtree_footer()` | `add_hashtree_footer` | Sign system partitions |
| `make_vbmeta_image()` | `make_vbmeta_image` | Create vbmeta variants |
| `extract_public_key_digest()` | `extract_public_key_digest` | Display key fingerprint |
| `get_image_size()` | `info_image --image` | Read partition size from footer |
| `info_image()` | `info_image` | Raw output for parsing |

Each function constructs the command list, delegates to `utils.run()`, and raises `RuntimeError` on failure.

Properties are passed to the avbtool as `--prop` arguments:

```python
if props:
    for ptype, key, val in props:
        cmd.extend([f"--{ptype}", f"{key}:{val}"])
```

### `utils.py` — Tool Execution

The core execution helper that finds and runs bundled AOSP binaries.

#### Tool Discovery

```python
PACKAGE_DIR = Path(__file__).resolve().parent
BIN_DIR = PACKAGE_DIR / "bin"
LIB64_DIR = PACKAGE_DIR / "lib64"
```

This resolves correctly whether running from the source tree or after `uv tool install` (where `__file__` is inside the installed package).

#### `run()` Function

Sets up environment and executes a binary:

```python
def run(tool: str, *args, capture_output=False, text=True, check=False, **kwargs):
    env = os.environ.copy()
    env["PATH"] = f"{BIN_DIR}:{env.get('PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{LIB64_DIR}:{env.get('LD_LIBRARY_PATH', '')}"

    tool_path = BIN_DIR / tool
    if tool_path.exists():
        cmd = [str(tool_path)]
    else:
        cmd = [tool]

    cmd.extend(str(a) for a in args)
    return subprocess.run(cmd, env=env, ...)
```

Key design points:
- `PATH` is prepended with `BIN_DIR` so the tool finds its `lib64/` dependencies
- `LD_LIBRARY_PATH` points to bundled shared libraries
- If the tool doesn't exist in `bin/`, falls back to system PATH (for `openssl`, which is in the system but also bundled)
- `check=False` by default; callers opt into strict error handling

#### `run_verbose()` Function

Wraps `run()` with optional debug output:

```python
def run_verbose(tool, *args, verbose=False, console=None, **kwargs):
    if verbose and console:
        console.print(f"[dim]  $ {tool} {' '.join(str(a) for a in args)}[/dim]")
    return run(tool, *args, **kwargs)
```

## Pipeline Flow (Detailed)

### Step 1: Key Generation

- If `--key` is provided and exists: extracts public key, stores paths
- If `--key` is provided but doesn't exist: error
- If `--key` is not provided: generates new RSA key with `openssl genpkey`, extracts public key with `avbtool extract_public_key`
- Displays SHA-1 fingerprint via `avbtool extract_public_key_digest`

### Step 2: Super Inspection

- Finds `super.img` in `image_infos`
- Runs `lpdump` and parses the layout
- Displays partition table with sizes
- If not dry-run: extracts all active partitions to a temp dir via `lpunpack --slot=0`
- Maps `_a`-suffixed filenames to base partition names

### Step 3: Properties

- Collects all filesystem partitions (from standalone images + those extracted from super)
- For each partition:
  - Detects fstype (EROFS/EXT4) if unknown
  - Extracts `build.prop` using the appropriate tool
  - Parses into key-value dict
- Displays fingerprint, OS version, security patch per partition
- Stores system props separately (used as fallback for other partitions)

### Step 4-5: Hash Footer Signing

- For `boot.img` and `dtbo.img`:
  - Detects existing AVB footer (warns if present)
  - Gets partition size from existing AVB footer or file size
  - Calls `avbtool add_hash_footer` with AVB properties

### Step 6: Hashtree Footers

- For each system partition (`system`, `system_ext`, `product`, `vendor`):
  - If partition was extracted from super: uses the extracted copy
  - If sparse: converts to raw, runs `e2fsck -f`
  - Calls `avbtool add_hashtree_footer`
  - Uses `sha256` for EROFS, `sha1` for EXT4
  - Embeds partition-specific AVB properties from build.prop
  - If sparse: converts back to sparse format

### Step 7: Super Rebuild

- Collects all modified partition paths from `active_partitions`
- Maps base names to super partition names (`system` → `system_a`)
- Calls `lpmake` with original layout parameters + modified images

### Step 8-10: vbmeta Image Creation

- **vbmeta_system**: includes descriptors from `system`, `system_ext`, `product` images
- **vbmeta_vendor**: includes descriptors from `vendor` image
- **vbmeta**: chain partition descriptors for `boot`, `vbmeta_system`, `vbmeta_vendor` + descriptors from `dtbo`

### Step 11: Output

- Copies all generated files to the output directory:
  - `vbmeta.img`, `vbmeta_system.img`, `vbmeta_vendor.img`
  - `avb.key`, `avb.avbpubkey`
  - `boot.img` (signed)
  - `super.img` (rebuilt, if applicable)

## Design Decisions

### Why Bundle AOSP Binaries?

Bundling eliminates the need for a full AOSP build environment. The 24 binaries plus their shared libraries are ~150MB total but make the tool self-contained: one `uv tool install` and you can run from any machine.

### Why Not Use importlib.resources?

`utils.py` uses `Path(__file__).resolve().parent` instead of `importlib.resources` because it works identically in development (running from source tree) and after install (`uv tool install` puts the package in a venv site-packages). `importlib.resources` has quirks with namespace packages and editable installs.

### Why Rename src/ → vbmeta_generator/?

The original `src/` layout caused `.pth` import resolution issues with editable installs. The `.pth` file added `src/` itself to `sys.path` rather than its parent, making `from src import utils` fail. Renaming to `vbmeta_generator/` (a proper Python package name) fixed this.

### Why /home/rd/tmp/ Instead of /tmp/?

The super image extraction and rebuild process creates large temporary files (up to 2× the super image size). On many Linux systems, `/tmp/` is a tmpfs mounted in RAM, which would quickly exhaust available memory. Using a disk-based directory avoids this issue.

### Why `--flags 1` by Default?

Flag value 1 = `AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED`. This allows `adb remount` on system partitions (which requires disabling dm-verity) while still verifying `boot.img`. It's the standard default for custom ROMs and most AOSP builds. End users who want full verification can override with `--flags 0`.

### Why Separate `_step_props` After `_step_super`?

The property extraction step runs after super extraction because many ROMs don't have standalone `system.img`/`vendor.img` files — those partitions exist only inside `super.img`. By extracting super first, the orchestrator can access the logical partitions and extract `build.prop` from them.

## Error Handling

- Each step catches failures via `check=True` in tool runs or explicit `result.returncode` checks
- Non-critical steps (e.g., missing `dtbo.img`) produce warnings and continue
- Critical setup failures (e.g., missing ROM directory, missing tools, tool execution failures) produce errors and exit
- The `finally` block ensures temp directory cleanup even on failure
- `--dry-run` mode skips all write operations but shows the full plan
