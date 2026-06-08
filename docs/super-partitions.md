# Android Super Partitions (Dynamic Partitions)

Android **dynamic partitions** (also called **super partitions**) allow multiple logical partitions (system, vendor, product, etc.) to be packed into a single physical partition image (`super.img`). This enables flexible resize and OTA update behavior without repartitioning the physical storage.

The super partition is managed by the **LPDM** (Logical Partition Device Mapper) toolchain: `lpmake`, `lpunpack`, and `lpdump`.

## Super Partition Layout

```
super.img
├── Metadata slot 0      ← active slot
│   ├── Partition table  (system_a, vendor_a, product_a, ...)
│   ├── Block device table
│   └── Group table
├── Metadata slot 1      ← inactive slot (for A/B updates)  
├── Metadata slot 2      ← backup
├── Logical partition data
│   ├── system_a.img data
│   ├── vendor_a.img data
│   ├── product_a.img data
│   └── ...
└── Zero padding
```

The metadata is stored at **offset 4096** (the first block) and contains the partition table, group definitions, and extent mappings. The size and slot count are configured when the super image is built.

### Magic

The super partition is identified by the magic bytes `gDla` at byte offset 4096:

```python
with open(path, "rb") as f:
    f.seek(4096)
    if f.read(4) == b"gDla":
        # This is a super image
```

---

## `lpdump` — Inspecting Super Partitions

`lpdump` parses the metadata from a super image and prints the partition layout.

```
lpdump [-s <SLOT#>|--slot=<SLOT#>] [-j|--json] [FILE|DEVICE]
```

Options:

| Option | Description |
|--------|-------------|
| `-s, --slot N` | Slot number to inspect (default: current active slot) |
| `-j, --json` | Output in JSON format |
| `-d, --dump-metadata-size` | Print metadata size in bytes |
| `-a, --all` | Dump all slots |

### Example Output

```
Metadata max size: 65536
Metadata slot count: 3

Partition table:
  Name: product_a
  Group: infinix_dynamic_partitions_a
  Attributes: readonly
  Extents:
    0 .. 5229567
    Linear extent 0 .. 5229567 on super

  Name: system_a
  Group: infinix_dynamic_partitions_a
  Attributes: readonly
  Extents:
    0 .. 1531903
    Linear extent 0 .. 1531903 on super

  Name: system_ext_a
  Group: infinix_dynamic_partitions_a
  Attributes: readonly
  Extents:
    0 .. 2935807
    Linear extent 0 .. 2935807 on super

  Name: vendor_a
  Group: infinix_dynamic_partitions_a
  Attributes: readonly
  Extents:
    0 .. 1660927
    Linear extent 0 .. 1660927 on super

Block device table:
  Name: super
  Size: 9126805504 bytes
  Partition GUID: ...

Group table:
  Name: infinix_dynamic_partitions_a
  Maximum size: 9126805504 bytes
```

### Parsing in vbmeta-generator

The `parse_lpdump_output()` function in `vbmeta_generator/super_partition.py` converts this text output into a `SuperLayout` dataclass:

```python
@dataclass
class SuperLayout:
    device_size: int           # Block device size in bytes
    metadata_size: int         # Max metadata size
    metadata_slots: int        # Number of slots
    groups: dict[str, int]     # Group name → max size
    partitions: list[dict]     # Partition name, group, extents
```

Each partition entry contains:

```python
{
    "name": "product_a",
    "group": "infinix_dynamic_partitions_a",
    "extents": [
        {"start": 0, "end": 5229567},
        # ... more extents if non-contiguous
    ]
}
```

Extent values are **sector numbers** (512 bytes per sector). The partition size in bytes is:

```python
size_bytes = sectors * 512
sectors = sum(e["end"] - e["start"] + 1 for e in part["extents"])
```

---

## `lpunpack` — Extracting Logical Partitions

`lpunpack` extracts logical partitions from a super image into individual `.img` files.

```
lpunpack [--slot=SLOT] <super_image> <output_directory>
```

Options:

| Option | Description |
|--------|-------------|
| `--slot=SLOT` | Slot to extract from (e.g. `0` for `_a`, `1` for `_b`). Default: 0 |

### Behavior

- Extracts partitions from the specified slot
- Output files are named `<partition_name>.img` (e.g. `product_a.img`, `system_a.img`)
- Only partitions with extents in the active slot are extracted
- The extracted images contain the raw filesystem data, without any AVB footer or hashtree

### Used in vbmeta-generator

