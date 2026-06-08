# avbtool Reference

`avbtool` is the userspace utility from the AOSP `external/avb` project for creating, inspecting, and verifying Android Verified Boot (AVB) images. The version bundled with `vbmeta-generator` is **avbtool 1.3.0**.

This document covers every subcommand and their options.

## Synopsis

```
avbtool <subcommand> [options]
```

## Subcommands

| Subcommand | Purpose |
|------------|---------|
| `add_hash_footer` | Add hash descriptor + VBMeta footer to an image |
| `add_hashtree_footer` | Add hashtree descriptor + Merkle tree + VBMeta footer to an image |
| `make_vbmeta_image` | Create a standalone vbmeta image (no image payload) |
| `info_image` | Display VBMeta/footer information from an image |
| `verify_image` | Verify an image's signature and descriptors |
| `extract_public_key` | Extract public key from a PEM private key |
| `extract_vbmeta_image` | Extract embedded VBMeta struct from a footer |
| `erase_footer` | Remove AVB footer from an image |
| `zero_hashtree` | Zero out hashtree and FEC data in an image |
| `resize_image` | Resize a partition image that has a footer |
| `calculate_vbmeta_digest` | Calculate expected VBMeta digest |
| `append_vbmeta_image` | Append a VBMeta image to another image |
| `version` | Print avbtool version |
| `calculate_kernel_cmdline` | Calculate kernel cmdline for dm-verity setup |
| `print_partition_digests` | Print per-partition digests from a vbmeta image |
| `set_ab_metadata` | Set A/B metadata on an image |
| `generate_test_image` | Generate a test image with known pattern |
| `make_certificate` (aliases: `make_atx_certificate`) | Create AVB certificate extension cert |
| `make_cert_permanent_attributes` (aliases: `make_atx_permanent_attributes`) | Create permanent attributes |
| `make_cert_metadata` (aliases: `make_atx_metadata`) | Create certificate metadata |
| `make_cert_unlock_credential` (aliases: `make_atx_unlock_credential`) | Create unlock credential |

---

## `add_hash_footer`

Adds a hash descriptor and VBMeta footer to a partition image. The entire image content is hashed (using the specified algorithm and salt) and stored in a hash descriptor inside an appended VBMeta struct. A 64-byte `AVBf` footer is written at the end.

This is used for small partitions that can be fully read and hashed at boot: `boot.img`, `dtbo.img`, `vendor_boot.img`, etc.

```
avbtool add_hash_footer
    --image IMAGE
    --partition_name NAME
    --partition_size SIZE
    [--key KEY]
    [--algorithm ALGORITHM]
    [--hash_algorithm HASH_ALG]
    [--salt SALT]
    [--rollback_index N]
    [--rollback_index_location N]
    [--prop KEY:VALUE]
    [--prop_from_file KEY:PATH]
    [--flags FLAGS]
    [--chain_partition PART:SLOT:KEY]
    [--include_descriptors_from_image IMAGE]
    [--kernel_cmdline CMDLINE]
    [--setup_rootfs_from_kernel IMAGE]
    [--output_vbmeta_image FILE]
    [--do_not_append_vbmeta_image]
    [--use_persistent_digest]
    [--append_to_release_string STR]
    [--signing_helper APP]
    [--signing_helper_with_files APP]
    [--public_key_metadata FILE]
    [--calc_max_image_size]
    [--do_not_use_ab]
    [--print_required_libavb_version]
```

### Key Options

| Option | Description |
|--------|-------------|
| `--image PATH` | Partition image file to modify (modified in-place) |
| `--partition_name NAME` | Logical partition name (e.g. `boot`) |
| `--partition_size SIZE` | Total partition size in bytes. **Required.** Must be >= image size + footer overhead. |
| `--key PATH` | RSA private key PEM file for signing |
| `--algorithm ALGORITHM` | Signing algorithm (default: NONE). See algorithm table in [avb-overview.md](avb-overview.md) |
| `--hash_algorithm HASH` | Hash for the image digest (default: `sha256`) |
| `--rollback_index N` | Rollback index value |
| `--prop KEY:VALUE` | Embed a property in the VBMeta |
| `--flags FLAGS` | VBMeta flags (default: 0) |
| `--chain_partition PART:SLOT:KEY` | Add a chain partition descriptor |

### How It Works

1. Hashes the image content with the chosen algorithm + random salt
2. Builds a hash descriptor containing (partition_name, hash_algorithm, salt, digest)
3. Constructs a VBMeta struct with the descriptor and optional properties
4. Signs the VBMeta struct with the RSA key (if algorithm != NONE)
5. Appends the VBMeta struct to the image
6. Writes a 64-byte `AVBf` footer at the end, pointing to the VBMeta struct
7. Pads the file to `partition_size` with zeros

