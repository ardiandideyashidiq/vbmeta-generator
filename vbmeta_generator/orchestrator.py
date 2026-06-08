import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich import box

from vbmeta_generator import utils, avb, properties
from vbmeta_generator.image import detect_image, detect_avb, ImageType
from vbmeta_generator.super_partition import dump as super_dump, extract as super_extract, rebuild as super_rebuild, get_active_partitions, SuperLayout


class Orchestrator:
    def __init__(self, rom_dir: str, output_dir: str, key_path: str | None,
                 algorithm: str, rollback_index: int, flags: int,
                 yes: bool, dry_run: bool, verbose: bool):
        self.rom_dir = Path(rom_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.key_path = key_path
        self.algorithm = algorithm
        self.rollback_index = rollback_index
        self.vbmeta_flags = flags
        self.yes = yes
        self.dry_run = dry_run
        self.verbose = verbose
        self.console = Console()

        self.images: dict[str, ImageType] = {}
        self.image_infos: dict[str, dict] = {}
        self.super_layout: SuperLayout | None = None
        self.build_props: dict[str, dict[str, str]] = {}
        self.work_dir: Path | None = None

    def _confirm(self, msg: str) -> bool:
        if self.yes:
            return True
        return Confirm.ask(msg, default=True)

    def _status(self, msg: str):
        self.console.print(f"[bold blue]▸[/] {msg}")

    def _ok(self, msg: str):
        self.console.print(f"  [green]✓[/] {msg}")

    def _warn(self, msg: str):
        self.console.print(f"  [yellow]⚠[/] {msg}")

    def _fail(self, msg: str):
        self.console.print(f"  [red]✗[/] {msg}")

    def _dash(self):
        self.console.print()

    @contextmanager
    def _spinner(self, message: str):
        with self.console.status(f"[bold blue]{message}"):
            yield

    def run_all(self):
        self.console.print(Panel.fit(
            "[bold yellow]vbmeta-generator[/] — AVB vbmeta image builder",
            border_style="blue"))

        if not self._check_tools():
            return 1

        if not self._detect_images():
            return 1

        if not self._confirm_start():
            return 1

        tmp_root = Path("/home/rd/tmp")
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.work_dir = Path(tempfile.mkdtemp(prefix="vbmeta_", dir=str(tmp_root)))
        try:
            self._step_keygen()
            self._step_super()
            self._step_props()
            self._step_sign_boot()
            self._step_sign_dtbo()
            self._step_hashtree()
            self._step_rebuild_super()
            self._step_vbmeta_system()
            self._step_vbmeta_vendor()
            self._step_vbmeta()
            self._step_output()
            self._step_summary()
        finally:
            if not self.verbose:
                shutil.rmtree(self.work_dir, ignore_errors=True)

        return 0

    def _check_tools(self) -> bool:
        self._status("Checking bundled tools...")
        required = [
            "avbtool", "openssl", "lpmake", "lpdump", "lpunpack",
            "simg2img", "img2simg", "e2fsck", "debugfs",
            "dump.erofs",
        ]
        missing = []
        for tool in required:
            if not (utils.BIN_DIR / tool).exists():
                missing.append(tool)

        if missing:
            self._fail(f"Missing tools: {', '.join(missing)}")
            return False
        self._ok(f"All {len(required)} tools available")
        return True

    def _detect_images(self) -> bool:
        self._status("Scanning ROM directory...")
        if not self.rom_dir.exists():
            self._fail(f"Directory not found: {self.rom_dir}")
            return False

        img_files = sorted(self.rom_dir.glob("*.img"))
        if not img_files:
            self._fail("No .img files found")
            return False

        table = Table(title="Detected Images", box=box.SIMPLE)
        table.add_column("File", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Size", style="green", justify="right")
        table.add_column("AVB", style="yellow")

        for f in img_files:
            info = detect_avb(detect_image(str(f)))
            self.image_infos[f.name] = {
                "info": info,
                "type": info.type,
                "fstype": info.fstype,
                "size": info.size,
                "has_avb": info.has_avb,
                "avb_algorithm": info.avb_algorithm,
                "avb_descriptors": info.avb_descriptors,
                "full_path": str(f),
            }
            type_str = info.type.value
            if info.fstype:
                type_str += f" ({info.fstype})"
            avb_str = ""
            if info.has_avb:
                avb_str = info.avb_algorithm or "present"
            size_str = f"{info.size / 1024 / 1024:.0f}M" if info.size > 1024 * 1024 else f"{info.size / 1024:.0f}K"
            table.add_row(f.name, type_str, size_str, avb_str)

        self.console.print(table)
        return True

    def _confirm_start(self) -> bool:
        has_super = any(v["type"] == ImageType.SUPER for v in self.image_infos.values())
        targets = [n for n, v in self.image_infos.items() if v["type"] not in (ImageType.SUPER, ImageType.OTHER)]
        if not targets:
            self._fail("No recognizable images found")
            return False

        self._dash()
        self.console.print(f"[dim]Output dir:[/] {self.output_dir}")
        self.console.print(f"[dim]Algorithm:[/] {self.algorithm}")
        self.console.print(f"[dim]Rollback index:[/] {self.rollback_index}")
        self.console.print(f"[dim]vbmeta flags:[/] {self.vbmeta_flags}")

        if not self._confirm("Proceed with vbmeta generation?"):
            return False
        self._dash()
        return True

    def _step_keygen(self):
        self._status("Step 1/11: Generating signing key")
        if self.key_path:
            kp = Path(self.key_path)
            if not kp.exists():
                self._fail(f"Key not found: {self.key_path}")
                raise SystemExit(1)
            self.pubkey_path = str(kp.with_suffix(".avbpubkey"))
            if not Path(self.pubkey_path).exists() and not self.dry_run:
                utils.run("avbtool", "extract_public_key",
                          "--key", self.key_path, "--output", self.pubkey_path,
                          check=True)
            self.key_path = str(kp)
        else:
            self.key_path = str(self.work_dir / "avb.key")
            self.pubkey_path = str(self.work_dir / "avb.avbpubkey")
            avb.generate_key(self.key_path, self.algorithm)

        if not self.dry_run or Path(self.key_path).exists():
            sha1 = avb.extract_public_key_digest(self.key_path)
        else:
            sha1 = "(dry-run, would be generated)"
        self.console.print(Panel(
            f"[cyan]Key:[/] {Path(self.key_path).name}\n"
            f"[cyan]Algorithm:[/] {self.algorithm}\n"
            f"[cyan]SHA1:[/] {sha1}",
            title="Signing Key", border_style="green"))
        if sha1 != "(dry-run, would be generated)":
            self._ok(f"Key ready: SHA1 {sha1[:16]}...")

    def _step_props(self):
        self._status("Step 3/11: Extracting build.prop properties")

        partition_images = {}
        for name, info in self.image_infos.items():
            pname = info["info"].partition_name
            if info["type"] in (ImageType.EROFS, ImageType.EXT4, ImageType.SPARSE):
                partition_images[pname] = info["full_path"]

        for base, img_path in getattr(self, "active_partitions", {}).items():
            if base not in partition_images:
                partition_images[base] = img_path

        if not partition_images:
            self._warn("No filesystem images found, skipping props")

        table = Table(title="Build Properties", box=box.SIMPLE)
        table.add_column("Partition", style="cyan")
        table.add_column("Fingerprint", style="yellow")
        table.add_column("OS", style="magenta")
        table.add_column("Security Patch", style="green")

        system_props = {}
        for partition, img_path in partition_images.items():
            fstype = self.image_infos.get(Path(img_path).name, {}).get("fstype")
            bp = {} if self.dry_run else properties.extract(img_path, fstype)

            if partition == "system":
                system_props = bp

            self.build_props[partition] = bp

            fp = bp.get("ro.build.fingerprint", "") or bp.get(f"ro.{partition}.build.fingerprint", "")
            os_ver = bp.get("ro.build.version.release", "") or bp.get(f"ro.{partition}.build.version.release", "")
            sec_patch = bp.get("ro.build.version.security_patch", "") or bp.get(f"ro.{partition}.build.version.security_patch", "")

            if not fp:
                fp = "(using system)" if partition != "system" else "(not found)"

            table.add_row(partition,
                         fp.split("/")[0] if "/" in fp else fp[:40],
                         os_ver, sec_patch)

        self.console.print(table)

    def _step_super(self):
        self._status("Step 2/11: Inspecting super partition")

        super_path = None
        for name, info in self.image_infos.items():
            if info["type"] == ImageType.SUPER:
                super_path = info["full_path"]
                break

        if not super_path:
            self._warn("No super.img found, skipping super handling")
            self.active_partitions = {}
            return

        self.super_layout = super_dump(super_path)
        active = get_active_partitions(self.super_layout)

        table = Table(title="Super Partition Layout (Active Slot)", box=box.SIMPLE)
        table.add_column("Partition", style="cyan")
        table.add_column("Group", style="magenta")
        table.add_column("Size", style="green", justify="right")

        for p in active:
            sectors = sum(e["end"] - e["start"] + 1 for e in p.get("extents", []))
            size_bytes = sectors * 512
            size_str = f"{size_bytes / 1024 / 1024:.0f}M" if size_bytes > 1024 * 1024 else f"{size_bytes / 1024:.0f}K"
            table.add_row(p["name"], p.get("group", ""), size_str)

        self.console.print(table)

        if self.dry_run:
            self.active_partitions = {}
            return

        self.super_extract_dir = Path(tempfile.mkdtemp(prefix="super_extract_", dir=str(Path("/home/rd/tmp"))))
        with self._spinner("  Extracting logical partitions from super.img..."):
            super_extract(super_path, str(self.super_extract_dir))

        self.active_partitions = {}
        self._super_name_map = {}
        for p in active:
            pname = p["name"]
            base = re.sub(r"_(a|b)$", "", pname)
            extracted = Path(self.super_extract_dir) / f"{pname}.img"
            if not extracted.exists():
                extracted = Path(self.super_extract_dir) / pname
            if extracted.exists():
                self.active_partitions[base] = str(extracted)
                self._super_name_map[base] = pname
                self._ok(f"  Extracted {pname} → {base}")

    def _step_sign_boot(self):
        self._status("Step 4/11: Signing boot.img")

        boot_path = None
        for name, info in self.image_infos.items():
            if info["type"] == ImageType.BOOTIMG:
                boot_path = info["full_path"]
                break

        if not boot_path:
            self._warn("No boot.img found, skipping")
            return

        boot_partition = Path(boot_path).stem
        avb_algo = self.image_infos.get(Path(boot_path).name, {}).get("avb_algorithm")
        if avb_algo and avb_algo != "NONE":
            self._warn(f"boot.img already signed with {avb_algo}, will re-sign")

        size = avb.get_image_size(boot_path) or os.path.getsize(boot_path)

        if not self.dry_run:
            boot_props = properties.get_avb_props("boot",
                                                  self.build_props.get("system", {}))
            with self._spinner("  Signing boot.img..."):
                avb.add_hash_footer(
                    boot_path, "boot", self.key_path, self.algorithm,
                    rollback_index=self.rollback_index,
                    partition_size=size,
                    props=boot_props)
        self._ok("boot.img signed")

    def _step_sign_dtbo(self):
        self._status("Step 5/11: Signing dtbo.img")

        dtbo_path = None
        for name, info in self.image_infos.items():
            if info["type"] == ImageType.DTBOIMG:
                dtbo_path = info["full_path"]
                break

        if not dtbo_path:
            for name, info in self.image_infos.items():
                info2 = info["info"]
                if info2.partition_name == "dtbo" and info["type"] in (ImageType.BOOTIMG, ImageType.OTHER):
                    dtbo_path = info["full_path"]
                    break

        if not dtbo_path:
            if self.verbose:
                self._warn("No dtbo.img found, skipping dtbo signing")
            return

        size = avb.get_image_size(dtbo_path) or os.path.getsize(dtbo_path)

        if not self.dry_run:
            dtbo_props = properties.get_avb_props("dtbo",
                                                  self.build_props.get("system", {}))
            with self._spinner("  Signing dtbo.img..."):
                avb.add_hash_footer(
                    dtbo_path, "dtbo", self.key_path, self.algorithm,
                    rollback_index=self.rollback_index,
                    partition_size=size,
                    props=dtbo_props)
        self._ok("dtbo.img signed")

    def _step_hashtree(self):
        self._status("Step 6/11: Adding hashtree footers")

        system_partitions = ["system", "system_ext", "product", "vendor"]
        targets = []

        for name, info in self.image_infos.items():
            pname = info["info"].partition_name
            if pname in system_partitions and info["type"] in (ImageType.EROFS, ImageType.EXT4, ImageType.SPARSE):
                targets.append((pname, info["full_path"], info["type"], info.get("fstype")))

        for pname in system_partitions:
            if pname in self.active_partitions:
                if not any(t[0] == pname for t in targets):
                    targets.append((pname, self.active_partitions[pname],
                                    ImageType.OTHER, "ext4"))

        if not targets:
            self._warn("No system partitions found for hashtree signing")
            return

        for partition, img_path, img_type, fstype in targets:
            if self.dry_run:
                continue

            # If this partition was extracted from super, use the extracted copy
            # (the standalone image may differ)
            if partition in self.active_partitions:
                work_img = self.active_partitions[partition]
            else:
                work_img = img_path

            if img_type == ImageType.SPARSE:
                self._status(f"    Unsparsing {partition}...")
                raw_path = str(self.work_dir / f"{partition}_raw.img")
                utils.run("simg2img", work_img, raw_path, check=True)
                utils.run("e2fsck", "-f", raw_path, capture_output=True, check=False)
                work_img = raw_path

            hash_algo = "sha256" if fstype == "erofs" else "sha1"
            part_props = properties.get_avb_props(
                partition,
                self.build_props.get(partition, {}),
                self.build_props.get("system", {}))

            with self._spinner(f"  Adding hashtree footer to {partition}..."):
                avb.add_hashtree_footer(
                    work_img, partition, self.key_path, self.algorithm,
                    rollback_index=self.rollback_index,
                    hash_algorithm=hash_algo,
                    props=part_props)

            if img_type == ImageType.SPARSE:
                self._status(f"    Re-sparsing {partition}...")
                utils.run("img2simg", work_img, img_path, check=True)
                os.unlink(work_img)

            # Track modified path for super rebuild
            if partition in self.active_partitions:
                if img_type != ImageType.SPARSE:
                    self.active_partitions[partition] = work_img

            self._ok(f"{partition} hashtree added")

    def _step_rebuild_super(self):
        self._status("Step 7/11: Rebuilding super.img")

        if not self.super_layout or not self.active_partitions:
            self._warn("No super to rebuild, skipping")
            return

        modified = {}
        for base, path in self.active_partitions.items():
            super_name = self._super_name_map.get(base, base)
            modified[super_name] = path

        output_super = str(self.work_dir / "super_new.img")

        if not self.dry_run:
            with self._spinner("  Rebuilding super.img..."):
                super_rebuild(self.super_layout, modified, output_super)
            self.rebuilt_super = output_super
        else:
            self.rebuilt_super = output_super

        self._ok(f"super.img rebuilt ({Path(output_super).stat().st_size / 1024 / 1024:.0f}M)" if not self.dry_run else "super.img would be rebuilt")

    def _step_vbmeta_system(self):
        self._status("Step 8/11: Creating vbmeta_system.img")

        output = str(self.work_dir / "vbmeta_system.img")

        partitions = ["system", "system_ext", "product"]
        include_images = []
        for p in partitions:
            if p in self.active_partitions:
                include_images.append(self.active_partitions[p])
            else:
                for name, info in self.image_infos.items():
                    if info["info"].partition_name == p and info["type"] in (ImageType.EROFS, ImageType.EXT4, ImageType.SPARSE):
                        include_images.append(info["full_path"])

        if not self.dry_run:
            avb.make_vbmeta_image(
                output, self.key_path, self.algorithm,
                rollback_index=self.rollback_index,
                flags=0, padding_size=4096,
                include_descriptors_from=include_images if include_images else None)
        self.vbmeta_system = output
        self._ok("vbmeta_system.img created")

    def _step_vbmeta_vendor(self):
        self._status("Step 9/11: Creating vbmeta_vendor.img")

        output = str(self.work_dir / "vbmeta_vendor.img")

        include_images = []
        if "vendor" in self.active_partitions:
            include_images.append(self.active_partitions["vendor"])
        else:
            for name, info in self.image_infos.items():
                if info["info"].partition_name == "vendor" and info["type"] in (ImageType.EROFS, ImageType.EXT4, ImageType.SPARSE):
                    include_images.append(info["full_path"])

        if not self.dry_run:
            avb.make_vbmeta_image(
                output, self.key_path, self.algorithm,
                rollback_index=self.rollback_index,
                flags=0, padding_size=4096,
                include_descriptors_from=include_images if include_images else None)
        self.vbmeta_vendor = output
        self._ok("vbmeta_vendor.img created")

    def _step_vbmeta(self):
        self._status("Step 10/11: Creating vbmeta.img")

        output = str(self.work_dir / "vbmeta.img")

        chain_partitions = []
        if "boot" in self.image_infos or any(v["type"] == ImageType.BOOTIMG for v in self.image_infos.values()):
            chain_partitions.append(("boot", 1, self.pubkey_path))
        chain_partitions.append(("vbmeta_system", 2, self.pubkey_path))
        chain_partitions.append(("vbmeta_vendor", 3, self.pubkey_path))

        include_descriptors = []
        for name, info in self.image_infos.items():
            if info["info"].partition_name == "dtbo":
                include_descriptors.append(info["full_path"])

        if not self.dry_run:
            avb.make_vbmeta_image(
                output, self.key_path, self.algorithm,
                rollback_index=0,
                flags=self.vbmeta_flags,
                padding_size=4096,
                chain_partitions=chain_partitions,
                include_descriptors_from=include_descriptors if include_descriptors else None)
        self.vbmeta = output
        self._ok("vbmeta.img created")

    def _step_output(self):
        self._status("Step 11/11: Copying output files")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        files_to_copy = {}

        files_to_copy["vbmeta.img"] = getattr(self, "vbmeta", None)
        files_to_copy["vbmeta_system.img"] = getattr(self, "vbmeta_system", None)
        files_to_copy["vbmeta_vendor.img"] = getattr(self, "vbmeta_vendor", None)

        if self.key_path and os.path.exists(self.key_path):
            files_to_copy["avb.key"] = self.key_path
        pub = self.pubkey_path if hasattr(self, "pubkey_path") else None
        if pub and os.path.exists(pub):
            files_to_copy["avb.avbpubkey"] = pub

        for name, info in self.image_infos.items():
            path = info["full_path"]
            if info["type"] in (ImageType.BOOTIMG, ImageType.DTBOIMG):
                files_to_copy[name] = path

        rebuilt = getattr(self, "rebuilt_super", None)
        if rebuilt and os.path.exists(rebuilt):
            files_to_copy["super.img"] = rebuilt

        table = Table(title="Output Files", box=box.SIMPLE)
        table.add_column("File", style="cyan")
        table.add_column("Size", style="green", justify="right")

        for fname, src in files_to_copy.items():
            if not src:
                continue
            exists = os.path.exists(src)
            size_str = "?"
            if exists:
                size = os.path.getsize(src)
                size_str = f"{size / 1024 / 1024:.0f}M" if size > 1024 * 1024 else f"{size / 1024:.0f}K"
            else:
                size_str = "(dry-run)"
            if not self.dry_run and exists:
                dst = self.output_dir / fname
                shutil.copy2(src, dst)
            table.add_row(fname, size_str)

        self.console.print(table)
        if self.dry_run:
            self._ok(f"Output would be written to: {self.output_dir}")
        else:
            self._ok(f"Output written to: {self.output_dir}")

    def _step_summary(self):
        self._dash()
        self.console.print(Panel.fit(
            "[bold green]✓ vbmeta generation complete[/]\n\n"
            f"Output: [cyan]{self.output_dir}[/]",
            border_style="green"))
