#!/usr/bin/env python3
"""
Profile scrolling performance between PDF pages using py-spy and the headless Wayland environment.
Generates an SVG flame graph showing stack trace samples captured during scrolling.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "pdfatlas" / "main.py"
WAYLAND_SCRIPT = REPO_ROOT / "scripts" / "screenshot_wayland_app.sh"
DEFAULT_PDF = REPO_ROOT / "sandbox.local" / "sample-files" / "deepseek-cordis.local.pdf"
DEFAULT_OUTPUT = REPO_ROOT / "sandbox.local" / "scroll_profile.svg"


def profile(
    pdf_path: Path,
    from_page: int = 8,
    to_page: int = 9,
    output_svg: Path = DEFAULT_OUTPUT,
    render_mode: str = "mt",
    rate: int = 100,
    steps: int = 40,
    repeat: int = 3,
    subprocesses: bool = False,
    fmt: str = "all",
    use_shm: bool = True,
    zoom: float = 1.0,
):
    if not pdf_path.exists():
        print(f"[Error] PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    output_svg.parent.mkdir(parents=True, exist_ok=True)
    output_raw = output_svg.with_suffix(".raw")

    state = {
        "zoom": zoom,
        "scroll_benchmark": {
            "from_page": from_page,
            "to_page": to_page,
            "steps": steps,
            "interval_ms": 16,
            "repeat": repeat,
            "auto_quit": True,
        },
    }
    state_json = json.dumps(state)

    # Construct the python main command
    shm_flag = "" if use_shm else " --no-shm"
    target_cmd = (
        f"{sys.executable} -m pdfatlas.main {shlex.quote(str(pdf_path))} "
        f"--render-mode {shlex.quote(render_mode)}{shm_flag} --state {shlex.quote(state_json)}"
    )

    subproc_flag = " --subprocesses" if (subprocesses or render_mode == "mp") else ""

    # Determine py-spy format
    spy_format = "raw" if fmt == "all" else fmt

    target_output_file = output_raw if fmt == "all" else output_svg

    # Wrap inside py-spy record
    py_spy_cmd = (
        f"uv run py-spy record --format {spy_format} --rate {rate}{subproc_flag} "
        f"-o {shlex.quote(str(target_output_file))} -- {target_cmd}"
    )

    print(f"[Profiler] Target PDF: {pdf_path}")
    print(f"[Profiler] Benchmark: Page {from_page} -> {to_page} ({steps} steps x {repeat} repeats)")
    print(f"[Profiler] Render mode: {render_mode}{shm_flag} (subprocesses profiling: {bool(subproc_flag)})")
    print(f"[Profiler] Output files: {output_svg} / {output_raw}")
    print("[Profiler] Launching profiler harness via Wayland script...")

    env = dict(os.environ)
    env["APP_CMD"] = py_spy_cmd
    env["OUTPUT_PNG"] = str(REPO_ROOT / "sandbox.local" / "scroll_benchmark_frame.png")
    env["APP_STARTUP_WAIT"] = "1.0"
    env["WAIT_FOR_EXIT"] = "1"

    res = subprocess.run(["bash", str(WAYLAND_SCRIPT)], env=env, cwd=str(REPO_ROOT))

    if res.returncode != 0:
        print(f"[Error] Profiling failed with exit code {res.returncode}", file=sys.stderr)
        sys.exit(res.returncode)

    # If raw format was generated for 'all', build SVG flamegraph from raw data as well
    if fmt == "all" and output_raw.exists():
        svg_cmd = (
            f"uv run py-spy record --format flamegraph --rate {rate}{subproc_flag} "
            f"-o {shlex.quote(str(output_svg))} -- {target_cmd}"
        )
        print(f"[Profiler] Generating SVG flamegraph at {output_svg}...")
        env["APP_CMD"] = svg_cmd
        subprocess.run(["bash", str(WAYLAND_SCRIPT)], env=env, cwd=str(REPO_ROOT))

    if output_svg.exists():
        print(f"\n[Success] Flame graph saved to: {output_svg} ({output_svg.stat().st_size:,} bytes)")
    if output_raw.exists():
        print(f"[Success] Raw perf data saved to: {output_raw} ({output_raw.stat().st_size:,} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Profile scrolling performance and generate raw & flame graph data.")
    parser.add_argument(
        "pdf_path",
        nargs="?",
        type=Path,
        default=DEFAULT_PDF,
        help="Path to PDF file to profile (default: sandbox.local/sample-files/deepseek-cordis.local.pdf)",
    )
    parser.add_argument("--from-page", type=int, default=8, help="Start page number (1-indexed, default: 8)")
    parser.add_argument("--to-page", type=int, default=9, help="Target page number (1-indexed, default: 9)")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output SVG flame graph file path (default: sandbox.local/scroll_profile.svg)",
    )
    parser.add_argument("--render-mode", choices=["mt", "mp"], default="mt", help="Render mode (default: mt)")
    parser.add_argument("--rate", type=int, default=100, help="py-spy sampling frequency in Hz (default: 100)")
    parser.add_argument("--steps", type=int, default=40, help="Scroll interpolation steps (default: 40)")
    parser.add_argument("--repeat", type=int, default=3, help="Number of benchmark repeat passes (default: 3)")
    parser.add_argument(
        "--subprocesses",
        action="store_true",
        help="Profile subprocesses spawned by the app (automatically enabled for mp mode)",
    )
    parser.add_argument(
        "--no-shm",
        action="store_false",
        dest="use_shm",
        help="Disable zero-copy shared memory IPC for multiprocessing render backend",
    )
    parser.add_argument("--zoom", type=float, default=1.0, help="Initial document zoom level (default: 1.0)")
    parser.add_argument(
        "--format",
        choices=["all", "flamegraph", "raw", "speedscope"],
        default="all",
        help="Output format (default: all - saves both .raw and .svg)",
    )

    args = parser.parse_args()
    profile(
        pdf_path=args.pdf_path,
        from_page=args.from_page,
        to_page=args.to_page,
        output_svg=args.output,
        render_mode=args.render_mode,
        rate=args.rate,
        steps=args.steps,
        repeat=args.repeat,
        subprocesses=args.subprocesses,
        fmt=args.format,
        use_shm=args.use_shm,
        zoom=args.zoom,
    )
