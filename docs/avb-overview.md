# Android Verified Boot 2.0 (AVB)

Android Verified Boot 2.0, also known as **AVB** (and formerly as *Verified Boot 2.0*), is the verified boot implementation introduced in Android 8.0 (Oreo) alongside Project Treble. It replaces the older `dm-verity` + `boot signature` approach with a unified, cryptographically signed metadata structure called **VBMeta**.

AVB ensures that every partition mounted at boot time is exactly as the device owner (OEM) intended — any modification, whether accidental corruption or deliberate tampering, causes the bootloader to reject the partition.

## Core Concepts

### The VBMeta Struct

The central data structure in AVB is the **VBMeta struct**. It is a signed container that holds:

- **Descriptors** — references to other partitions (hashes, hashtrees, chain partitions)
- **Properties** — key-value metadata (fingerprint, OS version, security patch level)
- **Rollback index** — prevents rolling back to a vulnerable version
- **Flags** — e.g. `AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED`

The VBMeta struct is divided into three blocks on disk:

```
+-----------------------------------------+
| Header data — fixed size (256 bytes)    |
+-----------------------------------------+
| Authentication data — variable size     |
|   (hash + signature of the VBMeta data) |
+-----------------------------------------+
| Auxiliary data — variable size          |
|   (public key + descriptors + props)    |
+-----------------------------------------+
```

The **header** (defined by `AvbVBMetaImageHeader`) is always 256 bytes and contains offsets and sizes for the other two blocks, along with the algorithm type, rollback index, flags, and release string.

The **authentication data** block holds the hash and RSA signature that cryptographically sign the entire VBMeta struct. The algorithm type in the header determines the hash function (SHA256, SHA512) and key size (RSA 2048, 4096).

The **auxiliary data** block contains the public key used for verification, plus all descriptors (hash, hashtree, chain partition) and properties. These are integrity-protected by the signature in the authentication block.

### AVB Footer

The footer is a 64-byte structure placed at the **end** of a partition to point to the VBMeta struct. Its magic is `AVBf` (`0x41564266`).

```
Offset  Size  Field
------  ----  -----
0        4    magic ("AVBf")
4        4    version_major
8        4    version_minor
12       8    original_image_size
20       8    vbmeta_offset     — offset of VBMeta struct from partition start
28       8    vbmeta_size       — size of VBMeta struct (header + auth + aux)
36      28    reserved (zeros)
```

When `avbtool` detects an `AVBf` footer on a partition, it reads the VBMeta struct from `vbmeta_offset`. This allows partitions to be updated in place without needing to update the top-level `vbmeta.img` — the footer always points to the correct location.

## Descriptor Types

### Hash Descriptor

Used for small, typically raw partitions like `boot.img` and `dtbo.img`. The descriptor contains:

- Partition name
- Hash algorithm (default: sha256)
- Salt (random, for each image)
- Root digest (the hash of the entire partition content)
- Flags (e.g. `DO_NOT_USE_AB`)

The bootloader reads the entire partition, hashes it, and compares against the root digest in the descriptor. If they match, the partition is trusted.

### Hashtree Descriptor

Used for large filesystem partitions (`system`, `vendor`, `product`, etc.). The descriptor contains:

- Partition name
- dm-verity version (usually 1)
- Image size (the data covered by the hashtree)
- Tree offset and size (where the Merkle hash tree is located in the image)
- Data and hash block sizes (typically 4096 and 4096)
- FEC (forward error correction) offset, size, and number of roots
- Hash algorithm (default: sha1 for compatibility)
- Salt and root digest

The hashtree itself is appended to the partition image data. The kernel's `dm-verity` driver traverses the tree on every read: each data block's hash is checked against the corresponding leaf hash, which is checked up the tree to the root digest in the descriptor.

For details on hashtree structure, see [dm-verity-and-fec.md](dm-verity-and-fec.md).

### Chain Partition Descriptor

This is the mechanism that enables **multiple signing authorities**. A chain partition descriptor delegates trust for a specific partition name to a different signing key. It contains:

- Partition name (e.g. `vbmeta_system`)
- Rollback index location (a unique slot number for this chain)
- Public key (the trusted key that must sign the target partition)

At boot, the bootloader reads the target partition (identified by name), extracts its VBMeta struct, and verifies its signature against the public key stored in the chain descriptor. This allows the OEM to sign the top-level `vbmeta`, while the SoC vendor signs `vbmeta_vendor` and the OEM signs `vbmeta_system`.

### Property Descriptor (`Prop`)

Properties are arbitrary key-value pairs embedded in the VBMeta struct. Common properties include:

- `com.android.build.system.fingerprint`
- `com.android.build.system.os_version`
- `com.android.build.system.security_patch`

These properties are extracted from the partition's `build.prop` file and embedded both in the partition's hashtree footer VBMeta and in the chained vbmeta images.

