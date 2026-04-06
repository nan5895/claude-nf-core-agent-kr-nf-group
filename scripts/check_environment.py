#!/usr/bin/env python3
"""
Pre-flight environment checks for nf-core pipeline execution.

Verifies that Nextflow, container engines (Docker/Singularity), Java,
and network access are available and correctly configured.

Usage:
    python check_environment.py
    python check_environment.py --json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _run_cmd(cmd: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", f"Command timed out: {' '.join(cmd)}"
    except Exception as e:
        return -3, "", str(e)


def check_java() -> Dict:
    """Check Java installation (required by Nextflow)."""
    rc, stdout, stderr = _run_cmd(["java", "-version"])
    output = stderr or stdout  # java -version prints to stderr
    if rc == 0:
        version = output.splitlines()[0] if output else "unknown"
        return {"name": "Java", "status": "ok", "version": version}
    return {"name": "Java", "status": "missing", "detail": "Install Java 11+ (e.g., `sdk install java`)"}


def check_nextflow() -> Dict:
    """Check Nextflow installation."""
    rc, stdout, _ = _run_cmd(["nextflow", "-version"])
    if rc == 0:
        for line in stdout.splitlines():
            if "version" in line.lower():
                return {"name": "Nextflow", "status": "ok", "version": line.strip()}
        return {"name": "Nextflow", "status": "ok", "version": stdout.splitlines()[0] if stdout else "unknown"}
    return {
        "name": "Nextflow",
        "status": "missing",
        "detail": "Install: curl -s https://get.nextflow.io | bash",
    }


def check_docker() -> Dict:
    """Check Docker installation and daemon status."""
    if not shutil.which("docker"):
        return {"name": "Docker", "status": "missing", "detail": "Install from https://docs.docker.com/get-docker/"}

    rc, stdout, stderr = _run_cmd(["docker", "info"])
    if rc != 0:
        return {"name": "Docker", "status": "error", "detail": "Docker installed but daemon not running. Start Docker Desktop or `sudo systemctl start docker`."}

    rc, stdout, _ = _run_cmd(["docker", "--version"])
    version = stdout if rc == 0 else "unknown"
    return {"name": "Docker", "status": "ok", "version": version}


def check_singularity() -> Dict:
    """Check Singularity/Apptainer installation."""
    for cmd_name in ["singularity", "apptainer"]:
        if shutil.which(cmd_name):
            rc, stdout, _ = _run_cmd([cmd_name, "--version"])
            version = stdout if rc == 0 else "unknown"
            return {"name": "Singularity/Apptainer", "status": "ok", "version": version}
    return {"name": "Singularity/Apptainer", "status": "missing", "detail": "Optional. Needed for HPC environments."}


def check_nf_core_tools() -> Dict:
    """Check nf-core tools installation."""
    rc, stdout, _ = _run_cmd(["nf-core", "--version"])
    if rc == 0:
        return {"name": "nf-core tools", "status": "ok", "version": stdout.strip()}
    return {
        "name": "nf-core tools",
        "status": "missing",
        "detail": "Optional. Install: pip install nf-core",
    }


def check_network() -> Dict:
    """Check network access to key services."""
    try:
        from utils.ncbi_utils import check_network_access
        ok, msg = check_network_access()
        return {
            "name": "Network Access",
            "status": "ok" if ok else "warning",
            "detail": msg,
        }
    except ImportError:
        # Fallback: simple HTTP check
        import urllib.request
        try:
            urllib.request.urlopen("https://raw.githubusercontent.com", timeout=10)
            return {"name": "Network Access", "status": "ok", "detail": "GitHub reachable"}
        except Exception as e:
            return {"name": "Network Access", "status": "error", "detail": str(e)}


def check_disk_space(min_gb: float = 10.0) -> Dict:
    """Check available disk space in working directory."""
    try:
        stat = os.statvfs(".")
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        status = "ok" if free_gb >= min_gb else "warning"
        detail = f"{free_gb:.1f} GB free" + ("" if status == "ok" else f" (recommend {min_gb:.0f}+ GB)")
        return {"name": "Disk Space", "status": status, "detail": detail}
    except Exception as e:
        return {"name": "Disk Space", "status": "unknown", "detail": str(e)}


def run_all_checks() -> List[Dict]:
    """Run all environment checks."""
    return [
        check_java(),
        check_nextflow(),
        check_docker(),
        check_singularity(),
        check_nf_core_tools(),
        check_network(),
        check_disk_space(),
    ]


STATUS_ICONS = {"ok": "OK", "warning": "!!", "error": "XX", "missing": "--", "unknown": "??"}


def print_report(checks: List[Dict]):
    """Print a human-readable report."""
    print()
    print("=" * 55)
    print("  nf-core Environment Check")
    print("=" * 55)

    has_critical = False

    for c in checks:
        icon = STATUS_ICONS.get(c["status"], "??")
        line = f"  [{icon}] {c['name']}"
        if "version" in c:
            line += f": {c['version']}"
        print(line)
        if "detail" in c and c["status"] != "ok":
            print(f"       {c['detail']}")
        if c["status"] in ("error", "missing") and c["name"] in ("Java", "Nextflow"):
            has_critical = True

    print("=" * 55)

    # Container engine summary
    docker = next((c for c in checks if c["name"] == "Docker"), None)
    singularity = next((c for c in checks if "Singularity" in c["name"]), None)
    has_container = (docker and docker["status"] == "ok") or (singularity and singularity["status"] == "ok")

    if not has_container:
        print("\n  [!!] No container engine available.")
        print("       Install Docker or Singularity to run nf-core pipelines.")
        has_critical = True

    if has_critical:
        print("\n  Critical issues found. Fix the items above before running pipelines.")
        return False

    print("\n  Environment is ready for nf-core pipelines.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Check environment for nf-core pipeline execution")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    checks = run_all_checks()

    if args.json:
        ready = all(
            c["status"] in ("ok", "warning")
            for c in checks
            if c["name"] in ("Java", "Nextflow")
        )
        docker_ok = any(c["status"] == "ok" for c in checks if c["name"] in ("Docker", "Singularity/Apptainer"))
        print(json.dumps({"ready": ready and docker_ok, "checks": checks}, indent=2))
        sys.exit(0 if (ready and docker_ok) else 1)

    ok = print_report(checks)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
