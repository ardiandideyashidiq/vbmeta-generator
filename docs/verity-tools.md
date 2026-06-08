# dm-verity Build Tools

Before AVB integrated dm-verity metadata into VBMeta structs, Android used a separate toolchain to build and manage dm-verity data. These tools are still bundled and useful for understanding the lower-level verity mechanisms.

These tools are **not directly invoked** by `vbmeta-generator` — avbtool's `add_hashtree_footer` command replaces their functionality. They are bundled for reference and potential use in custom workflows.

## Tool Overview

| Tool | Purpose | Replaced By |
|------|---------|-------------|
| `build_verity_tree` | Build a Merkle hash tree from a data image | `avbtool add_hashtree_footer` (internal) |
| `build_verity_metadata` | Build verity metadata (kernel cmdline params) | `avbtool calculate_kernel_cmdline` |
| `build_image` | Build an Android filesystem image with verity | AOSP build system |
| `verity_signer` | Sign a verity hashtree (shell script wrapper) | `avbtool` key options |
| `verity_verifier` | Verify a dm-verity protected image | `avbtool verify_image` |
| `generate_verity_key` | Generate RSA keys for verity signing | `openssl` + `avbtool extract_public_key` |

---

## `build_verity_tree`

Builds a Merkle hash tree from a raw data image. This is the foundational operation that all dm-verity relies on.

```
build_verity_tree [options] <input_file> <output_file>
```

### Input/Output

- **Input**: A raw block device or image file containing filesystem data
- **Output**: A file containing the Merkle hash tree (hashes only, no VBMeta or footer)

The output tree file starts with a short header followed by the hash tree levels:

```
+-------------------+
| Version (4 bytes) |
+-------------------+
| Hash block size   |
| (4 bytes)         |
+-------------------+
| Data block size   |
| (4 bytes)         |
+-------------------+
| Salt (32 bytes)   |
+-------------------+
| Root hash (32B    |
| for SHA256)       |
+-------------------+
| Hash tree levels  |
| (variable size)   |
+-------------------+
```

### Algorithm

1. Read input data in `data_block_size` chunks
2. Hash each chunk with the chosen algorithm + salt
3. Group the resulting hashes into `hash_block_size` chunks
4. Hash each group to create the next level
5. Repeat until a single hash remains (the root hash)
6. Write the tree to the output file

### Relationship to avbtool

`avbtool add_hashtree_footer` performs the same operation internally, then additionally:
- Appends the tree to the image
- Computes FEC data
- Creates a VBMeta struct with the hashtree descriptor
- Signs the VBMeta
- Writes the AVB footer

---

## `build_verity_metadata`

Builds the verity metadata that describes a dm-verity protected partition. This includes the kernel command-line parameters needed to set up dm-verity at boot.

```
build_verity_metadata [options] <input_image> <output_metadata>
```

Options:

| Option | Description |
|--------|-------------|
| `--data_device` | Block device for data (e.g. `/dev/block/bootdevice/by-name/system`) |
| `--hash_device` | Block device for hash tree (often same as data_device) |
| `--expected_len` | Expected length of the verity hash tree |
| `--verity_hash` | Root hash of the Merkle tree |
| `--verity_salt` | Salt used in hash computation |
| `--verity_signer_path` | Path to the verity signer binary |
| `--verity_key` | Key file for signing |

The output metadata is a table that can be parsed by the kernel's `dm-verity` driver:

```
<version> <data_dev> <hash_dev> <data_blocks> <hash_start>
<hash_algorithm> <root_digest> <salt>
```

### Example

```bash
build_verity_tree system.img system_tree.img
ROOT_HASH=$(build_verity_metadata --data_device /dev/block/system \
    --hash_device /dev/block/system \
    --expected_len $(stat -c%s system_tree.img) \
    --verity_hash $(dd if=system_tree.img bs=4 skip=3 count=8 2>/dev/null | xxd -p -c 32) \
    --verity_salt 00000000000000000000000000000001)
```

---

## `build_image`

Builds an entire Android filesystem image, optionally including verity metadata. This is the tool that the AOSP build system calls to produce `system.img`.

```
build_image [options] <source_dir> <build_spec> <output_image>
```

Options:

