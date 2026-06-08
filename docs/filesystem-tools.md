# Filesystem Tools

`vbmeta-generator` bundles several filesystem tools for dealing with EXT4 and EROFS partition images. These are used for extracting `build.prop` for AVB properties, resizing filesystems, and managing sparse images.

---

## EXT4 Tools

### `debugfs` — EXT4 File System Debugger

`debugfs` is an interactive or scriptable EXT4 filesystem debugger. It can read files from an EXT4 image without mounting it.

```
debugfs [-b blocksize] [-s superblock] [-R request] [-w] device
```

| Option | Description |
|--------|-------------|
| `-R request` | Execute a single command and exit (scripting mode) |
| `-w` | Open the filesystem for writing |

Common `-R` commands used in vbmeta-generator:

```
cat /build.prop      — print contents of build.prop from the root
cat /system/build.prop — from /system/ subdirectory
cat /etc/build.prop  — from /etc/ subdirectory
```

#### Used in vbmeta-generator

`extract_from_ext4()` in `vbmeta_generator/properties.py`:

```python
result = utils.run("debugfs", "-R", "cat /build.prop", raw_image,
                   capture_output=True, text=True)
if result.returncode == 0 and result.stdout.strip():
    return parse_build_prop(result.stdout)
```

It tries three paths: `/build.prop`, `/system/build.prop`, `/etc/build.prop`. The first one that returns valid content wins.

For sparse images, the tool first converts to raw format via `simg2img` before running `debugfs`, then cleans up the temporary file.

### `e2fsck` — EXT4 File System Check

`e2fsck` checks and optionally repairs EXT4 filesystems.

```
e2fsck [-panyrcdfktvDFV] [-b superblock] [-B blocksize] device
```

| Option | Description |
|--------|-------------|
| `-f` | Force check even if filesystem is clean |
| `-p` | Automatic repair (no questions) |
| `-y` | Assume "yes" to all questions |
| `-n` | Make no changes (read-only check) |

#### Used in vbmeta-generator

`e2fsck -f` is called after unsparsing a sparse EXT4 image, before adding the hashtree footer. This ensures the filesystem is in a consistent state before avbtool processes it:

```python
utils.run("e2fsck", "-f", raw_path, capture_output=True, check=False)
```

The `check=False` flag means the command may return non-zero if the filesystem had errors; `avbtool add_hashtree_footer` will still work as long as the filesystem is readable.

### `resize2fs` — EXT4 Resizer

Resizes EXT4 filesystems.

```
resize2fs [-d debug_flags] [-f] [-F] [-M] [-P] [-p] device [-b|-s|new_size]
```

| Option | Description |
|--------|-------------|
| `-M` | Shrink to minimum size |
| `-P` | Print minimum size and exit |
| `-f` | Force resize (override safety checks) |

### `e2fsdroid` — Android EXT4 Population Tool

`e2fsdroid` populates an EXT4 image with files from a staging directory, applying Android-specific file contexts and capabilities.

```
e2fsdroid [-B block_list] [-D basefs_out] [-T timestamp]
          [-C fs_config] [-S file_contexts] [-p product_out]
          [-a mountpoint] [-d basefs_in] [-f src_dir] [-e] [-s]
          [-u uid-mapping] [-g gid-mapping] image
```

| Option | Description |
|--------|-------------|
| `-f src_dir` | Source directory to populate from |
| `-a mountpoint` | Mount point for the image |
| `-C fs_config` | Filesystem config file (permissions, capabilities) |
| `-S file_contexts` | SELinux file contexts |
| `-T timestamp` | Timestamp for all files |

`e2fsdroid` is not currently used by `vbmeta-generator` but is bundled for potential use in ROM customization workflows.

---

## EROFS Tools

EROFS (Enhanced Read-Only File System) is a Linux filesystem designed for read-only use cases. It offers better compression and performance than EXT4 for system partitions.

### `dump.erofs` — EROFS Image Dumper

`dump.erofs` inspects and extracts data from EROFS images.

```
dump.erofs [OPTIONS] IMAGE
```

| Option | Description |
|--------|-------------|
| `--path X` | Look up the inode for path X |
| `--nid N` | Show info or operate on inode with NID N |
| `--cat` | Print file contents (requires `--nid`) |
| `--ls` | List directory contents (requires `--nid` or `--path`) |
| `-s` | Show superblock info |
| `-e` | Show extent info |
| `-S` | Show statistics |
| `--blkid-udev` | Print block device attributes for udev |

