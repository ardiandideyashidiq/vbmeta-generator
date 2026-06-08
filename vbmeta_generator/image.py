import os
import re
import struct
from enum import Enum
from pathlib import Path


class ImageType(Enum):
    BOOTIMG = "bootimg"
    DTBOIMG = "dtboimg"
    SUPER = "super"
    EROFS = "erofs"
    EXT4 = "ext4"
    SPARSE = "sparse"
    OTHER = "other"


class ImageInfo:
    def __init__(self, path: str):
        self.path = path
        self.filename = os.path.basename(path)
        self.partition_name = self._infer_partition_name()
        self.type: ImageType = ImageType.OTHER
        self.fstype: str | None = None
        self.is_sparse: bool = False
        self.has_avb: bool = False
        self.avb_algorithm: str | None = None
        self.avb_rollback_index: int | None = None
        self.avb_flags: int | None = None
        self.avb_descriptors: list[dict] | None = None
        self.size: int = 0
        self.partition_size: int | None = None

    def _infer_partition_name(self) -> str:
        name = Path(self.path).stem
        if name == "super":
            return name
        return re.sub(r"(_a|_b)?$", "", name)

    def __repr__(self) -> str:
        return f"<{self.filename} type={self.type.value} fstype={self.fstype} avb={self.has_avb}>"


SPARSE_MAGIC = 0xED26FF3A
EROFS_MAGIC = 0xE0F5E1E2
EXT4_MAGIC = 0xEF53
BOOT_MAGIC = b"ANDROID!"
SUPER_MAGIC = b"gDla"


def detect_image(path: str) -> ImageInfo:
    info = ImageInfo(path)
    file_size = os.path.getsize(path)

    if file_size < 64:
        info.size = file_size
        return info

    with open(path, "rb") as f:
        header = f.read(4096)

    with open(path, "rb") as f:
        f.seek(4096)
        super_magic = f.read(4)
    if super_magic == SUPER_MAGIC:
        info.type = ImageType.SUPER
        info.size = file_size
        return info

    if header[:8] == BOOT_MAGIC:
        info.type = ImageType.BOOTIMG
        info.size = file_size
        return info

    if len(header) >= 4:
        magic = struct.unpack("<I", header[:4])[0]
        if magic == SPARSE_MAGIC:
            info.type = ImageType.SPARSE
            info.is_sparse = True
            info.fstype = "ext4"
            info.size = file_size
            return info

    if len(header) >= 1028:
        magic = struct.unpack("<I", header[1024:1028])[0]
        if magic == EROFS_MAGIC:
            info.type = ImageType.EROFS
            info.fstype = "erofs"
            info.size = file_size
            return info

    if len(header) >= 1082:
        magic = struct.unpack("<H", header[1080:1082])[0]
        if magic == EXT4_MAGIC:
            info.type = ImageType.EXT4
            info.fstype = "ext4"
            info.size = file_size
            return info

    info.size = file_size
    return info


def detect_avb(info: ImageInfo, path: str | None = None) -> ImageInfo:
    from vbmeta_generator import utils

    p = path or info.path
    info.has_avb = False

    tail_size = 64
    if os.path.getsize(p) < tail_size:
        return info

    with open(p, "rb") as f:
        f.seek(os.path.getsize(p) - tail_size)
        tail = f.read(tail_size)

    if tail[:4] != b"AVBf":
        return info

    result = utils.run("avbtool", "info_image", "--image", p,
                       capture_output=True, text=True)
    if result.returncode != 0:
        return info

    out = result.stdout
    info.has_avb = True

    m = re.search(r"Algorithm:\s+(\S+)", out)
    if m:
        info.avb_algorithm = m.group(1)
    m = re.search(r"Rollback Index:\s+(\d+)", out)
    if m:
        info.avb_rollback_index = int(m.group(1))
    m = re.search(r"Flags:\s+(\d+)", out)
    if m:
        info.avb_flags = int(m.group(1))

    descriptors = []
    current = None
    for line in out.splitlines():
        if line.startswith("    Hash descriptor:") or line.startswith("    Hashtree descriptor:") or line.startswith("    Chain Partition descriptor:") or line.startswith("    Prop:"):
            if current:
                descriptors.append(current)
            if line.startswith("    Prop:"):
                current = {"type": "prop"}
                val = line.split(":", 1)[1].strip()
                if "->" in val:
                    current["key"] = val.split("->")[0].strip()
                    current["value"] = val.split("->")[1].strip()
                else:
                    current["text"] = val
                descriptors.append(current)
                current = None
            else:
                dtype = line.strip().replace(":", "").lower()
                current = {"type": dtype}
        elif current and ":" in line:
            key, val = line.strip().split(":", 1)
            current[key.strip().lower().replace(" ", "_")] = val.strip()
    if current:
        descriptors.append(current)

    info.avb_descriptors = descriptors
    return info
