# dm-verity and FEC

dm-verity is a Linux kernel device-mapper target that provides **transparent integrity checking** of block devices. It ensures every block read from a device matches a pre-computed cryptographic hash, detecting any corruption or tampering at the block level.

AVB leverages dm-verity for filesystem partitions (`system`, `vendor`, `product`, `system_ext`). The hashtree descriptors in AVB contain the exact parameters needed to initialize dm-verity at boot.

## Merkle Hash Tree (Hashtree)

The integrity checking is based on a **Merkle hash tree** (also called a hash tree or hashtree). This is a tree structure where:

- **Leaves**: Each block of the filesystem data is hashed
- **Internal nodes**: Each block of hashes from the level below is hashed
- **Root**: A single root hash that commits to the entire data set

```
Level 2 (root):   hash(hash_1 + hash_2)
                   /              \
Level 1:    hash(data_1 + data_2)  hash(data_3 + data_4)
               /      \              /       \
Level 0:   data_1    data_2       data_3    data_4
           (block)   (block)      (block)   (block)
```

### Verification at Read Time

When the kernel reads a data block through dm-verity:

1. The block's hash is computed
2. It's compared against the corresponding leaf hash in the tree
3. If it matches, the hash at the next level up is verified
4. This continues up to the root hash
5. The root hash is compared against the trusted value from the AVB descriptor

If any level fails, the block is considered corrupt. The kernel can either return an I/O error or, if FEC is enabled, attempt correction.

### Space Overhead

The hashtree adds a ~1/128 overhead for SHA-256 with 4096-byte blocks (each hash is 32 bytes covering 4096 bytes = 128 blocks of data per hash block). For a 4 GiB partition:

- 4 GiB data → 1,048,576 data blocks (4096-byte)
- Level 0 hashes: 1,048,576 × 32 bytes = 32 MiB = 8192 hash blocks
- Level 1 hashes: 8192 × 32 bytes = 256 KiB = 64 hash blocks
- Level 2 hashes: 64 × 32 bytes = 2 KiB = 1 hash block
- Total overhead: ~32.25 MiB

## On-Disk Layout

When `avbtool add_hashtree_footer` processes a filesystem image, the resulting layout is:

```
+----------------------------------+
| Filesystem data                  |
|  (EXT4 or EROFS)                 |
+----------------------------------+
| Hashtree (Merkle tree)           |
|  [tree_offset, tree_size]        |
+----------------------------------+
| FEC parity data (optional)       |
|  [fec_offset, fec_size]          |
+----------------------------------+
| VBMeta struct                    |
|  (signed, contains hashtree      |
|   descriptor + properties)       |
+----------------------------------+
| AVB footer (64 bytes, "AVBf")    |
+----------------------------------+
```

The hashtree descriptor in the VBMeta struct records:

- **tree_offset**: byte offset from start of partition to the hashtree
- **tree_size**: size of the hashtree in bytes
- **data_block_size**: typically 4096
- **hash_block_size**: typically 4096 (may differ from data block size)
- **salt**: random salt used in hashing (prevents precomputation attacks)
- **root_digest**: the root hash of the Merkle tree
- **hash_algorithm**: sha1 or sha256

## FEC (Forward Error Correction)

dm-verity optionally includes **Forward Error Correction** using **Reed-Solomon codes**. FEC allows the kernel to **recover corrupted blocks** rather than just reporting errors.

### How FEC Works

FEC uses a **Reed-Solomon RS(255, k)** code, where:

- `k = 255 - fec_roots`
- Each 255-byte codeword contains `k` message bytes and `fec_roots` parity bytes
- Default `fec_roots` = 2, giving an RS(255, 253) code (~0.8% overhead)

The FEC data is **interleaved** across the entire device for maximum burst error protection:

1. The message data region (data blocks + hash blocks + optional metadata) is divided into `k`-byte chunks
2. For each chunk, `fec_roots` parity bytes are computed
3. The codewords are interleaved: byte 0 of all codewords, then byte 1, etc.
4. This means a single-block corruption spreads across many codewords, each of which can correct its share

### FEC Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `fec_num_roots` | 2 | Number of parity bytes per codeword (2-24) |
| `fec_offset` | — | Byte offset to the start of FEC data |
| `fec_size` | — | Size of FEC parity data in bytes |