## Trust Chain

AVB establishes a chain of trust from the bootloader to every mounted partition:

```
Bootloader (Permanent): embedded AVB public key
  │
  ▼
vbmeta (Slot A/B): signed, contains descriptors
  ├── hash descriptor → boot.img (verified by hash)
  ├── chain descriptor → vbmeta_system (→ system, system_ext, product)
  ├── chain descriptor → vbmeta_vendor (→ vendor)
  └── (optional) include descriptor → dtbo.img
```

### Verification Flow at Boot

1. Bootloader reads the `vbmeta` partition (from the active A/B slot)
2. Verifies its signature using the embedded public key
3. Checks rollback index against stored NVRAM value (anti-rollback protection)
4. For each **hash descriptor**: hashes the referenced partition and compares to the stored digest
5. For each **hashtree descriptor**: reads the root digest from the descriptor, initializes `dm-verity` with it. The kernel will verify each block on first read.
6. For each **chain descriptor**: locates the chained partition by name, reads its VBMeta struct (via footer or direct), verifies its signature against the public key in the chain descriptor, then recursively processes hash/hashtree descriptors within it.

### Chained vbmeta Example

```
vbmeta.img (signed by OEM key)
  ├── hash: boot.img
  ├── chain: vbmeta_system → OEM system key
  │     ├── hashtree: system
  │     ├── hashtree: system_ext
  │     └── hashtree: product
  └── chain: vbmeta_vendor → SoC vendor key
        └── hashtree: vendor
```

This separation allows the SoC vendor (`vbmeta_vendor`) and OEM (`vbmeta_system`) to sign their own partitions independently, while the top-level `vbmeta` controls the overall trust policy.

## Rollback Protection

AVB uses **rollback indexes** to prevent an attacker from flashing an older, vulnerable version of Android. The bootloader maintains a set of rollback index values in tamper-evident storage (NVRAM or Replay Protected Memory Block — RPMB).

At each boot:

1. The VBMeta struct in `vbmeta` carries a `rollback_index` value
2. The bootloader checks that this value is >= the stored rollback index
3. If it passes, the bootloader updates the stored rollback index to this value
4. Chain partitions also carry their own rollback indexes (at unique rollback index locations)

This ensures you cannot downgrade a single partition to an older version without also downgrading the rollback index, which is impossible if the NVRAM is secure.

## Flags

The VBMeta header includes flags that modify verification behavior:

| Flag | Value | Meaning |
|------|-------|---------|
| `AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED` | `1 << 0` = 1 | Do not verify hashtree partitions (system, vendor, etc.). Hash partitions (boot) are still verified. |
| `AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED` | `1 << 1` = 2 | Disable all verification. Descriptors are not parsed. Only the key is checked. |

Flags are commonly set to `1` to allow using `adb remount` (which modifies system partitions) while still verifying `boot.img`.

## Algorithm Types

| Algorithm | Hash | RSA Key Size |
|-----------|------|--------------|
| `NONE` | — | No signing |
| `SHA256_RSA2048` | SHA256 | 2048 bits |
| `SHA256_RSA4096` | SHA256 | 4096 bits |
| `SHA512_RSA4096` | SHA512 | 4096 bits |

`SHA256_RSA2048` is the most common choice, balancing security and performance. `NONE` is used for testing only.

## A/B (Seamless) Updates

AVB is fully compatible with A/B (seamless) update slots. Each slot (`_a`, `_b`) has its own set of partitions including `vbmeta_a` / `vbmeta_b`. The bootloader selects the active slot and verifies its vbmeta.

Hashtree and hash descriptors implicitly target the active slot's partitions. The `DO_NOT_USE_AB` flag on a descriptor excludes it from A/B logic.

## Relationship to dm-verity

AVB does **not** replace dm-verity — it wraps it. The hashtree descriptors in AVB contain the exact same data that dm-verity needs: root digest, salt, hash algorithm, block size, and FEC parameters. The bootloader passes these to the kernel's `dm-verity` driver to set up integrity checking on block devices.

In older Android versions (pre-8.0), dm-verity metadata was built with `build_verity_tree` and `build_verity_metadata` and passed via kernel cmdline. AVB subsumes this by embedding the metadata in VBMeta structs, eliminating the need for cmdline-based verity setup.

## References

- AOSP AVB source: `platform/external/avb` — [android.googlesource.com](https://android.googlesource.com/platform/external/avb/)
- `libavb/avb_vbmeta_image.h` — VBMeta header struct definition
- `libavb/avb_footer.h` — Footer struct definition
- `libavb/avb_hashtree_descriptor.h` — Hashtree descriptor struct
- `libavb/avb_hash_descriptor.h` — Hash descriptor struct
- `libavb/avb_chain_partition_descriptor.h` — Chain partition descriptor
- [source.android.com/docs/security/features/avb](https://source.android.com/docs/security/features/avb)
