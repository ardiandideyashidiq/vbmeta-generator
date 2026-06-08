# vbmeta-generator

**Generate signed, chained AVB vbmeta images for custom Android ROMs — no AOSP build system required.**

`vbmeta-generator` is a self-contained Python CLI tool that takes a directory of raw Android partition images (`boot.img`, `dtbo.img`, `super.img`, etc.) and produces a complete set of signed AVB 2.0 verified boot images: `vbmeta.img`, `vbmeta_system.img`, `vbmeta_vendor.img`, a signed `boot.img`, and a rebuilt `super.img` with dm-verity hashtree footers on every system partition.

## Installation

### Via `uv tool install` (recommended)

```bash
# From a local path
uv tool install .

# From GitHub
uv tool install git+https://github.com/ardiandideyashidiq/vbmeta-generator.git

vbmeta-generator /path/to/ROM/ -y
```

### Via `uv run` (no install)

```bash
cd /path/to/vbmeta-generator
uv run vbmeta-generator /path/to/ROM/ -y
```

### Via `uvx` (one-shot)

```bash
uvx --from /path/to/vbmeta-generator vbmeta-generator /path/to/ROM/ -y
```

## Requirements

- Python ≥ 3.10
- [uv](https://docs.astral.sh/uv/) (package manager)
- Linux x86_64 (bundled AOSP binaries are x86_64)

## Usage

```
usage: vbmeta-generator [-h] [-o OUTPUT] [-k KEY]
                        [--algorithm {SHA256_RSA2048,SHA256_RSA4096,SHA512_RSA4096}]
                        [--rollback ROLLBACK] [--flags FLAGS] [-y] [--dry-run]
                        [-v] [--version]
                        rom_dir
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `rom_dir` | — | ROM directory containing `.img` files |
| `-o, --output` | `rom_dir/../vbmeta_out` | Output directory |
| `-k, --key` | Auto-generated | AVB signing key (PEM). Generated as `avb.key` if not specified |
| `--algorithm` | `SHA256_RSA2048` | Signing algorithm: `SHA256_RSA2048`, `SHA256_RSA4096`, or `SHA512_RSA4096` |
| `--rollback` | `1` | Rollback index for partitions (vbmeta uses 0) |
| `--flags` | `1` | vbmeta flags (`1` = `AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED`) |
| `-y, --yes` | — | Non-interactive mode (auto-confirm all) |
| `--dry-run` | — | Show the execution plan without modifying any files |
| `-v, --verbose` | — | Print every tool command as it runs |
| `--version` | — | Show version and exit |

### Examples

```bash
# Basic usage with a ROM directory
vbmeta-generator ~/roms/hyperxos/

# Specify output and custom key
vbmeta-generator ~/roms/hyperxos/ -o ./out -k mykey.pem

# Preview what would happen (safe to run)
vbmeta-generator ~/roms/hyperxos/ -y --dry-run

# Use stronger signing
vbmeta-generator ~/roms/hyperxos/ --algorithm SHA512_RSA4096 --rollback 5

# Verbose mode to debug tool invocations
vbmeta-generator ~/roms/hyperxos/ -y -v
```

## What It Does

The tool executes an 11-step pipeline:

| Step | Action | Description |
|------|--------|-------------|
| 1 | Key generation | Generates an RSA signing key pair using OpenSSL + `avbtool extract_public_key` |
| 2 | Super inspection | Parses `super.img` via `lpdump`, extracts logical partitions via `lpunpack` |
| 3 | Property extraction | Extracts `build.prop` from EROFS (`dump.erofs`) or EXT4 (`debugfs`) partitions, derives AVB properties (fingerprint, OS version, security patch) |
| 4 | Sign boot.img | Applies `avbtool add_hash_footer` with SHA256 hash to `boot.img` |
| 5 | Sign dtbo.img | Same hash-footer for `dtbo.img` |
| 6 | Hashtree footers | Applies `avbtool add_hashtree_footer` to each system partition (system, system_ext, product, vendor) with properties from build.prop |
| 7 | Rebuild super.img | Reconstructs `super.img` from modified partitions via `lpmake` |
| 8 | vbmeta_system.img | Creates chained vbmeta for system partitions with `avbtool make_vbmeta_image` |
| 9 | vbmeta_vendor.img | Creates chained vbmeta for vendor partition |
| 10 | vbmeta.img | Creates top-level vbmeta with chain descriptors for boot, vbmeta_system, vbmeta_vendor |
| 11 | Output | Copies all output files to the output directory |

## Architecture

```
                    ┌─────────────────────────┐
                    │     vbmeta-generator     │
                    │   (Orchestrator class)   │
                    ├─────────────────────────┤
                    │  cli.py     ─  argparse  │
                    │  image.py   ─  detection │
                    │  properties.py ─ props   │
                    │  super_partition.py      │
                    │  avb.py     ─  signing   │
                    │  utils.py   ─  tool run  │
                    └──────────┬──────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
   ┌──────────┐        ┌──────────────┐     ┌──────────────┐
   │  bin/    │        │  lib64/      │     │  Python std  │
   │  (24     │        │  (shared     │     │  lib + rich  │
   │  AOSP    │        │  libraries)  │     │  (CLI UI)    │
   │  prebuilts)       │              │     │              │
   └──────────┘        └──────────────┘     └──────────────┘
```

All AOSP binaries live in `vbmeta_generator/bin/` and their shared library dependencies in `vbmeta_generator/lib64/`. The `utils.run()` function sets `PATH` and `LD_LIBRARY_PATH` to these directories before executing any tool.

## Bundled Tools

| Category | Tools |
|----------|-------|
| AVB signing | `avbtool`, `openssl` |
| Dynamic partitions | `lpmake`, `lpunpack`, `lpdump` |
| EXT4 filesystem | `e2fsck`, `resize2fs`, `debugfs`, `e2fsdroid` |
| EROFS filesystem | `mkfs.erofs`, `dump.erofs` |
| Sparse images | `simg2img`, `img2simg` |
| Boot images | `mkbootimg`, `unpack_bootimg`, `repack_bootimg` |
| dm-verity | `build_image`, `build_verity_tree`, `build_verity_metadata`, `verity_signer`, `verity_verifier`, `generate_verity_key` |
| FEC | `fec` |
| Flashing | `fastboot` |

## Further Reading

See the `docs/` directory for in-depth technical documentation:

- [AVB 2.0 Overview](docs/avb-overview.md) — VBMeta struct, descriptors, chain partitions, rollback
- [avbtool Reference](docs/avbtool-reference.md) — Complete command reference for avbtool 1.3.0
- [dm-verity & FEC](docs/dm-verity-and-fec.md) — Merkle trees, block integrity, forward error correction
- [Super Partitions](docs/super-partitions.md) — Android logical partitions, lpmake/lpunpack/lpdump
- [Image Formats](docs/image-formats.md) — Detecting boot.img, super, sparse, EROFS, EXT4, AVB footers
- [Boot Image Tools](docs/boot-image-tools.md) — mkbootimg, unpack_bootimg, repack_bootimg
- [Filesystem Tools](docs/filesystem-tools.md) — debugfs, e2fsck, resize2fs, e2fsdroid, erofs tools
- [dm-verity Toolchain](docs/verity-tools.md) — AOSP verity build tools
- [Architecture](docs/architecture.md) — vbmeta-generator internal design

## Output Files

| File | Description |
|------|-------------|
| `vbmeta.img` | Top-level vbmeta image, signed with chain descriptors for boot, vbmeta_system, vbmeta_vendor |
| `vbmeta_system.img` | Chained vbmeta for system/system_ext/product partitions |
| `vbmeta_vendor.img` | Chained vbmeta for the vendor partition |
| `avb.key` | Generated RSA private key (PEM) |
| `avb.avbpubkey` | Extracted public key in AVB format |
| `boot.img` | Resigned boot image with hash footer |
| `super.img` | Rebuilt super image with hashtree footers on all partitions |

## How It Works (Briefly)

**AVB 2.0** (Android Verified Boot) uses a top-level `vbmeta` partition that is cryptographically signed. This partition contains descriptors that point to the hashes of other partitions (`boot`, `system`, `vendor`, etc.). At boot time, the bootloader verifies `vbmeta` against a known public key, then uses the descriptors to verify each partition before mounting it.

For **filesystem partitions** (system, vendor, etc.), AVB uses **dm-verity hashtrees** — Merkle hash trees that the kernel's `dm-verity` device-mapper target uses to verify every block on read. The hashtree is appended to the partition image, along with a VBMeta struct and a 64-byte footer (`AVBf`) at the end.

**Chained vbmeta partitions** (`vbmeta_system`, `vbmeta_vendor`) allow different owners to sign different groups of partitions. The top-level `vbmeta` contains chain partition descriptors that delegate trust to these sub-vbmeta images.

For details, see [docs/avb-overview.md](docs/avb-overview.md).

## License

Apache 2.0 (the bundled AOSP prebuilts carry their own licenses).
