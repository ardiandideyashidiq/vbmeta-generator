import os
import subprocess
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BIN_DIR = PACKAGE_DIR / "bin"
LIB64_DIR = PACKAGE_DIR / "lib64"


def get_bin(name: str) -> str:
    return str(BIN_DIR / name)


def run(tool: str, *args, capture_output=False, text=True, check=False, **kwargs):
    env = os.environ.copy()
    env["PATH"] = f"{BIN_DIR}:{env.get('PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{LIB64_DIR}:{env.get('LD_LIBRARY_PATH', '')}"

    tool_path = BIN_DIR / tool
    if tool_path.exists():
        cmd = [str(tool_path)]
    else:
        cmd = [tool]

    cmd.extend(str(a) for a in args)
    return subprocess.run(
        cmd,
        env=env,
        capture_output=capture_output,
        text=text,
        check=check,
        **kwargs,
    )


def run_verbose(tool: str, *args, **kwargs):
    progress_desc = kwargs.pop("progress_desc", None)
    verbose = kwargs.pop("verbose", False)
    console = kwargs.pop("console", None)

    if verbose and console:
        console.print(f"[dim]  $ {tool} {' '.join(str(a) for a in args)}[/dim]")

    return run(tool, *args, **kwargs)