In Step 2 (`_step_super`), after displaying the super layout, the orchestrator extracts all active (non-zero-size) partitions:

```python
super_extract(super_path, str(self.super_extract_dir))
```

The extracted images are mapped to their base names (stripping `_a`/`_b` suffixes):

```python
# super_extract_dir/product_a.img  →  active_partitions["product"]
# super_extract_dir/system_a.img   →  active_partitions["system"]
```

This mapping allows later steps (hashtree, property extraction) to process partitions by their canonical name regardless of A/B suffix.

---

## `lpmake` — Creating (Rebuilding) Super Partitions

`lpmake` creates a super partition image from individual partition images and metadata configuration.

```
lpmake
  -d,--device-size=SIZE    Size of the block device
  -m,--metadata-size=SIZE  Max size for partition metadata
  -s,--metadata-slots=N    Number of metadata slots
  -p,--partition=DATA      Add a partition
  -i,--image=FILE          Partition image data
  -g,--group=GROUP:SIZE    Define a partition group
  -o,--output=FILE         Output file
  [--alignment=N]          Optimal partition alignment (default: 4096)
  [--sparse]               Output a sparse image
  [--block-size=N]         Physical block size (default: 4096)
```

### Partition Format

The `--partition` option uses a colon-delimited format:

```
--partition name:attributes:size[:group]
```

| Field | Description |
|-------|-------------|
| `name` | Partition name (e.g. `system_a`) |
| `attributes` | Partition attributes (must be `none` in current lpmake) |
| `size` | Size of the partition in bytes |
| `group` | Optional group name (must match a defined `--group`) |

### Group Format

```
--group group_name:max_size
```

Groups define the maximum total size of all partitions within them. This is used for dynamic resize.

### Image Mapping

```
--image partition_name=image_file_path
```

Maps a partition name to its image data file. The image must be ≤ the partition size.

### Rebuild Algorithm

The `rebuild()` function in `vbmeta_generator/super_partition.py` reconstructs a super image by:

1. Using the original `device_size`, `metadata_size`, and `metadata_slots` from the parsed layout
2. Re-creating the same groups with their original sizes
3. For each partition:
   - If the partition was **modified** (has a new image in `modified_images`): uses the modified image's actual size (rounded up to block boundary)
   - If the partition was **not modified**: uses the original extent-derived size (sectors × 512). Partitions with zero size are skipped.
4. Writing the output with `--image` for each modified partition

```python
part_size = os.path.getsize(img_path)
part_size = ((part_size + 4095) // 4096) * 4096  # round up to block

cmd.extend(["--partition", f"{name}:none:{part_size}:{group}"])
cmd.extend(["--image", f"{name}={img_path}"])
```

### Constraints

- Partition names must match exactly between `--partition` and `--image`
- The total of all partition sizes plus metadata overhead must fit within `device_size`
- Groups enforce maximum total sizes across their partitions
- Attributes field (`none`) is required even though it's always "none" in modern lpmake

---

## A/B Slot Handling

Super partitions use A/B slot naming conventions:

- **Slot A**: partitions suffixed with `_a` (e.g., `system_a`, `vendor_a`)
- **Slot B**: partitions suffixed with `_b`

Only the **active slot** is extracted (via `lpunpack --slot=0` for slot A). The inactive slot's partitions are preserved verbatim in the rebuilt super image (their extent data remains unchanged).

When rebuilding, vbmeta-generator maps modified base names back to the super partition names:

```python
self._super_name_map = {
    "system": "system_a",
    "product": "product_a",
    "vendor": "vendor_a",
    # ...
}
```

So modifying `system` (extracted as `system_a.img`) maps to `--partition system_a:none:size --image system_a=system.img`.

---

## SuperLayout Data Model

```python
@dataclass
class SuperLayout:
    device_size: int = 0              # Physical partition size
    metadata_size: int = 65536        # Metadata overhead
    metadata_slots: int = 3           # Number of slot copies
    groups: dict[str, int]            # e.g. {"infinix_dynamic_partitions_a": 9126805504}
    partitions: list[dict]            # List of partition descriptors
```

The `get_active_partitions()` helper filters the partition list to those with non-empty extents:

```python
def get_active_partitions(layout: SuperLayout) -> list[dict]:
    return [p for p in layout.partitions if p.get("extents")]
```

## References

- AOSP source: `system/core/fs_mgr/liblp/` — LPDM implementation
- `lpmake` / `lpunpack` / `lpdump`: `system/core/fs_mgr/tools/`