#### Used in vbmeta-generator

`extract_from_erofs()` in `vbmeta_generator/properties.py` uses a two-step process:

1. Find the NID (Node ID) of the `build.prop` file:

```python
result = utils.run("dump.erofs", "--path", "/system/build.prop", image,
                   capture_output=True, text=True)
# Parse: "NID: 1234" from output
```

2. Read the file contents by NID:

```python
result = utils.run("dump.erofs", "--nid", nid, "--cat", image,
                   capture_output=True, text=True)
```

This works because EROFS doesn't have a mountable filesystem interface like `debugfs` — files must be accessed by their inode number (NID).

The function tries three paths: `/system/build.prop`, `/build.prop`, `/etc/build.prop`.

### `mkfs.erofs` — EROFS Image Creator

`mkfs.erofs` creates EROFS images from source directories.

```
mkfs.erofs [OPTIONS] FILE SOURCE(s)
```

| Option | Description |
|--------|-------------|
| `-b#` | Block size (default: page size) |
| `-zX` | Compression algorithm: `lz4`, `lz4hc`, `lzma`, `deflate`, `zstd` |
| `-x#` | Xattr tolerance (negative = disable xattrs) |
| `-d<0-9>` | Verbosity (0=quiet, 9=verbose) |

Compression algorithms:

| Algorithm | Level Range | Notes |
|-----------|-------------|-------|
| `lz4` | — | Fastest, moderate compression |
| `lz4hc` | 0-12 | High-compression LZ4 (default: 9) |
| `lzma` | 0-9 (normal), 100-109 (extreme) | Best compression, slower (default: 6) |
| `deflate` | 0-9 | Standard deflate (default: 1) |
| `zstd` | — | Good balance of speed and ratio |

`mkfs.erofs` is not currently used by `vbmeta-generator` because hashtree footers are appended to existing images in-place. It is bundled for potential use.

---

## Sparse Image Tools

Android uses a sparse image format for fastboot flashing. Sparse images contain "chunk" headers that describe runs of data and runs of zeros, allowing efficient storage of images with large empty regions.

### `simg2img` — Sparse to Raw Converter

Converts an Android sparse image to a raw image.

```
simg2img <sparse_image_files> <raw_image_file>
```

Multiple input sparse files can be specified; they are concatenated in order.

#### Used in vbmeta-generator

When a partition image is detected as sparse (magic `0xED26FF3A`), it is converted to raw before:
- Running `debugfs` to extract `build.prop`
- Running `avbtool add_hashtree_footer`

```python
raw = image_path + ".raw"
utils.run("simg2img", image_path, raw, check=True)
```

### `img2simg` — Raw to Sparse Converter

Converts a raw image back to Android sparse format.

```
img2simg [-s] <raw_image_file> <sparse_image_file> [<block_size>]
```

| Option | Description |
|--------|-------------|
| `-s` | Use sparse chunk headers (smaller output) |

#### Used in vbmeta-generator

After adding a hashtree footer to a sparse image's raw conversion, the result is re-sparsified:

```python
utils.run("img2simg", work_img, img_path, check=True)
```

---

## Image Size Management

### Rounding to Block Boundaries

Partition sizes for `lpmake` must be aligned to 4096-byte blocks. The orchestrator rounds up:

```python
part_size = os.path.getsize(img_path)
part_size = ((part_size + 4095) // 4096) * 4096
```

### Filesystem Consistency

Before modifying a filesystem image (adding hashtree, resizing), consistency should be verified. `e2fsck -f` forces a check even on clean filesystems, ensuring any journal replay or minor corruption is resolved.

---

## Quick Reference

| Tool | Use Case | Source Image Type |
|------|----------|-------------------|
| `debugfs -R cat /path` | Extract `build.prop` | EXT4 (raw) |
| `simg2img` → `debugfs` | Extract `build.prop` from sparse | EXT4 (sparse) |
| `dump.erofs --path` + `--nid --cat` | Extract `build.prop` | EROFS |
| `e2fsck -f` | Pre-hashtree consistency check | EXT4 (raw) |
| `simg2img` | Unsparse for processing | sparse ANY |
| `img2simg` | Re-sparse for output | raw ANY |
| `dump.erofs -s` | Inspect EROFS superblock | EROFS |
| `mkfs.erofs` | Create EROFS images | source directory |