### Used in vbmeta-generator

Steps 4 and 5 sign `boot.img` and `dtbo.img`:

```bash
avbtool add_hash_footer \
    --image boot.img \
    --partition_name boot \
    --partition_size $SIZE \
    --key avb.key \
    --algorithm SHA256_RSA2048 \
    --rollback_index 1 \
    --prop com.android.build.boot.fingerprint:Xiaomi/bullhead/...
```

---

## `add_hashtree_footer`

Adds a dm-verity hashtree, hashtree descriptor, and VBMeta footer to a filesystem partition image. The hashtree (Merkle tree) is appended after the filesystem data, followed by optional FEC parity data, then the VBMeta struct and footer.

This is used for large filesystem partitions: `system`, `vendor`, `product`, `system_ext`, `odm`, etc.

```
avbtool add_hashtree_footer
    --image IMAGE
    --partition_name NAME
    --partition_size SIZE
    [--key KEY]
    [--algorithm ALGORITHM]
    [--hash_algorithm HASH_ALG]
    [--salt SALT]
    [--block_size SIZE]
    [--rollback_index N]
    [--do_not_generate_fec]
    [--fec_num_roots N]
    [--prop KEY:VALUE]
    [--chain_partition PART:SLOT:KEY]
    [--include_descriptors_from_image IMAGE]
    [--output_vbmeta_image FILE]
    [--do_not_append_vbmeta_image]
    [--no_hashtree]
    [--check_at_most_once]
    [--setup_as_rootfs_from_kernel]
    [--use_persistent_digest]
    [--calc_max_image_size]
    [--do_not_use_ab]
    [--print_required_libavb_version]
```

### Key Options

| Option | Description |
|--------|-------------|
| `--image PATH` | Filesystem image to modify (modified in-place) |
| `--partition_name NAME` | Partition name (e.g. `system`) |
| `--partition_size SIZE` | Total partition size. **Required.** |
| `--hash_algorithm HASH` | Hash algorithm for the Merkle tree (default: `sha1`) |
| `--block_size SIZE` | Filesystem block size (default: 4096) |
| `--do_not_generate_fec` | Skip FEC parity data generation |
| `--fec_num_roots N` | Reed-Solomon roots for FEC (default: 2, range: 2-24) |
| `--no_hashtree` | Only create the descriptor, skip the actual hashtree blob |
| `--check_at_most_once` | Set `CHECK_AT_MOST_ONCE` flag in the hashtree descriptor |
| `--output_vbmeta_image FILE` | Write the VBMeta struct to an external file instead of appending |
| `--do_not_append_vbmeta_image` | Don't append the VBMeta struct to the image |

### How It Works

1. Reads the filesystem data from the image
2. Computes a Merkle hash tree: bottom layer hashes each data block, each subsequent layer hashes blocks of hashes, up to a single root hash
3. Computes FEC parity data using Reed-Solomon RS(255, k) encoding over the data + hashtree
4. Appends the hashtree, FEC data, VBMeta struct, and AVB footer to the image
5. Signs the VBMeta struct with the RSA key

The resulting image layout:

```
+----------------------------+
| Filesystem data            |
+----------------------------+
| Merkle hash tree           |
+----------------------------+
| FEC parity data (optional) |
+----------------------------+
| VBMeta struct              |
|   (hashtree descriptor,    |
|    properties, signature)  |
+----------------------------+
| AVB footer (64 bytes)      |
+----------------------------+
```

The `--do_not_append_vbmeta_image` option is useful when you want to collect the VBMeta struct in a separate file (via `--output_vbmeta_image`) and include it later into a chained vbmeta image via `make_vbmeta_image`'s `--include_descriptors_from_image`.

### Used in vbmeta-generator

Step 6 applies hashtree footers to each system partition. For EROFS images, `--hash_algorithm sha256` is used instead of the default `sha1` for better security.

```bash
avbtool add_hashtree_footer \
    --image system.img \
    --partition_name system \
    --partition_size $SIZE \
    --key avb.key \
    --algorithm SHA256_RSA2048 \
    --hash_algorithm sha256 \
    --rollback_index 1 \
    --prop com.android.build.system.fingerprint:Xiaomi/...
```

---

## `make_vbmeta_image`

Creates a standalone vbmeta image — a VBMeta struct with descriptors but no partition payload. This is used to create `vbmeta.img`, `vbmeta_system.img`, `vbmeta_vendor.img`, and similar.

