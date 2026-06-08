import argparse
import sys
from pathlib import Path

from vbmeta_generator.orchestrator import Orchestrator


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vbmeta-generator",
        description="Generate signed AVB vbmeta images for custom Android ROMs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  vbmeta-generator /path/to/ROM/\n"
            "  vbmeta-generator /path/to/ROM/ -o ./output -k mykey.pem\n"
            "  vbmeta-generator /path/to/ROM/ -y --dry-run\n"
        ),
    )
    p.add_argument("rom_dir", type=str, help="ROM directory containing .img files")
    p.add_argument("-o", "--output", type=str, default=None,
                   help="Output directory (default: ROM_dir/../vbmeta_out)")
    p.add_argument("-k", "--key", type=str, default=None,
                   help="AVB signing key (PEM). Generated if not specified")
    p.add_argument("--algorithm", type=str, default="SHA256_RSA2048",
                   choices=["SHA256_RSA2048", "SHA256_RSA4096", "SHA512_RSA4096"],
                   help="Signing algorithm (default: SHA256_RSA2048)")
    p.add_argument("--rollback", type=int, default=1,
                   help="Rollback index for partitions (default: 1; vbmeta uses 0)")
    p.add_argument("--flags", type=int, default=1,
                   help="vbmeta flags (default: 1 = disable hashtree verification)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="Non-interactive mode (auto-confirm all)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show plan without executing")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Verbose output (tool commands)")
    p.add_argument("--version", action="version", version="vbmeta-generator 1.0.0")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    rom_dir = Path(args.rom_dir).resolve()
    if not rom_dir.exists():
        print(f"Error: ROM directory not found: {rom_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output) if args.output else rom_dir.parent / "vbmeta_out"

    orch = Orchestrator(
        rom_dir=str(rom_dir),
        output_dir=str(output_dir),
        key_path=args.key,
        algorithm=args.algorithm,
        rollback_index=args.rollback,
        flags=args.flags,
        yes=args.yes,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    sys.exit(orch.run_all())


if __name__ == "__main__":
    main()
