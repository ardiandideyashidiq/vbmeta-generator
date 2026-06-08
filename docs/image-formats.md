# Image Format Detection

`vbmeta-generator` must identify the format of each `.img` file in the ROM directory before deciding how to process it. Detection is based on magic bytes and structural heuristics, performed by `detect_image()` in `vbmeta_generator/image.py`.

## Image Types

| Type | Magic / Heuristic | Used For |
|------|-------------------|----------|
| `SUPER` | `gDla` at offset 4096 | `super.img` (dynamic partitions) |
| `BOOTIMG` | `ANDROID!` at offset 0 | `boot.img`, `vendor_boot.img`, `recovery.img` |
| `SPARSE` | `0xED26FF3A` (LE u32) at offset 0 | Sparse EXT4 images from `img2simg` / `fastboot` |
| `EROFS` | `0xE0F5E1E2` (LE u32) at offset 1024 | EROFS filesystem images |
| `EXT4` | `0xEF53` (LE u16) at offset 1080 | Raw EXT4 filesystem images |
| `OTHER` | None of the above | `dtbo.img`, `vbmeta.img`, unknown |

## Detection Order

Detection follows a specific order to avoid false positives. Each check returns immediately on match.

```
1.  Check SUPER magic at offset 4096
2.  Check BOOTIMG magic at offset 0
3.  Check SPARSE magic at offset 0
4.  Check EROFS magic at offset 1024
5.  Check EXT4 magic at offset 1080
6.  Fall through to OTHER
```

---

## 1. SUPER (`super.img`)

**Magic**: `gDla` (4 bytes) at **byte offset 4096**.

The super partition uses the Android Logical Partition format. Its header starts at offset 4096 (one typical block size into the image), where the magic `gDla` identifies the LPDM (Logical Partition Device Mapper) metadata.

```python
with open(path, "rb") as f:
    f.seek(4096)
    super_magic = f.read(4)
if super_magic == b"gDla":
    info.type = ImageType.SUPER
```

A super image is **never** sparse — it is always a raw image containing the LPDM metadata at offset 4096, followed by the logical partition data.

The super image is parsed further by `lpdump` (see [super-partitions.md](super-partitions.md)).

---

## 2. BOOTIMG (`boot.img`)

**Magic**: `ANDROID!` (8 bytes) at **offset 0**.

The boot image header starts with `ANDROID!`. This covers:

- `boot.img` — kernel, ramdisk, dtb
- `vendor_boot.img` — vendor ramdisk, bootconfig
- `recovery.img` — recovery kernel and ramdisk

```python
if header[:8] == b"ANDROID!":
    info.type = ImageType.BOOTIMG
```

The boot image may or may not have an AVB footer appended (detected separately by `detect_avb()` — see below).

---

## 3. SPARSE (Android Sparse Format)

**Magic**: `0xED26FF3A` (little-endian 32-bit) at **offset 0**.

The Android sparse image format is used by `fastboot` and `img2simg` to represent raw images with "don't care" chunks for reduced size and faster flashing.

```python
magic = struct.unpack("<I", header[:4])[0]
if magic == 0xED26FF3A:
    info.type = ImageType.SPARSE
    info.is_sparse = True
    info.fstype = "ext4"
```

The sparse header has this layout:

| Offset | Size | Field |
|--------|------|-------|
| 0 | 4 | Magic (`0xED26FF3A`) |
| 4 | 2 | Major version (usually 1) |
| 6 | 2 | Minor version (usually 0) |
| 8 | 2 | File header size |
| 10 | 2 | Chunk header size |
| 12 | 4 | Block size (typically 4096) |
| 16 | 4 | Total blocks in the expanded image |
| 20 | 4 | Total chunks in this sparse file |
| 24 | 4 | CRC32 checksum (0 = unused) |

Sparse images are always treated as EXT4. They must be converted to raw format (`simg2img`) before the hashtree footer can be added, then converted back (`img2simg`) for output.

