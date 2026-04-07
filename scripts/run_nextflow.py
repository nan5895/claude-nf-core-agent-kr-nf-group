"""nf-core pipeline parameter inspector (zero-dependency, non-interactive).

Fetches the pipeline schema from GitHub, extracts required and key optional
parameters, detects local system resources, and prints everything to stdout.
Does NOT execute Nextflow or prompt for user input.
"""

import json
import math
import os
import platform
import re
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

GITHUB_SCHEMA_URLS = [
    "https://raw.githubusercontent.com/{org}/{name}/master/nextflow_schema.json",
    "https://raw.githubusercontent.com/{org}/{name}/main/nextflow_schema.json",
]

SKIP_PARAMS = {
    "input", "outdir", "publish_dir_mode", "email", "plaintext_email",
    "monochrome_logs", "monochromeLogs", "help", "version", "validate_params",
    "max_memory", "max_cpus", "max_time",
}

SKIP_SECTIONS = {"institutional", "generic", "max_job_request"}

MAX_GUIDED = 10


# ── Schema fetching ──────────────────────────────────────────

def fetch_schema(pipeline: str) -> dict:
    """Fetch nextflow_schema.json from GitHub."""
    parts = pipeline.split("/")
    if len(parts) != 2:
        return {}
    org, name = parts
    for url_tmpl in GITHUB_SCHEMA_URLS:
        url = url_tmpl.format(org=org, name=name)
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            continue
    return {}


# ── Parameter extraction ─────────────────────────────────────

def extract_guided_params(schema: dict) -> List[dict]:
    """Extract required + enum parameters from the schema (up to MAX_GUIDED)."""
    defs = schema.get("$defs") or schema.get("definitions") or {}

    required_params = []
    enum_params = []
    seen = set()

    for section_name, section in defs.items():
        if any(skip in section_name for skip in SKIP_SECTIONS):
            continue
        props = section.get("properties", {})
        required_names = set(section.get("required", []))
        title = section.get("title", section_name)

        for param_name, param_info in props.items():
            if param_name in SKIP_PARAMS or param_name in seen:
                continue
            seen.add(param_name)

            entry = {
                "name": param_name,
                "description": param_info.get("description", ""),
                "help_text": param_info.get("help_text", ""),
                "enum": param_info.get("enum"),
                "type": param_info.get("type", "string"),
                "default": param_info.get("default"),
                "section": title,
                "is_required": param_name in required_names,
            }

            if param_name in required_names:
                required_params.append(entry)
            elif "enum" in param_info:
                enum_params.append(entry)

    result = list(required_params)
    remaining = max(0, MAX_GUIDED - len(result))
    result.extend(enum_params[:remaining])
    return result


# ── System resource detection (stdlib only) ──────────────────

def get_local_resources() -> Tuple[int, float]:
    """Detect CPU cores and physical RAM (GB) using only standard libraries."""
    cores = os.cpu_count() or 1

    system = platform.system()
    if system == "Darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True
            )
            ram_gb = int(out.strip()) / (1024 ** 3)
        except Exception:
            ram_gb = 8.0
    else:  # Linux
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        ram_gb = kb / (1024 ** 2)
                        break
                else:
                    ram_gb = 8.0
        except Exception:
            ram_gb = 8.0

    return cores, ram_gb


# ── Output formatting ────────────────────────────────────────

def print_params(params: List[dict]) -> None:
    """Print parameter table to stdout."""
    n_req = sum(1 for p in params if p["is_required"])
    n_opt = len(params) - n_req
    print(f"\n[Parameters] {n_req} required + {n_opt} key options = {len(params)} total\n")

    for i, p in enumerate(params, 1):
        tag = "REQUIRED" if p["is_required"] else "optional"
        default_str = f"  (default: {p['default']})" if p["default"] is not None else ""
        print(f"  [{i}/{len(params)}] [{tag}] --{p['name']}{default_str}")
        if p["description"]:
            print(f"          {p['description']}")
        if p.get("help_text"):
            sentences = re.split(r'(?<=[.!?])\s+', p["help_text"].strip())
            short_help = " ".join(sentences[:2])
            print(f"          Tip: {short_help}")
        if p["enum"]:
            enum_str = ", ".join(str(v) for v in p["enum"])
            print(f"          Allowed values: [{enum_str}]")
        print()


def print_resources(cores: int, ram_gb: float) -> None:
    """Print detected system resources and safe limits."""
    safe_cores = max(1, cores - 1)
    safe_ram = math.floor(ram_gb * 0.9)
    print("[System Resources]")
    print(f"  Detected: {cores} CPU cores, {ram_gb:.1f} GB RAM")
    print(f"  Safe limits for local execution:")
    print(f"    --max_cpus {safe_cores}")
    print(f"    --max_memory '{safe_ram}.GB'")
    print()


# ── Main ─────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python run_nextflow.py <pipeline_name>")
        print("Example: python run_nextflow.py nf-core/rnaseq")
        sys.exit(1)

    pipeline = sys.argv[1]

    # 1) Detect and print system resources
    cores, ram_gb = get_local_resources()
    print_resources(cores, ram_gb)

    # 2) Fetch schema and print parameters
    print(f"[INFO] Fetching '{pipeline}' schema from GitHub...")
    schema = fetch_schema(pipeline)

    if schema:
        params = extract_guided_params(schema)
        if params:
            print_params(params)
        else:
            print("[INFO] No guided parameters found in schema.\n")
    else:
        print("[WARN] Could not fetch schema from GitHub.\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