```
avbtool make_vbmeta_image
    --output FILE
    [--key KEY]
    [--algorithm ALGORITHM]
    [--rollback_index N]
    [--rollback_index_location N]
    [--flags N]
    [--padding_size N]
    [--prop KEY:VALUE]
    [--prop_from_file KEY:PATH]
    [--kernel_cmdline CMDLINE]
    [--setup_rootfs_from_kernel IMAGE]
    [--chain_partition PART:SLOT:KEY_PATH]
    [--chain_partition_do_not_use_ab PART:SLOT:KEY_PATH]
    [--include_descriptors_from_image IMAGE]
    [--append_to_release_string STR]
    [--signing_helper APP]
    [--signing_helper_with_files APP]
    [--public_key_metadata FILE]
    [--set_hashtree_disabled_flag]
    [--print_required_libavb_version]
```

### Key Options

| Option | Description |
|--------|-------------|
| `--output FILE` | Output vbmeta image path |
| `--key KEY` | RSA private key for signing |
| `--algorithm ALGORITHM` | Signing algorithm |
| `--rollback_index N` | Rollback index (default: 0) |
| `--flags N` | VBMeta flags |
| `--padding_size N` | Pad output to multiple of N bytes |
| `--chain_partition PART:SLOT:KEY` | Chain partition descriptor: partition_name:rollback_location:public_key_path |
| `--include_descriptors_from_image IMAGE` | Copy all descriptors from an image that has a VBMeta struct (hashtree footer images, vbmeta images) |
| `--prop KEY:VALUE` | Add a property |

### How It Works

1. Creates an empty VBMeta header
2. For each `--include_descriptors_from_image`: reads the image's VBMeta struct (either the top-level for vbmeta images or the footer VBMeta for partition images) and copies all descriptors
3. Adds all `--chain_partition` and `--prop` entries as new descriptors
4. Signs the accumulated VBMeta struct with the RSA key
5. Writes the output file, padded to `--padding_size`

### Used in vbmeta-generator

**vbmeta_system.img** (Step 8): includes descriptors from system, system_ext, product partitions:

```bash
avbtool make_vbmeta_image \
    --output vbmeta_system.img \
    --key avb.key \
    --algorithm SHA256_RSA2048 \
    --rollback_index 1 \
    --flags 0 \
    --padding_size 4096 \
    --include_descriptors_from_image system.img \
    --include_descriptors_from_image system_ext.img \
    --include_descriptors_from_image product.img
```

**vbmeta_vendor.img** (Step 9): includes descriptors from the vendor partition.

**vbmeta.img** (Step 10): includes chain descriptors for boot, vbmeta_system, vbmeta_vendor, plus descriptors from dtbo.img:

```bash
avbtool make_vbmeta_image \
    --output vbmeta.img \
    --key avb.key \
    --algorithm SHA256_RSA2048 \
    --rollback_index 0 \
    --flags 1 \
    --padding_size 4096 \
    --chain_partition boot:1:avb.avbpubkey \
    --chain_partition vbmeta_system:2:avb.avbpubkey \
    --chain_partition vbmeta_vendor:3:avb.avbpubkey \
    --include_descriptors_from_image dtbo.img
```

---

## `info_image`

Displays information about a vbmeta image or any image with an AVB footer. This is the primary tool for inspecting AVB metadata.

```
avbtool info_image --image IMAGE [--output FILE] [--cert]
```

### Example Output

```
Footer version: 1.0
Image size: 402653184 bytes
Original image size: 399507456
vbmeta offset: 400048128
vbmeta size: 1216 bytes

Minimum libavb version: 1.0

Header block: 256 bytes
Authentication block: 320 bytes
Auxiliary block: 640 bytes

Algorithm: SHA256_RSA2048
Rollback Index: 1
Flags: 0
Rollback Index Location: 0
Release String: avbtool 1.3.0

Descriptors:
    Hashtree descriptor:
      Version: dm-verity 1.0
      Image Size: 399507456 bytes
      Tree Offset: 399507456
      Tree Size: 305152 bytes
      Data Block Size: 4096 bytes
      Hash Block Size: 4096 bytes
      FEC num roots: 2
      FEC offset: 399812608
      FEC size: 540672 bytes
      Hash Algorithm: sha1
      Partition Name: system
      Salt: a1b2c3d4...
      Root Digest: e5f6g7h8...
      Flags: 0

    Prop: com.android.build.system.fingerprint -> Xiaomi/...
    Prop: com.android.build.system.os_version -> 14
    Prop: com.android.build.system.security_patch -> 2024-01-05
```

### Used in vbmeta-generator

`info_image` is called by `detect_avb()` in `image.py` to detect existing AVB footers on images and extract algorithm, rollback index, flags, and descriptors. Also used by `avb.get_image_size()` to read the `Image size` field.

---

## `verify_image`

Verifies an AVB image. Checks the VBMeta signature and optionally validates descriptors against the referenced partitions.