---

## 4. EROFS (Enhanced Read-Only File System)

**Magic**: `0xE0F5E1E2` (little-endian 32-bit) at **offset 1024**.

EROFS is a Linux read-only filesystem designed for Android that offers better compression ratios and performance than EXT4. It was introduced as the default for system partitions in newer devices.

```python
magic = struct.unpack("<I", header[1024:1028])[0]
if magic == 0xE0F5E1E2:
    info.type = ImageType.EROFS
    info.fstype = "erofs"
```

The EROFS superblock starts at offset 1024 (matching the Linux kernel's `BLOCK_SIZE_BITS` default). The detection uses the first 4 bytes at that offset.

EROFS partitions are processed with `dump.erofs` for `build.prop` extraction and `mkfs.erofs` for recreation (though vbmeta-generator does not recreate them — hashtree footers are appended in-place).

---

## 5. EXT4

**Magic**: `0xEF53` (little-endian 16-bit) at **offset 1080**.

The standard EXT4 superblock magic resides at offset 0x438 (1080) from the start of the partition:

```python
magic = struct.unpack("<H", header[1080:1082])[0]
if magic == 0xEF53:
    info.type = ImageType.EXT4
    info.fstype = "ext4"
```

This check comes **after** the sparse check because raw EXT4 images are less common than sparse ones in ROM distributions.

---

## 6. OTHER

Any image that doesn't match the above criteria falls into the `OTHER` category. This typically includes:

- `dtbo.img` (device tree blob overlay)
- `vbmeta.img` and `vbmeta_system.img` / `vbmeta_vendor.img`
- Vendor ramdisks
- Unknown or corrupted images

`OTHER` images are generally **skipped** by the main processing pipeline, except:

- `dtbo.img` is detected by partition name heuristic (`re.sub(r"(_a|_b)?$", "", stem)`) and signed with `add_hash_footer` if its partition name evaluates to `"dtbo"`
- Any `OTHER` image is still checked for AVB footers by `detect_avb()`

---

## AVB Footer Detection

The `detect_avb()` function checks for an AVB footer **independently** of image type detection. It reads the last 64 bytes of the image and looks for the `AVBf` magic:

```python
with open(p, "rb") as f:
    f.seek(os.path.getsize(p) - 64)
    tail = f.read(64)

if tail[:4] == b"AVBf":
    info.has_avb = True
```

If `AVBf` is found, `avbtool info_image --image` is called to extract the full VBMeta details:

- `Algorithm` — the signing algorithm (e.g. `SHA256_RSA2048`)
- `Rollback Index` — rollback counter value
- `Flags` — VBMeta flags
- All descriptors (hash, hashtree, chain partition, prop)

This is critical for detecting pre-signed images. For example, a `boot.img` may already have an AVB footer from the factory, in which case `vbmeta-generator` displays a warning and re-signs it.

```python
info.has_avb = True
info.avb_algorithm = matched_algorithm
info.avb_rollback_index = matched_rollback
info.avb_descriptors = [parsed descriptor list]
```

---

## Size Heuristics

The `ImageInfo` object also infers the **partition name** from the filename by stripping A/B slot suffixes:

```python
name = Path(path).stem
return re.sub(r"(_a|_b)?$", "", name)
```

So `system_a.img` → `system`, `product_b.img` → `product`, `super.img` → `super`.

---

## Detection Flow in Orchestrator

```
detect_image(path)       → ImageInfo (type, fstype, is_sparse)
       │
       ▼
detect_avb(info, path)   → ImageInfo (has_avb, algorithm, rollback, descriptors)

Stored in:
    self.image_infos[filename] = {
        "info": info,
        "type": info.type,
        "fstype": info.fstype,
        "size": info.size,
        "has_avb": info.has_avb,
        "avb_algorithm": info.avb_algorithm,
        "avb_descriptors": info.avb_descriptors,
        "full_path": str(f),
    }
```
