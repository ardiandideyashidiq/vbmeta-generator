# Android Verified Boot (AVB) & vbmeta — Complete Guide

## Table of Contents

1. [What is vbmeta?](#1-what-is-vbmeta)
2. [Architecture Overview](#2-architecture-overview)
3. [BoardConfig Variables Reference](#3-boardconfig-variables-reference)
4. [Phase 1: Signing Individual Partitions](#4-phase-1-signing-individual-partitions)
5. [Phase 2: Building the vbmeta Images](#5-phase-2-building-the-vbmeta-images)
6. [Chained Partitions Deep Dive](#6-chained-partitions-deep-dive)
7. [Descriptor Chain Explained](#7-descriptor-chain-explained)
8. [AVB Key Management](#8-avb-key-management)
9. [dm-verity Integration](#9-dm-verity-integration)
10. [FEC (Forward Error Correction)](#10-fec-forward-error-correction)
11. [Eng Build vs User Build](#11-eng-build-vs-user-build)
12. [Target Files Packaging & OTA](#12-target-files-packaging--ota)
13. [Troubleshooting](#13-troubleshooting)
14. [Reference: Source File Map](#14-reference-source-file-map)

---

## 1. What is vbmeta?

The **vbmeta image** is the root of trust for Android Verified Boot 2.0 (AVB). It is a small (64 KB) partition that contains:

- Cryptographic **descriptors** (hash or hashtree) for each verified partition
- **Chain partition descriptors** that delegate verification of a partition to another vbmeta image
- **Property descriptors** (OS version, security patch level, system fingerprint)
- An **RSA signature** over all of the above, signed with the device's private key

At boot, the bootloader:
1. Reads `vbmeta.img` from the `vbmeta` partition
2. Verifies the RSA signature using a built-in public key
3. Follows `ChainPartitionDescriptor`s to recursively verify chained vbmeta images
4. Uses `HashDescriptor`s and `HashtreeDescriptor`s to verify each partition

If any step fails, the device boots with an orange/warning state or doesn't boot at all,
depending on the lock state.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    vbmeta.img (64 KB)                       │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ AVB Header (algorithm, rollback_index, flags, etc.)   │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │ Auth Data (SHA256 hash + RSA signature)               │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │ Auxiliary Data:                                       │  │
│  │                                                       │  │
│  │  HashDescriptor: boot       ← sha256(salt || image)  │  │
│  │  HashDescriptor: init_boot                            │  │
│  │  HashDescriptor: vendor_boot                          │  │
│  │  HashDescriptor: vendor_kernel_boot                   │  │
│  │  HashDescriptor: dtbo                                 │  │
│  │  HashtreeDescriptor: system  ← dm-verity root hash   │  │
│  │  HashtreeDescriptor: vendor                           │  │
│  │  HashtreeDescriptor: product                          │  │
│  │  HashtreeDescriptor: system_ext                       │  │
│  │  HashtreeDescriptor: odm                              │  │
│  │  HashtreeDescriptor: vendor_dlkm                      │  │
│  │  HashtreeDescriptor: odm_dlkm                         │  │
│  │  HashtreeDescriptor: system_dlkm                      │  │
│  │  ChainPartitionDescriptor: vbmeta_system              │  │
│  │  ChainPartitionDescriptor: vbmeta_vendor              │  │
│  │  PropertyDescriptors (os_version, fingerprint, ...)   │  │
│  │  Public Key (embedded for bootloader verification)    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │
          ├── ChainPartitionDescriptor: vbmeta_system
          │     │
          │     ▼
          │   ┌──────────────────────────────────────────────┐
          │   │          vbmeta_system.img                   │
          │   │  HashtreeDescriptor: system                  │
          │   │  HashtreeDescriptor: system_ext              │
          │   └──────────────────────────────────────────────┘
          │
          └── ChainPartitionDescriptor: vbmeta_vendor
                │
                ▼
              ┌──────────────────────────────────────────────┐
              │          vbmeta_vendor.img                   │
              │  HashtreeDescriptor: vendor                  │
              │  HashtreeDescriptor: odm                     │
              │  HashtreeDescriptor: vendor_dlkm             │
              │  HashtreeDescriptor: odm_dlkm                │
              └──────────────────────────────────────────────┘
```

---

## 3. BoardConfig Variables Reference

### 3.1 Master Toggle

```makefile
# Enable AVB 2.0 (Android Verified Boot)
BOARD_AVB_ENABLE := true
```

When set, the build system produces `vbmeta.img` and signs all supported partitions.

### 3.2 Global Signing Configuration

| Variable | Default | Description |
|---|---|---|
| `BOARD_AVB_KEY_PATH` | `external/avb/test/data/testkey_rsa4096.pem` | Path to RSA private key |
| `BOARD_AVB_ALGORITHM` | `SHA256_RSA4096` | Signing algorithm |
| `BOARD_AVB_ROLLBACK_INDEX` | `PLATFORM_SECURITY_PATCH_TIMESTAMP` | Rollback protection value |
| `BOARD_AVB_ROLLBACK_INDEX_LOCATION` | 0 | Slot index for rollback (0 = main) |

### 3.3 Per-Partition Configuration

For any partition `<PART>` (boot, system, vendor, product, etc.):

| Variable | Description |
|---|---|
| `BOARD_AVB_<PART>_KEY_PATH` | Per-partition key (makes it a **chained partition**) |
| `BOARD_AVB_<PART>_ALGORITHM` | Per-partition algorithm |
| `BOARD_AVB_<PART>_ROLLBACK_INDEX` | Per-partition rollback value |
| `BOARD_AVB_<PART>_ROLLBACK_INDEX_LOCATION` | Unique slot number (≥1) |
| `BOARD_AVB_<PART>_ADD_HASH_FOOTER_ARGS` | Extra avbtool flags for hash footer |
| `BOARD_AVB_<PART>_ADD_HASHTREE_FOOTER_ARGS` | Extra avbtool flags for hashtree footer |

### 3.4 Chained vbmeta Grouping

| Variable | Description |
|---|---|
| `BOARD_AVB_VBMETA_SYSTEM` | Space-separated list: e.g. `system system_ext` |
| `BOARD_AVB_VBMETA_VENDOR` | Space-separated list: e.g. `vendor odm vendor_dlkm odm_dlkm` |
| `BOARD_AVB_VBMETA_CUSTOM_PARTITIONS` | Additional custom vbmeta group names |

### 3.5 Build Flags

| Variable | Description |
|---|---|
| `BOARD_AVB_MAKE_VBMETA_IMAGE_ARGS` | Extra flags for `make_vbmeta_image` |
| `BOARD_AVB_BOOT_ADD_HASH_FOOTER_ARGS` | Extra flags for boot hash footer |
| `BOARD_AVB_SYSTEM_ADD_HASHTREE_FOOTER_ARGS` | Extra flags for system hashtree footer |

### 3.6 Complete BoardConfig Example

```makefile
# File: device/<oem>/<device>/BoardConfig.mk

# Enable AVB
BOARD_AVB_ENABLE := true

# Global signing (produces vbmeta.img)
BOARD_AVB_KEY_PATH := device/$(OEM)/$(DEVICE)/security/avb.pem
BOARD_AVB_ALGORITHM := SHA256_RSA4096
BOARD_AVB_ROLLBACK_INDEX := $(PLATFORM_SECURITY_PATCH_TIMESTAMP)
BOARD_AVB_ROLLBACK_INDEX_LOCATION := 0

# Chained partition: boot uses its own key
BOARD_AVB_BOOT_KEY_PATH := device/$(OEM)/$(DEVICE)/security/boot.pem
BOARD_AVB_BOOT_ALGORITHM := SHA256_RSA4096
BOARD_AVB_BOOT_ROLLBACK_INDEX := 1
BOARD_AVB_BOOT_ROLLBACK_INDEX_LOCATION := 1

# Group system partitions into vbmeta_system
BOARD_AVB_VBMETA_SYSTEM := system system_ext

# Group vendor partitions into vbmeta_vendor
BOARD_AVB_VBMETA_VENDOR := vendor odm vendor_dlkm odm_dlkm

# Chained vbmeta_system uses its own key
BOARD_AVB_VBMETA_SYSTEM_KEY_PATH := device/$(OEM)/$(DEVICE)/security/system.pem
BOARD_AVB_VBMETA_SYSTEM_ALGORITHM := SHA256_RSA4096
BOARD_AVB_VBMETA_SYSTEM_ROLLBACK_INDEX := 2
BOARD_AVB_VBMETA_SYSTEM_ROLLBACK_INDEX_LOCATION := 2

# Chained vbmeta_vendor uses its own key
BOARD_AVB_VBMETA_VENDOR_KEY_PATH := device/$(OEM)/$(DEVICE)/security/vendor.pem
BOARD_AVB_VBMETA_VENDOR_ALGORITHM := SHA256_RSA4096
BOARD_AVB_VBMETA_VENDOR_ROLLBACK_INDEX := 3
BOARD_AVB_VBMETA_VENDOR_ROLLBACK_INDEX_LOCATION := 3

# Optional extra args
BOARD_AVB_MAKE_VBMETA_IMAGE_ARGS += --prop com.android.build.fingerprint:$(BUILD_FINGERPRINT)
```

### 3.7 Minimal BoardConfig (test keys, no chaining)

```makefile
BOARD_AVB_ENABLE := true
# Uses default test key at external/avb/test/data/testkey_rsa4096.pem
# Partitions are included directly (not chained)
```

---

## 4. Phase 1: Signing Individual Partitions

Before vbmeta can reference any partition, that partition must have an AVB footer
appended. There are two types depending on the partition type.

### 4.1 Hash Footer (boot images)

Used for small, fixed-content partitions: **boot, init_boot, vendor_boot,
vendor_kernel_boot, dtbo, pvmfw, recovery**.

**Make invocation** (`android_build/core/Makefile`):

```makefile
$(AVBTOOL) add_hash_footer \
    --image $(target-image) \
    --partition_name boot \
    --key $(BOARD_AVB_BOOT_KEY_PATH) \
    --algorithm $(BOARD_AVB_BOOT_ALGORITHM) \
    --salt $(salt) \
    --rollback_index $(BOARD_AVB_BOOT_ROLLBACK_INDEX) \
    --prop com.android.build.boot.os_version:$(PLATFORM_VERSION) \
    --prop com.android.build.boot.fingerprint:$(BUILD_FINGERPRINT) \
    $(BOARD_AVB_BOOT_ADD_HASH_FOOTER_ARGS)
```

**Soong equivalent** (`android_build_soong/filesystem/bootimg.go`):

The `addAvbFooter()` method constructs the same avbtool command. It appends the
hash footer to the boot image by: (1) copying the image, (2) calling avbtool,
(3) replacing the original.

**What it does internally** (`android_external_avb/avbtool.py`):

1. Read the image file
2. Compute `hash = SHA256(salt || image_data)`
3. Create an `AvbHashDescriptor` with the digest, salt, algorithm, image_size
4. Encode the descriptor into a vbmeta blob
5. Sign the vbmeta blob with the RSA key
6. Append `vbmeta_blob + AvbFooter` to the end of the image

The resulting partition layout:

```
┌─────────────────┐
│   boot.img      │  ← original image data
├─────────────────┤
│  vbmeta blob    │  ← hash descriptor + signature
├─────────────────┤
│  AvbFooter      │  ← points to vbmeta offset, magic "AVBf"
└─────────────────┘
```

### 4.2 Hashtree Footer (filesystem images)

Used for large, writable filesystem partitions: **system, vendor, product,
system_ext, odm, vendor_dlkm, odm_dlkm, system_dlkm**.

**Make invocation** (via `build_image.py`):

When `avb_hashtree_enable=true` is set in the image properties dict,
`build_image.py` calls:

```bash
avbtool add_hashtree_footer \
    --image $(image) \
    --partition_name system \
    --key $(BOARD_AVB_SYSTEM_KEY_PATH) \
    --algorithm $(BOARD_AVB_SYSTEM_ALGORITHM) \
    --hash_algorithm sha256 \
    --salt $(salt) \
    --rollback_index $(BOARD_AVB_SYSTEM_ROLLBACK_INDEX) \
    --prop com.android.build.system.os_version:$(PLATFORM_VERSION) \
    --prop com.android.build.system.fingerprint:$(BUILD_FINGERPRINT) \
    $(BOARD_AVB_SYSTEM_ADD_HASHTREE_FOOTER_ARGS)
```

**Soong equivalent** (`android_build_soong/filesystem/filesystem.go`):

When `use_avb: true` is in the filesystem module properties, it sets:
- `avb_hashtree_enable=true`
- `avb_avbtool`, `avb_algorithm`, `avb_key_path`
- `avb_add_hashtree_footer_args` built from `getAvbAddHashtreeFooterArgs()`

**What it does internally** (`avbtool.py`):

1. Calculate dm-verity hash tree levels from the image data
2. Create an `AvbHashtreeDescriptor` (tag=1) with tree offset/size, root digest
3. Optionally generate FEC (Forward Error Correction) Reed-Solomon data
4. Encode and sign the vbmeta blob
5. Append hash tree + FEC data + vbmeta blob + footer to the image

Resulting partition layout:

```
┌────────────────────┐
│   system.img       │  ← original filesystem data
├────────────────────┤
│  dm-verity hash    │  ← multi-level hash tree
│  tree              │
├────────────────────┤
│  FEC data          │  ← optional Reed-Solomon parity
│  (optional)        │
├────────────────────┤
│  vbmeta blob       │  ← hashtree descriptor + signature
├────────────────────┤
│  AvbFooter         │  ← points to vbmeta offset
└────────────────────┘
```

### 4.3 Which partitions get which footer type

| Partition | Footer Type | Notes |
|---|---|---|
| `boot` | hash | Fixed-size, small |
| `init_boot` | hash | GKI boot config |
| `vendor_boot` | hash | Vendor ramdisk |
| `vendor_kernel_boot` | hash | GKI vendor kernel |
| `dtbo` | hash | Device tree blob overlay |
| `pvmfw` | hash | Protected VM firmware |
| `recovery` | hash | Recovery ramdisk |
| `system` | hashtree | Large writable FS |
| `vendor` | hashtree | Large writable FS |
| `product` | hashtree | Large writable FS |
| `system_ext` | hashtree | System extensions |
| `odm` | hashtree | OEM-specific |
| `vendor_dlkm` | hashtree | Vendor DLKM |
| `odm_dlkm` | hashtree | ODM DLKM |
| `system_dlkm` | hashtree | System DLKM |

---

## 5. Phase 2: Building the vbmeta Images

### 5.1 Chained vbmeta Images

Chained vbmeta images group related partitions under a delegated signing key.
The standard ones are `vbmeta_system.img` and `vbmeta_vendor.img`.

**Make invocation** (`android_build/core/Makefile`):

```makefile
# For vbmeta_system:
$(AVBTOOL) make_vbmeta_image \
    --algorithm SHA256_RSA4096 \
    --key device/.../system.pem \
    --padding_size 4096 \
    --rollback_index 2 \
    --rollback_index_location 2 \
    $(if $(filter eng,$(TARGET_BUILD_VARIANT)),--set_hashtree_disabled_flag) \
    --include_descriptors_from_image $(PRODUCT_OUT)/system.img \
    --include_descriptors_from_image $(PRODUCT_OUT)/system_ext.img \
    --output $(PRODUCT_OUT)/vbmeta_system.img && \
truncate -s 65536 $(PRODUCT_OUT)/vbmeta_system.img
```

**Soong equivalent** (`android_build_soong/fsgen/vbmeta_partitions.go`):

Creates a `vbmeta` Soong module for each chained group (iterating
`ChainedVbmetaPartitions` from the JSON config). The module factory is in
`android_build_soong/filesystem/vbmeta.go`.

### 5.2 Top-Level vbmeta.img

This is the root of trust. It contains or chains to all other partitions.

**Make invocation** (`android_build/core/Makefile`, ~line 5242-5301):

```makefile
$(AVBTOOL) make_vbmeta_image \
    --algorithm SHA256_RSA4096 \
    --key $(BOARD_AVB_KEY_PATH) \
    --padding_size 4096 \
    --rollback_index $(BOARD_AVB_ROLLBACK_INDEX) \
    --rollback_index_location 0 \
    $(if $(filter eng,$(TARGET_BUILD_VARIANT)),--set_hashtree_disabled_flag) \
    $(foreach img,$(DIRECTLY_INCLUDED_IMAGES), \
        --include_descriptors_from_image $(img)) \
    $(foreach chain,$(CHAINED_PARTITIONS), \
        --chain_partition $(chain):$(ril):$(avbpubkey_path)) \
    --prop com.android.build.os_version:$(PLATFORM_VERSION) \
    --prop com.android.build.fingerprint:$(BUILD_FINGERPRINT) \
    --prop com.android.build.security_patch:$(PLATFORM_SECURITY_PATCH) \
    --output $(PRODUCT_OUT)/vbmeta.img && \
truncate -s 65536 $(PRODUCT_OUT)/vbmeta.img
```

**Soong equivalent** (`android_build_soong/filesystem/vbmeta.go`):

`GenerateAndroidBuildActions()` constructs the same command. Key details:

- **Partition ordering** (for bit-identical output): `boot, init_boot, vendor_boot, vendor_kernel_boot, system, vendor, product, system_ext, odm, vendor_dlkm, odm_dlkm, system_dlkm, dtbo, pvmfw, recovery, vbmeta_system, vbmeta_vendor`
- **Eng builds**: `--set_hashtree_disabled_flag` is added
- **Output**: Always truncated to exactly 64 KB

### 5.3 How `make_vbmeta_image` Works Internally

In `android_external_avb/avbtool.py` (`_generate_vbmeta_blob()`):

1. **Parse `--chain_partition` args**: Create `AvbChainPartitionDescriptor` objects
   with rollback_index_location and public key bytes (tag=4)
2. **Parse `--prop` args**: Create `AvbPropertyDescriptor` objects (tag=0)
3. **Parse `--kernel_cmdline` args**: Create `AvbKernelCmdlineDescriptor` objects (tag=3)
4. **Parse `--include_descriptors_from_image`**: For each image, read its appended
   vbmeta blob via `_parse_image()`, extract all descriptors from it
5. **Build `AvbVBMetaHeader`**: Contains algorithm info, rollback_index, flags
6. **Encode auxiliary data**: All descriptors + public key + key metadata
7. **Hash**: `digest = SHA256(header || aux_data)`
8. **Sign**: RSA sign the digest with the private key
9. **Produce final blob**: `header || auth_data (hash + signature) || aux_data (descriptors + key)`

### 5.4 Build Time Variables Flow

```
BoardConfig.mk
    │
    ▼
build/core/config.mk             ← AVBTOOL path resolution
    │
    ▼
build/core/soong_config.mk       ← BOARD_AVB_* → JSON for Soong
    │
    ├─► build/core/Makefile      ← Make-based vbmeta build
    │     (lines 4700-5300)
    │
    └─► Soong (go files)
          │
          ├─ android/variable.go  ← PartitionsVariables struct
          ├─ fsgen/vbmeta_partitions.go ← creates vbmeta Soong modules
          └─ filesystem/vbmeta.go ← GenerateAndroidBuildActions()
```

---

## 6. Chained Partitions Deep Dive

### 6.1 What is a chained partition?

A **chained partition** is a partition that has its **own signing key**, separate
from the top-level vbmeta key. Instead of including the partition's descriptor
directly in `vbmeta.img`, the top-level vbmeta contains a
`ChainPartitionDescriptor` that embeds the partition's public key. At boot,
the bootloader:

1. Reads the chain descriptor from `vbmeta.img`
2. Locates the partition on disk
3. Reads that partition's vbmeta footer
4. Verifies it using the public key from the chain descriptor

### 6.2 When to chain

| Scenario | Chained? | Why |
|---|---|---|
| Same key for all partitions | No | All descriptors go directly into vbmeta.img |
| OEM uses different key for boot | Yes | Boot can be updated independently |
| Vendor uses their own key | Yes | Vendor separation |
| System uses Google key | Yes | GSI compatibility |

### 6.3 Decision logic

In `android_build_soong/fsgen/vbmeta_partitions.go` (lines 252-270):

```go
for _, partitionType := range avbPartitions {
    if hasPerPartitionKey(partitionType) {
        // Mode B: add --chain_partition
        chainedPartitions = append(chainedPartitions, name)
    } else {
        // Mode A: add --include_descriptors_from_image
        includePartitions = append(includePartitions, name)
    }
}
```

### 6.4 Key extraction for chained partitions

When a partition has its own key, the build extracts the public key:

**Make** (`android_build/core/Makefile`):

```makefile
$(AVBTOOL) extract_public_key \
    --key $(BOARD_AVB_$(PART)_KEY_PATH) \
    --output $(avbpubkey_path)
```

**Soong** (`android_build_soong/filesystem/vbmeta.go`):

```go
extractPublicKeyRule = ruleBuilder.
    Command("avbtool extract_public_key --key $in --output $out")
```

The `.avbpubkey` file is then passed to `--chain_partition`:

```
--chain_partition boot:1:path/to/boot.avbpubkey
```

---

## 7. Descriptor Chain Explained

### 7.1 Descriptor Types

| Tag | Class | Purpose |
|---|---|---|
| 0 | `AvbPropertyDescriptor` | Key-value properties (version, fingerprint) |
| 1 | `AvbHashtreeDescriptor` | dm-verity hash tree root |
| 2 | `AvbHashDescriptor` | Image hash (for boot partitions) |
| 3 | `AvbKernelCmdlineDescriptor` | Kernel cmdline parameters |
| 4 | `AvbChainPartitionDescriptor` | Delegated partition verification |

### 7.2 AvbHashDescriptor (Tag=2)

Used for boot images. Contains:

| Field | Description |
|---|---|
| `image_size` | Size of the partition image |
| `hash_algorithm` | Always `sha256` |
| `partition_name` | e.g. `boot` |
| `salt` | Random salt for hash computation |
| `digest` | `SHA256(salt || image_data)` |
| `flags` | Reserved |

### 7.3 AvbHashtreeDescriptor (Tag=1)

Used for filesystem images. Contains:

| Field | Description |
|---|---|
| `dm_verity_version` | Always 1 |
| `image_size` | Size of the partition image |
| `tree_offset` | Offset of hash tree from image start |
| `tree_size` | Size of the hash tree |
| `data_block_size` | Typically 4096 |
| `hash_block_size` | Typically 4096 |
| `fec_num_roots` | FEC parity roots (0 = no FEC) |
| `fec_offset` | Offset of FEC data |
| `fec_size` | Size of FEC data |
| `hash_algorithm` | Always `sha256` |
| `partition_name` | e.g. `system` |
| `salt` | Random salt |
| `root_digest` | Root hash of the dm-verity tree |
| `flags` | Reserved |

### 7.4 AvbChainPartitionDescriptor (Tag=4)

| Field | Description |
|---|---|
| `partition_name` | e.g. `vbmeta_system` |
| `rollback_index_location` | Unique slot identifier |
| `public_key` | The public key (in AVB format) |

### 7.5 Complete tree example

```
vbmeta.img (key: device_key)
├── HashDescriptor: boot
│     digest=SHA256(salt_boot || boot.img)
├── HashDescriptor: dtbo
│     digest=SHA256(salt_dtbo || dtbo.img)
├── ChainPartitionDescriptor: vbmeta_system
│     rollback_index_location=2
│     public_key=<system_pubkey>
│
└── ChainPartitionDescriptor: vbmeta_vendor
      rollback_index_location=3
      public_key=<vendor_pubkey>

vbmeta_system.img (key: system_key)
├── HashtreeDescriptor: system
│     root_digest=SHA256(...hash_tree_root...)
│     tree_offset, tree_size, salt, ...
└── HashtreeDescriptor: system_ext
      root_digest=SHA256(...hash_tree_root...)

vbmeta_vendor.img (key: vendor_key)
├── HashtreeDescriptor: vendor
├── HashtreeDescriptor: odm
├── HashtreeDescriptor: vendor_dlkm
└── HashtreeDescriptor: odm_dlkm
```

---

## 8. AVB Key Management

### 8.1 Key Generation

Generate an RSA key pair for AVB:

```bash
# Generate RSA 4096 private key
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 \
    -out avb.pem

# Extract public key in AVB format
avbtool extract_public_key --key avb.pem --output avb.avbpubkey

# View key metadata
avbtool info --key avb.pem
```

### 8.2 Test Keys

Location: `external/avb/test/data/`

| File | Algorithm |
|---|---|
| `testkey_rsa2048.pem` | SHA256_RSA2048 |
| `testkey_rsa4096.pem` | SHA256_RSA4096 (default) |
| `testkey_rsa8192.pem` | SHA256_RSA8192 |
| `testkey_ecdsa256.pem` | SHA256_ECDSA256 |
| `testkey_ecdsa384.pem` | SHA256_ECDSA384 |

**Never use test keys for production devices.** Test keys are well-known and do
not provide any security. Anyone with the test public key can sign vbmeta images.

### 8.3 Production Key Storage

Production keys MUST be kept outside the build tree, often on a dedicated
signing server or HSM. The build extract_public_key step runs on the server,
and only the `.avbpubkey` file enters the build tree.

Recommended approach:

```makefile
# The .pem private key is NEVER committed to the source tree
# Instead, commit only the .avbpubkey and sign externally
BOARD_AVB_KEY_PATH := device/$(OEM)/$(DEVICE)/security/avb.pem
# ^^ This is overridden by the signing server build script
```

Signing server flow:

```bash
# 1. Build unsigned images
make target-files-package

# 2. On signing server:
./build/tools/releasetools/sign_target_files_apks \
    --key_mapping device_key=path/to/production_key.pem \
    $OUT/obj/PACKAGING/target_files_intermediates/*-target_files-*.zip \
    signed-target_files.zip
```

### 8.4 Multiple Key Architecture

For devices with multiple stakeholders (SoC vendor, OEM, carrier):

```
vbmeta.img          ← signed by OEM key
├── vbmeta_system   ← signed by Google key (for GSI)
├── vbmeta_vendor   ← signed by SoC vendor key
│   └── odm         ← signed by carrier key
└── boot            ← signed by SoC vendor key (for GKI)
```

This allows each party to sign their own partition independently.

---

## 9. dm-verity Integration

### 9.1 Relationship between AVB and dm-verity

AVB and dm-verity work together but are distinct:

- **AVB (vbmeta)**: Verifies at boot that the partition hasn't been tampered with
- **dm-verity (kernel)**: Verifies individual blocks on access, enabling
  read-only verified mounts

The bridge is the `AvbHashtreeDescriptor`: AVB stores the dm-verity root hash
and tree metadata in the descriptor; the bootloader or kernel reads this to
configure dm-verity.

### 9.2 Kernel Cmdline Generation

`avbtool` generates kernel cmdline descriptors for hashtree partitions
(`_get_cmdline_descriptors_for_hashtree_descriptor()` in avbtool.py):

```
dm="1" root=/dev/dm-0
dm.name=<partition> dm.devices=/dev/block/by-name/<partition>
dm.flags=verity
dm.verity.hash=<hashtree_offset>,<hashtree_size>
```

These are stored as `AvbKernelCmdlineDescriptor` (Tag=3) in the vbmeta blob.

### 9.3 Disabling dm-verity (eng builds)

When `--set_hashtree_disabled_flag` is passed, the vbmeta header has bit 0 set:
`AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED`. The bootloader checks this flag
and skips hashtree verification, but AVB header verification still occurs
(unless verity is also disabled at boot via the bootloader menu).

---

## 10. FEC (Forward Error Correction)

### 10.1 What FEC does

FEC uses Reed-Solomon error correction to recover from bit rot or partial data
corruption on flash storage. It allows the device to boot even if a few blocks
are damaged.

### 10.2 FEC in the build

FEC is generated by `add_hashtree_footer` in avbtool:

```python
# avbtool.py, ~line 3856
if not do_not_generate_fec:
    fec_data = generate_fec_data(image_data, fec_num_roots)
    # Append FEC data to image
    # Set fec_offset, fec_size, fec_num_roots in descriptor
```

### 10.3 Controlling FEC

```makefile
# Disable FEC for a specific partition
BOARD_AVB_SYSTEM_ADD_HASHTREE_FOOTER_ARGS += --do_not_generate_fec

# Or globally via make_vbmeta_image args (FEC is per-partition in hashtree footer)
```

### 10.4 The `fec/` library

The `fec/` directory contains `libfec`, a userspace implementation of
Reed-Solomon decoding used by `fs_mgr` to read from damaged partitions.
It also has a `fec` command-line tool for encoding/decoding.

---

## 11. Eng Build vs User Build

### 11.1 Behavior differences

| Feature | Eng | Userdebug | User |
|---|---|---|---|
| `--set_hashtree_disabled_flag` | Yes | No | No |
| dm-verity enforcement | Disabled | Enabled | Enabled |
| AVB verification | Still happens | Full | Full |
| Boot warning | Orange state | Orange state | Red=bad, Green=OK |

### 11.2 Code paths

Make (`android_build/core/Makefile`, line 5084):

```makefile
ifeq (eng,$(TARGET_BUILD_VARIANT))
    BOARD_AVB_MAKE_VBMETA_IMAGE_ARGS += --set_hashtree_disabled_flag
endif
```

Soong (`android_build_soong/filesystem/vbmeta.go`, lines 230-233):

```go
if ctx.Config().IsEng() {
    flags += " --set_hashtree_disabled_flag"
}
```

### 11.3 How to handle in custom ROMs

For custom ROM development, you typically want:

```makefile
# In your device BoardConfig.mk:
ifeq (eng,$(TARGET_BUILD_VARIANT))
    # Keep dm-verity disabled for faster flashing/testing
    BOARD_AVB_MAKE_VBMETA_IMAGE_ARGS += --set_hashtree_disabled_flag
else
    # Ensure full verification in user builds
    BOARD_AVB_MAKE_VBMETA_IMAGE_ARGS += \
        --prop com.android.build.security_patch:$(PLATFORM_SECURITY_PATCH)
endif
```

---

## 12. Target Files Packaging & OTA

### 12.1 vbmeta in target-files.zip

When `add_img_to_target_files.py` packs the target files, it reconstructs the
vbmeta chain from metadata stored in `META/misc_info.txt`:

```
avb_avbtool=avbtool
avb_vbmeta_key_path=device/.../avb.pem
avb_vbmeta_algorithm=SHA256_RSA409IT6
avb_vbmeta_rollback_index=202501
avb_vbmeta_args=--prop ... --chain_partition ...
avb_vbmeta_system_key_path=device/.../system.pem
avb_vbmeta_system_algorithm=SHA256_RSA4096
avb_vbmeta_system_args=...
```

### 12.2 OTA Update Handling

During OTA updates, the updater flashes new partition images. The vbmeta chain
must be consistent:

1. Bootloader verifies vbmeta.img against its built-in key
2. If `_src` vbmeta is signed with key A and `_dst` with key B, the OTA must
   update vbmeta first (or use a special `--downgrade` or key rotation mechanism)

### 12.3 Key Rotation

AVB supports key rotation via the `--rollback_index` mechanism. To rotate keys:

1. Sign the new vbmeta with the **new** key
2. Set the rollback index to a higher value than the previous
3. The bootloader (which has the old public key) cannot verify the new vbmeta
   → requires a bootloader update first

For OTA key rotation, the bootloader must be updated in a prior OTA to accept
the new key.

---

## 13. Troubleshooting

### 13.1 Build fails with "No key for chained partition"

```
error: No key found for chained partition 'vbmeta_system'
```

**Fix:** Set the key path for that partition:

```makefile
BOARD_AVB_VBMETA_SYSTEM_KEY_PATH := device/.../system.pem
BOARD_AVB_VBMETA_SYSTEM_ALGORITHM := SHA256_RSA4096
```

### 13.2 Build fails with "avbtool not found"

```
/bin/sh: avbtool: command not found
```

**Fix:** Ensure the build environment is properly set up:

```bash
source build/envsetup.sh
lunch <target>
make avbtool
```

Or set a custom path:

```makefile
BOARD_CUSTOM_AVBTOOL := /path/to/avbtool
```

### 13.3 Device boots with ORANGE state

The device is booting with an unsigned or test-signed vbmeta:

```
AVB: VERIFICATION: ERROR: public key not found
AVB: booting with ORANGE state
```

**Causes:**
- Using test keys (expected; OEM unlock triggers orange state)
- Using a different key than what the bootloader expects
- Bootloader needs an update to include the new public key

### 13.4 Device doesn't boot (RED state)

```
AVB: VERIFICATION FAILED
AVB: booting with RED state
```

**Causes:**
- Partition has been modified (AVB hash doesn't match)
- Wrong key was used to sign
- Rollback index mismatch

**Debug:** Verify the vbmeta and partitions:

```bash
# In the bootloader/fastboot:
fastboot getvar avb-version

# On a rooted device:
avbtool info --image /dev/block/by-name/vbmeta
avbtool verify_image --image /dev/block/by-name/vbmeta --key avb_pubkey.bin
```

### 13.5 Debugging vbmeta contents

```bash
# View vbmeta info
avbtool info --image vbmeta.img

# Verify vbmeta
avbtool verify_image --image vbmeta.img

# Verify a partition's AVB footer
avbtool info --image boot.img

# Extract descriptors from a partition
avbtool info --image system.img

# Dump raw vbmeta
avbtool info --image vbmeta.img --verbose
```

### 13.6 Common build variables to check

```bash
# After lunch, check your AVB config:
get_build_var BOARD_AVB_ENABLE
get_build_var BOARD_AVB_KEY_PATH
get_build_var BOARD_AVB_ALGORITHM
get_build_var BOARD_AVB_ROLLBACK_INDEX
get_build_var BOARD_AVB_MAKE_VBMETA_IMAGE_ARGS
get_build_var BUILDING_VBMETA_IMAGE
```

---

## 14. Reference: Source File Map

| File | Purpose |
|---|---|
| `build/core/config.mk` | AVBTOOL path resolution |
| `build/core/Makefile` | All vbmeta build targets (~lines 4700-5300) |
| `build/core/soong_config.mk` | BOARD_AVB_* → JSON for Soong |
| `build/soong/android/variable.go` | PartitionVariables struct |
| `build/soong/filesystem/vbmeta.go` | Soong vbmeta module factory & build actions |
| `build/soong/filesystem/bootimg.go` | AVB hash footer for boot images |
| `build/soong/filesystem/filesystem.go` | AVB hashtree footer for filesystem images |
| `build/soong/fsgen/vbmeta_partitions.go` | Chained vbmeta grouping logic |
| `build/soong/filesystem/avb_gen_vbmeta_image.go` | Unsigned vbmeta blob generation |
| `build/soong/etc/avbpubkey.go` | Public key extraction |
| `build/tools/releasetools/build_image.py` | AVB hashtree footer during image building |
| `build/tools/releasetools/add_img_to_target_files.py` | vbmeta repack in target-files.zip |
| `build/tools/releasetools/common.py` | BuildVBMeta() helper |
| `external/avb/avbtool.py` | The avbtool itself |
| `external/avb/test/data/` | Test keys (do NOT use in production) |
| `system/tools/mkbootimg/mkbootimg.py` | GKI boot certificate via avbtool |
| `system/tools/mkbootimg/gci/certify_bootimg.py` | Boot certificate generation |
| `system/tools/mkbootimg/gci/generate_gki_certificate.py` | GKI 2.0 certificate generation |
| `fec/` | libfec for Reed-Solomon FEC |

---

## Appendix A: Quick Setup Checklist

- [ ] `BOARD_AVB_ENABLE := true` set in BoardConfig.mk
- [ ] `BOARD_AVB_KEY_PATH` points to your RSA private key (or default test key)
- [ ] `BOARD_AVB_ALGORITHM` matches your key (typically `SHA256_RSA4096`)
- [ ] For each partition with its own key, set `BOARD_AVB_<PART>_KEY_PATH`
- [ ] For chained vbmeta groups, set `BOARD_AVB_VBMETA_SYSTEM` / `BOARD_AVB_VBMETA_VENDOR`
- [ ] For each chained group, set `BOARD_AVB_VBMETA_<GROUP>_KEY_PATH`
- [ ] Rollback indices are monotonically increasing per slot location
- [ ] Public keys are extracted (`avbtool extract_public_key`) for chain partitions
- [ ] Eng build: `--set_hashtree_disabled_flag` for faster boot
- [ ] Production: use real keys from a secure signing environment

## Appendix B: Quick Start Minimal Config

```makefile
# Minimal: just enable AVB with default test key
BOARD_AVB_ENABLE := true
# That's it! All partitions get hash/hashtree footers.
# The default test key is used for everything.
# No chaining -- all descriptors go directly into vbmeta.img.
```

## Appendix C: Custom ROM Checklist

For custom ROM developers building for non-Pixel devices:

1. **Extract or generate keys**: Use `avbtool extract_public_key` from stock
   firmware's vbmeta to get the expected public key, or generate your own
   (which will cause orange state unless you flash a modified bootloader)

2. **Match the bootloader**: The bootloader has a hardcoded public key. If you
   sign with a different key, the device will show an orange warning. To avoid
   this, you need to either:
   - Use the OEM's original key (if available)
   - Extract the OEM's public key from stock vbmeta and sign with it
   - Flash a modified bootloader (requires unlock)

3. **Multi-slot (AB)**: For AB devices, all AVB operations are per-slot.
   Each slot has its own vbmeta and signed partitions.

4. **Super partitions**: On devices with `super` (logical partitions), the
   partitions inside super (system, vendor, product, etc.) still get AVB
   hashtree footers that are referenced from vbmeta.

5. **Vendor boot image**: On GKI devices, `vendor_boot.img` (containing vendor
   ramdisk) must be signed with a hash footer and referenced from vbmeta.