```
avbtool verify_image
    --image IMAGE
    [--key KEY]
    [--expected_chain_partition PART:SLOT:KEY_PATH]
    [--follow_chain_partitions]
    [--accept_zeroed_hashtree]
```

| Option | Description |
|--------|-------------|
| `--key KEY` | Check that the embedded public key matches the given key |
| `--expected_chain_partition` | Specify expected chain partition key |
| `--follow_chain_partitions` | Follow chain partitions to verify recursively |
| `--accept_zeroed_hashtree` | Allow zeroed-out hashtrees (useful for adb remount) |

---

## `extract_public_key`

Extracts the AVB-formatted public key from a PEM private key file. The output is a binary file containing the public key in the format expected by AVB descriptors (e.g. chain partition descriptors).

```
avbtool extract_public_key --key KEY --output OUTPUT
```

### Used in vbmeta-generator

Called in Step 1 after key generation to produce `avb.avbpubkey`, which is then referenced in chain partition descriptors.

---

## `extract_vbmeta_image`

Extracts the embedded VBMeta struct from an image with an AVB footer. The output is a standalone vbmeta image containing only the VBMeta struct.

```
avbtool extract_vbmeta_image
    --image IMAGE
    [--output FILE]
    [--padding_size N]
```

---

## `erase_footer`

Removes the AVB footer from an image. This restores the image to its original size (before padding).

```
avbtool erase_footer --image IMAGE [--keep_hashtree]
```

With `--keep_hashtree`, the hashtree and FEC data are preserved even though the footer/VBMeta is removed. This is useful when re-signing.

---

## `zero_hashtree`

Zeroes out the hashtree and FEC data in an image that has a VBMeta footer. The resulting image will have no hashtree or FEC, but the VBMeta struct and footer remain.

```
avbtool zero_hashtree --image IMAGE
```

This is equivalent to what happens when `AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED` is in effect — the hashtree is zeroed so the kernel has nothing to check.

---

## `resize_image`

Resizes an image that has an AVB footer to a new partition size. The VBMeta struct and footer are moved to the new end of the partition.

```
avbtool resize_image --image IMAGE --partition_size SIZE
```

---

## `calculate_vbmeta_digest`

Calculates the expected VBMeta digest for a given image. This is the hash that the bootloader would calculate when verifying the image.

```
avbtool calculate_vbmeta_digest
    --image IMAGE
    [--hash_algorithm HASH_ALG]
    [--output FILE]
```

---

## `extract_public_key_digest`

Extracts the SHA-1 digest of the AVB public key. This produces a hex string that uniquely identifies the key, useful for display and logging.

```
avbtool extract_public_key_digest --key KEY --output FILE
```

### Used in vbmeta-generator

Called in Step 1 to display the SHA-1 fingerprint of the generated signing key:

```
SHA1: b0f60eaada29e27849d3cb390ab5340849fa777ffaa50b9bc72f35a9ddb7c3eb
```

---

## `calculate_kernel_cmdline`

Calculates the kernel command-line parameters needed for dm-verity setup based on descriptors in a vbmeta image.

```
avbtool calculate_kernel_cmdline --image IMAGE
```

---

## `print_partition_digests`

Prints the digest of each partition referenced by descriptors in a vbmeta image.

```
avbtool print_partition_digests --image IMAGE
```

---

## `version`

Prints the avbtool version.

```
avbtool version
```

Output: `avbtool 1.3.0`

---

## ATX Certificate Subcommands

AVB supports Android Things (ATX) certificate extensions for device-specific signing. These subcommands are rarely used outside of ATX:

- `make_certificate` / `make_atx_certificate`
- `make_cert_permanent_attributes` / `make_atx_permanent_attributes`
- `make_cert_metadata` / `make_atx_metadata`
- `make_cert_unlock_credential` / `make_atx_unlock_credential`

---

## Common Patterns

### Re-sign an Image with a Different Key

```bash
# Extract existing VBMeta
avbtool extract_vbmeta_image --image signed.img --output vbmeta_only.img

# Erase footer (keep hashtree)
avbtool erase_footer --image signed.img --keep_hashtree

# Re-sign with new key
avbtool add_hashtree_footer \
    --image signed.img \
    --partition_name system \
    --partition_size $(stat -c%s signed.img) \
    --key new_key.pem \
    --algorithm SHA256_RSA2048 \
    --rollback_index 1
```

### Check Whether an Image Has AVB

```bash
avbtool info_image --image boot.img 2>&1 | grep -q "Footer version" && echo "AVB present"
```

### Get Root Digest from a Hashtree Partition

```bash
avbtool info_image --image system.img | grep "Root Digest" | awk '{print $3}'
```
