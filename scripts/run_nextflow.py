"""nf-core pipeline safe execution and monitoring script (Usage tips + parameter guide)."""

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

# Add project root to path so `import utils` works
sys.path.insert(0, str(Path(__file__).parent.parent))

LOG_FILE = "nextflow_run.log"
DEFAULT_PROFILE = "docker"
MIN_GUIDED = 5
MAX_GUIDED = 10

# 가이드에서 제외할 파라미터 (이미 CLI로 지정하거나 고급/인프라 설정)
SKIP_PARAMS = {
    "input", "outdir", "publish_dir_mode", "email", "plaintext_email",
    "monochrome_logs", "monochromeLogs", "help", "version", "validate_params",
    "max_memory", "max_cpus", "max_time",
}

# 제외할 섹션 (파이프라인 로직과 무관한 인프라/일반 설정)
SKIP_SECTIONS = {"institutional", "generic", "max_job_request"}

GITHUB_SCHEMA_URLS = [
    "https://raw.githubusercontent.com/{org}/{name}/master/nextflow_schema.json",
    "https://raw.githubusercontent.com/{org}/{name}/main/nextflow_schema.json",
]


def fetch_schema(pipeline: str) -> dict:
    """GitHub에서 nextflow_schema.json을 가져온다."""
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


# ── Usage 문서 스크래핑 & 룰 기반 요약 ──────────────────────────

GITHUB_USAGE_URLS = [
    "https://raw.githubusercontent.com/{org}/{name}/master/docs/usage.md",
    "https://raw.githubusercontent.com/{org}/{name}/main/docs/usage.md",
]

# 추출할 admonition 패턴 (> [!WARNING], > [!NOTE], > [!IMPORTANT], > [!TIP])
RE_ADMONITION_START = re.compile(
    r"^>\s*\[!(WARNING|NOTE|IMPORTANT|TIP)\]", re.IGNORECASE
)
# Introduction / Quickstart 섹션 헤딩
RE_INTRO_HEADING = re.compile(
    r"^#{1,3}\s+(Introduction|Quickstart|Quick\s*Start)", re.IGNORECASE
)

ADMONITION_ICONS = {
    "WARNING": "⚠️",
    "NOTE": "📝",
    "IMPORTANT": "❗",
    "TIP": "💡",
}


def fetch_usage_md(pipeline: str) -> Optional[str]:
    """GitHub에서 docs/usage.md를 가져온다."""
    parts = pipeline.split("/")
    if len(parts) != 2:
        return None
    org, name = parts

    for url_tmpl in GITHUB_USAGE_URLS:
        url = url_tmpl.format(org=org, name=name)
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return resp.read().decode()
        except Exception:
            continue
    return None


def _extract_intro(lines: List[str]) -> Optional[str]:
    """Introduction / Quickstart 섹션의 첫 문단(1~3문장)을 추출한다."""
    capture = False
    buf = []
    for line in lines:
        if RE_INTRO_HEADING.match(line):
            capture = True
            continue
        if capture:
            if line.startswith("#"):
                break
            stripped = line.strip()
            if stripped and not stripped.startswith(">") and not stripped.startswith("```"):
                buf.append(stripped)
                # 마침표가 3개 이상 모이면 종료
                if sum(1 for s in buf if s.endswith((".","!","?"))) >= 3:
                    break
    return " ".join(buf) if buf else None


def _extract_admonitions(lines: List[str], max_count: int = 8) -> List[dict]:
    """Admonition 블록을 추출해 kind와 text를 담은 dict 리스트로 반환."""
    results = []
    i = 0
    while i < len(lines) and len(results) < max_count:
        m = RE_ADMONITION_START.match(lines[i])
        if m:
            kind = m.group(1).upper()
            icon = ADMONITION_ICONS.get(kind, "")
            buf = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                text = lines[i].lstrip("> ").strip()
                if text:
                    buf.append(text)
                i += 1
            if buf:
                summary = " ".join(buf)
                if len(summary) > 150:
                    first_sentence = re.split(r'(?<=[.!?])\s', summary, maxsplit=1)[0]
                    summary = first_sentence
                results.append({
                    "kind": kind,
                    "icon": icon,
                    "text": summary,
                })
        else:
            i += 1
    return results


# Nextflow 기본 규칙 카테고리 판별 키워드
_NF_BASIC_KEYWORDS = re.compile(
    r"(?i)(`?-c`?\s*<file>|single.?hyphen|core nextflow|nextflow.*option|"
    r"`?-profile`?\b|custom.?config|\.nf\.config|params\.yaml|"
    r"`?-resume`?\b|double.?hyphen|pipeline.*parameter)",
)


