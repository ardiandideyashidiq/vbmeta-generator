# Boot Image Tools

Android boot images pack the kernel, ramdisk, device tree, and metadata into a single binary. The boot image format has evolved through several header versions (0-4+), with newer versions adding vendor boot, DTBO, and bootconfig support.

`vbmeta-generator` bundles `mkbootimg`, `unpack_bootimg`, and `repack_bootimg` from AOSP for working with boot images. These tools are currently **not invoked directly** by `vbmeta-generator`—avbtool operates on boot images in-place by appending AVB hash footers without unpacking/repacking. They are bundled for potential use in ROM customization.

## Boot Image Header

All boot images start with the magic `ANDROID!` (8 bytes) at offset 0. The header version determines the layout:

```
Offset 0: "ANDROID!" magic
Followed by header (varies by version):
  Header v0: base, kernel/ramdisk offsets, cmdline, pagesize (1584 bytes)
  Header v1: v0 + recovery dtbo (1640 bytes)
  Header v2: v1 + dtb (1648 bytes)
  Header v3: simplified, only kernel + ramdisk + dtb (1640 bytes)
  Header v4: v3 + vendor boot data (1620 bytes)
```

## `unpack_bootimg` — Deconstruct a Boot Image

`unpack_bootimg` extracts the components of a boot, recovery, or vendor_boot image into individual files.

```
unpack_bootimg --boot_img BOOT_IMG [--out OUT]
               [--format {info,mkbootimg}] [-0]
```

| Option | Description |
|--------|-------------|
| `--boot_img PATH` | Path to the boot image (required) |
| `--out DIR` | Output directory for extracted components |
| `--format info` | Human-readable text output (default) |
| `--format mkbootimg` | Output shell-escaped arguments for `mkbootimg` |

### With `--format info`

Produces human-readable info:

```
Kernel: out/kernel (offset 0x00008000)
Ramdisk: out/ramdisk (offset 0x01000000)
Second stage: (none)
Page size: 4096
OS version: 15.0.0
OS patch level: 2026-04
Board: 
Command line: console=ttyMSM0,115200n8 androidboot.console=ttyMSM0...
```

### With `--format mkbootimg`

Outputs shell-escaped or null-terminated argument strings that can be piped directly to `mkbootimg`:

```bash
unpack_bootimg --boot_img boot.img --out out --format=mkbootimg \
    | tee mkbootimg_args
mkbootimg $(cat mkbootimg_args) -o repacked.img
```

With `-0` (null-terminated), useful for scripting:

```bash
unpack_bootimg --boot_img vendor_boot.img --out out --format=mkbootimg -0 \
    | tee mkbootimg_args
declare -a MKBOOTIMG_ARGS=()
while IFS= read -r -d '' ARG; do
    MKBOOTIMG_ARGS+=("${ARG}")
done <mkbootimg_args
mkbootimg "${MKBOOTIMG_ARGS[@]}" --vendor_boot repacked.img
```

## `mkbootimg` — Create a Boot Image

`mkbootimg` assembles kernel, ramdisk, DTB, and metadata into a boot image.

```
mkbootimg --kernel KERNEL [--ramdisk RAMDISK] [--dtb DTB]
          [--cmdline CMDLINE] [--base BASE]
          [--kernel_offset OFFSET] [--ramdisk_offset OFFSET]
          [--pagesize {2048,4096,8192,16384}]
          [--header_version VERSION]
          [--os_version VERSION] [--os_patch_level DATE]
          [--vendor_boot VENDOR_BOOT] [--vendor_ramdisk VENDOR_RAMDISK]
          [--vendor_bootconfig VENDOR_BOOTCONFIG]
          [--gki_signing_algorithm ALGO] [--gki_signing_key KEY]
          -o OUTPUT
```