Higher `fec_num_roots` provides stronger correction but higher overhead:
- `fec_num_roots=2` → ~0.8% overhead (RS(255,253))
- `fec_num_roots=4` → ~1.6% overhead (RS(255,251))
- `fec_num_roots=24` → ~10.4% overhead (RS(255,231))

### FEC with dm-verity

The FEC integration has a crucial property: **FEC-corrected blocks are always re-verified against the hash tree**. This means:

- FEC doesn't reduce security — a block cannot be silently corrupted
- FEC only activates when a hash verification **fails**
- In the common case (no errors), FEC adds zero overhead
- Burst errors spanning many blocks can be recovered due to interleaving

### The `fec` Binary

The bundled `fec` binary is a userspace tool for working with dm-verity FEC data:

```
fec --help
```

Options include:

| Operation | Description |
|-----------|-------------|
| `--encode INPUT OUTPUT` | Encode FEC parity data for an image |
| `--decode INPUT OUTPUT` | Decode and attempt correction using FEC data |
| `--verify INPUT` | Verify integrity using FEC data |
| `--roots N` | Number of Reed-Solomon roots (default: 2) |

The `fec` binary is invoked internally by `avbtool add_hashtree_footer` when generating FEC data (unless `--do_not_generate_fec` is specified). It is called via `avbtool` rather than directly by `vbmeta-generator`.

## Kernel dm-verity Parameters

When the bootloader initializes dm-verity, it constructs a device-mapper table entry. The parameters include:

```
<version> <data_device> <hash_device> <data_blocks>
<hash_start> <hash_algorithm> <root_digest> <salt>
[<use_fec_from_device> <fec_blocks> <fec_start> [...other options...]]
```

Key parameters:

| Parameter | Source in AVB |
|-----------|---------------|
| `version` | 1 (always) |
| `data_blocks` | `image_size / data_block_size` |
| `hash_start` | `tree_offset / hash_block_size` |
| `hash_algorithm` | From hashtree descriptor |
| `root_digest` | From hashtree descriptor |
| `salt` | From hashtree descriptor |
| `use_fec_from_device` | Same as data device (or separate) |
| `fec_blocks` | `fec_size / data_block_size` |
| `fec_start` | `fec_offset / data_block_size` |

## Relationship Between AVB and dm-verity

AVB provides the **trusted delivery** of dm-verity parameters. Without AVB, dm-verity parameters must be passed via kernel cmdline, which is less secure and harder to manage.

With AVB:

1. AVB descriptors are cryptographically signed (the VBMeta struct is signed)
2. The root digest is embedded inside the signed VBMeta
3. The bootloader reads the VBMeta, verifies the signature, extracts parameters
4. The bootloader activates dm-verity with these parameters

The chain of trust is:

```
Hardware Root of Trust → Bootloader → vbmeta.img (signed) →
  hashtree descriptor (root_digest, salt, etc.) →
    dm-verity kernel target → verified filesystem mount
```

## Hash Algorithm Selection

| Algorithm | Digest Size | Default For | Notes |
|-----------|-------------|-------------|-------|
| `sha1` | 20 bytes | `add_hashtree_footer` | Legacy, widely compatible |
| `sha256` | 32 bytes | `add_hash_footer` | Stronger, recommended |

`vbmeta-generator` uses `sha256` for EROFS partitions and `sha1` for EXT4 (avbtool default). The `add_hash_footer` command (for `boot.img`) always uses `sha256`.

## Security Considerations

- **Rollback protection** is separate from dm-verity — it's enforced by the bootloader using AVB's rollback index mechanism
- **FEC + verification** is strictly stronger than FEC alone — damage detected by hash verification triggers FEC recovery, then re-verification
- **Salt uniqueness**: each image should use a unique salt (randomly generated by avbtool by default); this prevents cross-image hash comparisons
- **dm-verity is read-only**: the device-mapper target denies writes, so it cannot be used for mutable data

## References

- Linux kernel documentation: `Documentation/admin-guide/device-mapper/verity.rst`
- AOSP AVB source: `libavb/avb_hashtree_descriptor.h`
- Reed-Solomon FEC in dm-verity: Android `external/avb/libavb/avb_hashtree_descriptor.h`
- `veritysetup(8)` man page — userspace dm-verity management via cryptsetup
