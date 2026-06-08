import os
import re
import tempfile

from vbmeta_generator import utils
from vbmeta_generator.image import detect_image, ImageType


def parse_build_prop(content: str) -> dict[str, str]:
    props = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            props[key.strip()] = val.strip()
    return props


def extract_from_erofs(image_path: str) -> dict[str, str]:
    paths_to_try = ["/system/build.prop", "/build.prop", "/etc/build.prop"]
    for try_path in paths_to_try:
        r = utils.run("dump.erofs", "--path", try_path, image_path, capture_output=True, text=True)
        if r.returncode != 0:
            continue
        m = re.search(r"NID:\s+(\d+)", r.stdout)
        if not m:
            continue
        r2 = utils.run("dump.erofs", "--nid", m.group(1), "--cat", image_path, capture_output=True, text=True)
        if r2.returncode == 0 and r2.stdout.strip():
            return parse_build_prop(r2.stdout)
    return {}


def extract_from_ext4(image_path: str) -> dict[str, str]:
    raw = image_path
    cleanup = False

    info = detect_image(image_path)
    if info.is_sparse:
        raw = image_path + ".raw"
        utils.run("simg2img", image_path, raw, check=True)
        cleanup = True

    try:
        r = utils.run("debugfs", "-R", "cat /build.prop", raw, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return parse_build_prop(r.stdout)

        r = utils.run("debugfs", "-R", "cat /system/build.prop", raw, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return parse_build_prop(r.stdout)

        r = utils.run("debugfs", "-R", "cat /etc/build.prop", raw, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return parse_build_prop(r.stdout)
    finally:
        if cleanup and os.path.exists(raw):
            os.unlink(raw)

    return {}


def extract(image_path: str, fstype: str | None) -> dict[str, str]:
    if fstype is None:
        info = detect_image(image_path)
        if info.type in (ImageType.EROFS,):
            fstype = "erofs"
        elif info.type in (ImageType.EXT4, ImageType.SPARSE):
            fstype = "ext4"
    if fstype == "erofs":
        return extract_from_erofs(image_path)
    elif fstype == "ext4":
        return extract_from_ext4(image_path)
    return {}


def get_avb_props(partition: str, build_props: dict[str, str], fallback_props: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    prefix_map = {
        "system": "ro.system.build",
        "system_ext": "ro.system.build",
        "product": "ro.product.build",
        "vendor": "ro.vendor.build",
        "boot": "ro.build",
        "dtbo": "ro.build",
    }

    prefix = prefix_map.get(partition, f"ro.{partition}.build")
    fb = fallback_props or {}

    fingerprint = build_props.get(f"{prefix}.fingerprint") or build_props.get("ro.build.fingerprint") or fb.get("ro.build.fingerprint", "")
    os_version = build_props.get(f"{prefix}.version.release") or build_props.get("ro.build.version.release") or fb.get("ro.build.version.release", "")
    security_patch = build_props.get(f"{prefix}.version.security_patch") or build_props.get("ro.build.version.security_patch") or fb.get("ro.build.version.security_patch", "")

    avb_prefix = f"com.android.build.{partition}"
    props = []
    if fingerprint:
        props.append(("prop", f"{avb_prefix}.fingerprint", fingerprint))
    if os_version:
        props.append(("prop", f"{avb_prefix}.os_version", os_version))
    if security_patch:
        props.append(("prop", f"{avb_prefix}.security_patch", security_patch))
    return props