def _categorize_tip(tip_dict: dict) -> str:
    """팁 텍스트를 분석해 'nextflow' 또는 'pipeline' 카테고리로 분류."""
    if _NF_BASIC_KEYWORDS.search(tip_dict["text"]):
        return "nextflow"
    return "pipeline"


def summarize_usage(pipeline: str) -> Dict[str, List[str]]:
    """usage.md에서 핵심 정보를 추출해 카테고리별로 분류한다."""
    md = fetch_usage_md(pipeline)
    if not md:
        return {}

    lines = md.splitlines()
    categorized = {"pipeline": [], "nextflow": []}  # type: Dict[str, List[str]]

    # 1) Introduction 요약 → 항상 파이프라인 분석 팁
    intro = _extract_intro(lines)
    if intro:
        shortened = textwrap.shorten(intro, width=200, placeholder="...")
        categorized["pipeline"].append(f"📌 {shortened}")

    # 2) Admonition 블록 분류
    admonitions = _extract_admonitions(lines, max_count=8)
    for ad in admonitions:
        cat = _categorize_tip(ad)
        line = f"{ad['icon']} [{ad['kind']}] {ad['text']}"
        categorized[cat].append(line)

    # 각 카테고리 최대 4개
    for k in categorized:
        categorized[k] = categorized[k][:4]

    return categorized


def show_usage_tips(pipeline: str):
    """Usage 팁을 카테고리별로 나눠 터미널에 출력한다."""
    categorized = summarize_usage(pipeline)
    if not categorized or not any(categorized.values()):
        return

    print()
    print("=" * 60)
    print(f"  💡 [{pipeline} Usage & Best Practices]")

    # 🧬 파이프라인 분석 팁
    if categorized.get("pipeline"):
        print()
        print("  🧬 [파이프라인 분석 팁]")
        for tip in categorized["pipeline"]:
            wrapped = textwrap.fill(tip, width=54, subsequent_indent="      ")
            print(f"    {wrapped}")

    # 🔰 Nextflow 기본 규칙
    if categorized.get("nextflow"):
        print()
        print("  🔰 [Nextflow 기본 규칙 (Beginner Guide)]")
        for tip in categorized["nextflow"]:
            wrapped = textwrap.fill(tip, width=54, subsequent_indent="      ")
            print(f"    {wrapped}")

    print()
    print("=" * 60)


# ── 파라미터 추출 ────────────────────────────────────────────

def extract_guided_params(schema: dict) -> List[dict]:
    """스키마에서 1순위(required) + 2순위(enum) 파라미터를 5~10개 추출한다."""
    defs = schema.get("$defs") or schema.get("definitions") or {}

    required_params = []  # 1순위: required 필수 파라미터
    enum_params = []      # 2순위: enum 선택지 파라미터
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

    # 필수 파라미터는 전부 포함
    result = list(required_params)

    # 남은 슬롯을 enum 파라미터로 채움
    remaining = max(0, MAX_GUIDED - len(result))
    result.extend(enum_params[:remaining])

    # 최소 5개 미만이면 그대로 (필수가 적으면 어쩔 수 없음)
    return result


def run_parameter_guide(params: List[dict]) -> str:
    """인터랙티브 가이드로 파라미터를 수집한다."""
    selected = []

    n_req = sum(1 for p in params if p["is_required"])
    n_enum = len(params) - n_req
    print(f"\n[Parameter Guide] 필수 {n_req}개 + 주요 옵션 {n_enum}개 = 총 {len(params)}개 파라미터\n")

    for i, p in enumerate(params, 1):
        tag = "필수" if p["is_required"] else "선택"
        default_str = f" (기본값: {p['default']})" if p["default"] is not None else ""

        print(f"  [{i}/{len(params)}] [{tag}] --{p['name']}{default_str}")
        if p["description"]:
            print(f"          {p['description']}")

        # help_text가 있으면 첫 1~2문장을 팁으로 표시
        if p.get("help_text"):
            sentences = re.split(r'(?<=[.!?])\s+', p["help_text"].strip())
            short_help = " ".join(sentences[:2])
            print(f"          💡 [Tip] {short_help}")

        # enum이 있으면 선택지 표시, 없으면 자유 입력
        if p["enum"]:
            enum_str = ", ".join(str(v) for v in p["enum"])
            print(f"          가능한 값: [{enum_str}]")

        answer = input("          입력 [Enter=기본값]: ").strip()

        if answer:
            if p["enum"] and answer not in [str(v) for v in p["enum"]]:
                print(f"          [WARN] '{answer}'은(는) 유효하지 않습니다. 기본값을 사용합니다.\n")
            else:
                selected.append(f"--{p['name']} {answer}")
                print(f"          → {answer} 선택됨\n")
        else:
            if p["default"] is not None:
                print(f"          → 기본값 {p['default']} 사용\n")
            else:
                print(f"          → 건너뜀\n")

    return " ".join(selected)