| Option | Description |
|--------|-------------|
| `-t` | Type: ext4, erofs, etc. |
| `-s` | Sparse output |
| `-v` | Include verity hash tree |
| `--verity_key KEY` | Key for signing verity metadata |
| `--verity_signer_path PATH` | Path to `verity_signer` |

The `build_image` script reads a build specification file that defines the filesystem parameters:

```
build_spec example:
  build_path: out/target/product/xyz/system
  ext4_mkuserimg: true
  ext4_rsize: ext4
  fs_type: ext4
  ext4_blocksize: 4096
  use_sdcardfs: false
```

### Relationship to avbtool

In modern Android with AVB, `build_image` is still used for creating the filesystem, but verity metadata is added by `avbtool add_hashtree_footer` in a separate step rather than by `build_image` itself.

---

## `verity_signer`

A shell script that wraps the actual signing operation for verity hash trees. It calls `openssl` to sign the root hash.

```
verity_signer <hash_tree> <key> <signature_output>
```

The script:
1. Reads the root hash from the verity hash tree file
2. Signs it with the RSA private key using `openssl pkeyutl -sign`
3. Writes the signature to the output file

This is a thin wrapper around:

```bash
openssl pkeyutl -sign -inkey "$key" -in "$root_hash" -out "$signature_output"
```

In AVB, this signing is done internally by `avbtool` using its `--key` option, making `verity_signer` unnecessary.

---

## `verity_verifier`

A userspace tool that verifies a dm-verity protected image without kernel involvement. It reads the data, computes the hash tree, and compares the root digest.

```
verity_verifier [options] <data_image> <hash_tree>
```

Options:

| Option | Description |
|--------|-------------|
| `-r, --root_hash HEX` | Expected root hash (hex string) |
| `-s, --salt HEX` | Salt used in hash computation |
| `-d, --data_blocks N` | Number of data blocks |
| `-b, --block_size N` | Block size (default: 4096) |
| `-a, --hash_algorithm ALGO` | Hash algorithm (default: sha256) |

The verifier reads data blocks, recomputes the hash tree, and compares each level against the stored tree. If the root hash matches, the image is verified.

### Relationship to avbtool

`avbtool verify_image` provides superset functionality — it verifies the VBMeta signature, checks hashtree descriptors, and can follow chain partitions:

```bash
avbtool verify_image --image system.img --follow_chain_partitions
```

---

## `generate_verity_key`

Generates an RSA key pair for verity signing. This is a minimal tool that creates a key in the format expected by the AOSP verity toolchain.

```
generate_verity_key OUTPUT_KEY
```

The output key is a PEM-encoded RSA private key. It can be used with any of the other verity tools.

In `vbmeta-generator`, keys are generated using `openssl genpkey` directly (as avbtool accepts standard PEM format):

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out avb.key
avbtool extract_public_key --key avb.key --output avb.avbpubkey
```

---

## Legacy vs AVB Workflow

### Pre-AVB (Android 7.x and earlier)

```bash
# 1. Build filesystem
build_image system_dir system_spec.txt system.raw

# 2. Build hash tree
build_verity_tree system.raw system_tree.img

# 3. Build verity metadata
ROOT_HASH=$(... extract root hash from system_tree.img ...)
build_verity_metadata ... > verity_metadata

# 4. Sign
verity_signer system_tree.img key.pem tree.signature

# 5. Append tree + metadata to image
cat system_tree.img verity_metadata >> system.raw

# 6. Convert to sparse (optional)
img2simg system.raw system.img
```

### With AVB (Android 8.0+)

```bash
# 1. Build filesystem (same)
build_image system_dir system_spec.txt system.raw

# 2. Single avbtool command does everything:
#    hash tree + FEC + VBMeta + signing + footer
avbtool add_hashtree_footer \
    --image system.raw \
    --partition_name system \
    --partition_size $SIZE \
    --key avb.key \
    --algorithm SHA256_RSA2048

# 3. Convert to sparse (optional)
img2simg system.raw system.img
```

### With vbmeta-generator

```python
# All of the above is abstracted by avb.add_hashtree_footer().
# The orchestrator handles the end-to-end flow including
# super partition extraction, property injection, and
# chained vbmeta creation.
```

---

## References

- AOSP `system/extras/verity/` — verity builder sources
- `build_verity_tree.cpp` — hash tree construction
- `build_verity_metadata.cpp` — metadata generation
- Linux kernel `Documentation/admin-guide/device-mapper/verity.rst`