| Option | Description |
|--------|-------------|
| `--kernel PATH` | Kernel image (zImage, Image.gz, etc.) |
| `--ramdisk PATH` | Initramfs/ramdisk image |
| `--dtb PATH` | Device tree blob |
| `--cmdline STR` | Kernel command line |
| `--base ADDR` | Base address (default: 0x10000000) |
| `--pagesize SIZE` | Page size (2048, 4096, 8192, or 16384) |
| `--header_version N` | Boot image header version (0-4) |
| `--os_version STR` | OS version for the boot image header |
| `--os_patch_level DATE` | Security patch level (YYYY-MM) |
| `--gki_signing_algorithm ALGO` | GKI signing algorithm for boot signature |
| `--gki_signing_key KEY` | GKI signing key path |
| `-o FILE` | Output file path |

### GKI Signing

`mkbootimg` supports **GKI (Generic Kernel Image)** signing via `--gki_signing_algorithm` and `--gki_signing_key`. GKI signing adds a VBMeta struct to the boot image, similar to what `avbtool add_hash_footer` does, but done at build time by `mkbootimg`.

GKI 2.0 boot images carry an embedded AVB footer with a hash descriptor for the boot image content. The `--gki_signing_avbtool_path` option allows specifying a custom `avbtool` binary path.

## `repack_bootimg` — Rebuild a Boot Image

`repack_bootimg` rebuilds a boot image from its components after modification. It's typically used after `unpack_bootimg` has extracted the parts.

```
repack_bootimg --boot_img BOOT_IMG --out OUT [--kernel KERNEL]
               [--ramdisk RAMDISK] [--dtb DTB] ...
```

## Boot Image Signing vs AVB Hash Footer

There are two approaches to signing boot images:

### 1. Embedded Boot Signature (Header v4+ / GKI)

The kernel and ramdisk are signed by `mkbootimg` at build time using a GKI signing key. The signature is embedded in the boot image header (not as an AVB footer). This is verified by the bootloader before passing control to the kernel.

### 2. AVB Hash Footer (avbtool add_hash_footer)

`avbtool append_hash_footer` computes a SHA256 hash of the entire boot image and stores it in an AVB VBMeta struct appended to the end of the image, with an `AVBf` footer at the very end. The hashtree descriptor for `boot` in `vbmeta.img` references this hash.

Most custom ROMs use **approach 2** because:
- It integrates with the existing AVB chain of trust
- It doesn't require recompiling the kernel or using GKI signing keys
- The bootloader's AVB implementation handles it automatically

### How vbmeta-generator Signs boot.img

Step 4 does:

```bash
avbtool add_hash_footer \
    --image boot.img \
    --partition_name boot \
    --partition_size $(avbtool info_image --image boot.img | grep "Image size" | awk '{print $3}' || stat -c%s boot.img) \
    --key avb.key \
    --algorithm SHA256_RSA2048 \
    --rollback_index 1 \
    --prop com.android.build.boot.fingerprint:...
```

If `boot.img` already has an AVB footer (some ROMs come pre-signed), the tool warns and re-signs:

```
⚠ boot.img already signed with SHA256_RSA2048, will re-sign
```

The existing footer is overwritten by `add_hash_footer` (avbtool replaces footers in-place).

## Misc: `fastboot`

The `fastboot` binary is bundled for potential use in flashing workflows. It is not invoked by `vbmeta-generator` but can be used to flash the generated images to a device:

```bash
fastboot flash vbmeta vbmeta.img
fastboot flash vbmeta_system vbmeta_system.img  
fastboot flash vbmeta_vendor vbmeta_vendor.img
fastboot flash boot boot.img
fastboot reboot
```

## References

- AOSP `system/tools/mkbootimg/` — mkbootimg source
- `unpack_bootimg` / `repack_bootimg` — same directory
- Boot image header format: `system/tools/mkbootimg/bootimg.h`
- GKI signing: `system/tools/mkbootimg/gki/gen_kernel_cmdline.py`