def get_local_resources() -> Tuple[int, float]:
    """Detect CPU cores and physical RAM (GB) for Mac/Linux."""
    system = platform.system()
    cores = os.cpu_count() or 1

    if system == "Darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            ram_gb = int(out.strip()) / (1024 ** 3)
        except Exception:
            ram_gb = 8.0
    else:
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


def build_command(pipeline: str, samplesheet: str, outdir: str) -> str:
    parts = [
        f"nextflow run {pipeline}",
        f"-profile {DEFAULT_PROFILE}",
    ]
    if samplesheet.lower() != "none":
        parts.append(f"--input {os.path.abspath(samplesheet)}")
    parts.append(f"--outdir {os.path.abspath(outdir)}")
    return " ".join(parts)


def main():
    if len(sys.argv) < 4:
        print("Usage: python run_nextflow.py <pipeline_name> <samplesheet_path> <outdir>")
        sys.exit(1)

    pipeline = sys.argv[1]
    samplesheet = sys.argv[2]
    outdir = sys.argv[3]

    # Validate samplesheet (skip check when samplesheet is "none")
    if samplesheet.lower() != "none" and not os.path.isfile(samplesheet):
        print(f"[ERROR] Samplesheet not found: {samplesheet}")
        sys.exit(1)

    # 0) Environment selection
    print("\n" + "=" * 60)
    print("  Where will you run this pipeline?")
    print("    1. Local computer (needs resource limits)")
    print("    2. Cloud / HPC (Slurm, AWS, etc.)")
    print("=" * 60)
    env_choice = input("  Select [1/2]: ").strip()

    resource_flags = ""
    if env_choice == "1":
        cores, ram_gb = get_local_resources()
        safe_cores = max(1, cores - 1)
        safe_ram = math.floor(ram_gb * 0.9)
        resource_flags = f"--max_cpus {safe_cores} --max_memory '{safe_ram}.GB'"
        print(f"\n[INFO] Detected {cores} cores, {ram_gb:.1f} GB RAM.")
        print(f"[INFO] Safe limits: --max_cpus {safe_cores}  --max_memory '{safe_ram}.GB'")

    # 1) Usage doc tips
    print(f"\n[INFO] Fetching '{pipeline}' official docs...")
    show_usage_tips(pipeline)

    # 2) Schema-based parameter guide
    print(f"\n[INFO] Fetching '{pipeline}' schema from GitHub...")
    schema = fetch_schema(pipeline)
    guided_params_str = ""

    if schema:
        params = extract_guided_params(schema)
        if params:
            guided_params_str = run_parameter_guide(params)
        else:
            print("[INFO] No guided parameters found.\n")
    else:
        print("[WARN] Could not fetch schema. Proceeding with manual input.\n")

    # 3) Additional free-form parameters
    extra = input(
        "Enter any additional parameters (e.g., --skip_fastqc --save_trimmed) "
        "[press Enter to skip]: "
    ).strip()

    # 4) Assemble final command
    cmd = build_command(pipeline, samplesheet, outdir)
    if resource_flags:
        cmd = f"{cmd} {resource_flags}"
    if guided_params_str:
        cmd = f"{cmd} {guided_params_str}"
    if extra:
        cmd = f"{cmd} {extra}"

    print()
    print("=" * 60)
    print("[Dry-run] The following command will be executed:\n")
    print(f"  {cmd}\n")
    print(f"  Log file: {os.path.abspath(LOG_FILE)}")
    print("=" * 60)

    confirm = input("\nRun this command in the background? (y/n): ").strip().lower()

    if confirm != "y":
        print("Execution cancelled.")
        sys.exit(0)

    log = open(LOG_FILE, "w")
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    print(f"\n[OK] Background execution started (PID: {proc.pid})")
    print(f"To monitor progress: tail -f {LOG_FILE}")


if __name__ == "__main__":
    main()
