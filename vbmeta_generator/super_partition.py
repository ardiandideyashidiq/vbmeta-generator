import os
import re
from dataclasses import dataclass, field

from vbmeta_generator import utils


@dataclass
class SuperLayout:
    device_size: int = 0
    metadata_size: int = 65536
    metadata_slots: int = 3
    groups: dict[str, int] = field(default_factory=dict)
    partitions: list[dict] = field(default_factory=list)


def parse_lpdump_output(output: str) -> SuperLayout:
    layout = SuperLayout()
    lines = output.splitlines()

    current_section = None
    current_part = None

    for raw in lines:
        stripped = raw.strip()

        if stripped in ("Partition table:", "Block device table:", "Group table:"):
            current_section = stripped.rstrip(":")
            continue

        if stripped == "Super partition layout:":
            current_section = None
            continue

        if current_section is None:
            m = re.search(r"Metadata max size:\s*(\d+)", stripped)
            if m:
                layout.metadata_size = int(m.group(1))
                continue
            m = re.search(r"Metadata slot count:\s*(\d+)", stripped)
            if m:
                layout.metadata_slots = int(m.group(1))
                continue
            continue

        if current_section == "Partition table":
            if stripped.startswith("Name:"):
                if current_part and current_part.get("extents"):
                    layout.partitions.append(current_part)
                current_part = {"name": stripped.split(":", 1)[1].strip(), "group": "", "extents": []}
            elif stripped.startswith("Group:") and current_part:
                current_part["group"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Extents:"):
                pass
            elif current_part and re.match(r"\s*\d+\s+\.\.\s+\d+", stripped):
                m = re.match(r"(\d+)\s+\.\.\s+(\d+)", stripped)
                if m:
                    current_part["extents"].append({"start": int(m.group(1)), "end": int(m.group(2))})
            elif stripped.startswith("---"):
                if current_part and current_part.get("extents"):
                    layout.partitions.append(current_part)
                current_part = None

        elif current_section == "Block device table":
            m = re.search(r"Size:\s*(\d+)", stripped)
            if m and "bytes" in stripped:
                layout.device_size = int(m.group(1))

        elif current_section == "Group table":
            if stripped.startswith("Name:"):
                current_part = {"name": stripped.split(":", 1)[1].strip()}
            elif stripped.startswith("Maximum size:") and current_part:
                m = re.search(r"(\d+)", stripped)
                max_size = int(m.group(1)) if m else 0
                if current_part.get("name"):
                    layout.groups[current_part["name"]] = max_size
                current_part = None

    if current_part and current_part.get("extents"):
        layout.partitions.append(current_part)

    return layout


def dump(path: str) -> SuperLayout:
    result = utils.run("lpdump", path, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"lpdump failed: {result.stderr}")
    return parse_lpdump_output(result.stdout)


def extract(super_path: str, output_dir: str) -> None:
    result = utils.run("lpunpack", "--slot=0", super_path, output_dir,
                       capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"lpunpack failed: {result.stderr}")


def rebuild(layout: SuperLayout, modified_images: dict[str, str],
            output_path: str) -> None:
    cmd = [
        utils.get_bin("lpmake"),
        "--device-size", str(layout.device_size),
        "--metadata-size", str(layout.metadata_size),
        "--metadata-slots", str(layout.metadata_slots),
    ]

    for group_name, max_size in layout.groups.items():
        if group_name == "default":
            continue
        if max_size > 0:
            cmd.extend(["--group", f"{group_name}:{max_size}"])
        else:
            cmd.extend(["--group", group_name])

    for part in layout.partitions:
        name = part["name"]
        group = part.get("group", "default")
        if name in modified_images:
            img_path = modified_images[name]
            part_size = os.path.getsize(img_path)
            part_size = ((part_size + 4095) // 4096) * 4096
            if group and group != "default":
                cmd.extend(["--partition", f"{name}:none:{part_size}:{group}"])
            else:
                cmd.extend(["--partition", f"{name}:none:{part_size}"])
            cmd.extend(["--image", f"{name}={img_path}"])
        else:
            sectors = sum(e["end"] - e["start"] + 1 for e in part.get("extents", []))
            part_size = sectors * 512
            if part_size > 0:
                if group and group != "default":
                    cmd.extend(["--partition", f"{name}:none:{part_size}:{group}"])
                else:
                    cmd.extend(["--partition", f"{name}:none:{part_size}"])

    cmd.extend(["--output", output_path])
    result = utils.run(*cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"lpmake failed:\n{result.stderr}")


def get_active_partitions(layout: SuperLayout) -> list[dict]:
    return [p for p in layout.partitions if p.get("extents")]
