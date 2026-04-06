"""nf-core 파이프라인용 samplesheet.csv 자동 생성 스크립트 (범용 동적 스키마 엔진)."""

import csv
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path so `import utils` works
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.sample_inference import (
    extract_sample_info,
    infer_tumor_normal_status,
    match_read_pairs,
)
from utils.validators import validate_samplesheet, ValidationResult
from utils.file_discovery import discover_files

# ── GitHub 스키마 URL 템플릿 ──────────────────────────────────
GITHUB_SCHEMA_URLS = [
    "https://raw.githubusercontent.com/{org}/{name}/master/assets/schema_input.json",
    "https://raw.githubusercontent.com/{org}/{name}/main/assets/schema_input.json",
]

# Paired-end 패턴: _R1/_R2 또는 _1/_2 (확장자 직전)
RE_PAIR = re.compile(r"^(.+?)(?:_R?([12]))(\..+)$")

# 파일 스캔으로 자동 매핑 가능한 컬럼
AUTO_FILE_COLUMNS = {"fastq_1", "fastq_2", "spring_1", "spring_2"}
AUTO_INFER_COLUMNS = {"sample"}

# ── Sarek 특화: Tumor/Normal 추론 키워드 ──────────────────────
TUMOR_KEYWORDS = re.compile(
    r"(tumor|tumour|cancer|metastasis|met[0-9]|relapse|recurrence)", re.IGNORECASE
)
NORMAL_KEYWORDS = re.compile(
    r"(normal|blood|germline|control|healthy|buffy|pbmc)", re.IGNORECASE
)


# ══════════════════════════════════════════════════════════════
# 1. 동적 스키마 파싱 엔진
# ══════════════════════════════════════════════════════════════

def fetch_schema(pipeline: str) -> dict:
    """GitHub에서 schema_input.json을 가져와 파싱한다.

    Returns dict with keys: properties, required, raw
    """
    parts = pipeline.split("/")
    if len(parts) != 2:
        print(f"[ERROR] 파이프라인 이름이 올바르지 않습니다: {pipeline}")
        print("        형식: nf-core/<pipeline_name>")
        sys.exit(1)

    org, name = parts

    for url_tmpl in GITHUB_SCHEMA_URLS:
        url = url_tmpl.format(org=org, name=name)
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                schema = json.loads(resp.read().decode())
        except Exception:
            continue

        # items.properties 에서 추출
        items = schema.get("items", {})
        props = items.get("properties", {})

        # fallback: definitions 에서 찾기
        if not props:
            for def_val in schema.get("definitions", {}).values():
                if "properties" in def_val:
                    props = def_val["properties"]
                    items = def_val
                    break

        if not props:
            continue

        required = items.get("required", [])

        return {"properties": props, "required": required, "raw": schema}

    print(f"[ERROR] '{pipeline}'의 스키마를 GitHub에서 가져올 수 없습니다.")
    for url_tmpl in GITHUB_SCHEMA_URLS:
        print(f"  시도한 URL: {url_tmpl.format(org=org, name=name)}")
    sys.exit(1)


def display_schema_requirements(pipeline: str, schema: dict):
    """터미널에 파이프라인의 샘플 시트 요구사항을 예쁘게 출력한다."""
    props = schema["properties"]
    required = schema["required"]
    optional = [k for k in props if k not in required]

    print()
    print("=" * 60)
    print(f"📊 [{pipeline} 샘플 시트 요구사항]")
    print(f"- 필수 컬럼: {', '.join(required)}")
    if optional:
        print(f"- 선택 컬럼: {', '.join(optional)}")
    print("=" * 60)
    print()


# ══════════════════════════════════════════════════════════════
# 2. 파일 스캔 & 페어링
# ══════════════════════════════════════════════════════════════

def scan_fastqs(data_dir: str) -> List[str]:
    """디렉토리에서 .fastq.gz / .fq.gz 파일을 절대경로로 반환한다.

    Uses utils.file_discovery for recursive search, falls back to flat scan.
    """
    try:
        found = discover_files(data_dir, file_type="fastq")
        if found:
            return [f.path for f in found]
    except Exception:
        pass
    # Fallback: flat directory scan
    files = []
    for f in sorted(os.listdir(data_dir)):
        if f.endswith((".fastq.gz", ".fq.gz")):
            files.append(os.path.abspath(os.path.join(data_dir, f)))
    return files


def pair_files(files: List[str]) -> List[dict]:
    """파일 목록을 샘플 이름 기준으로 페어링한다."""
    samples: Dict[str, dict] = {}

    for fpath in files:
        fname = os.path.basename(fpath)
        m = RE_PAIR.match(fname)
        if m:
            sample_name = m.group(1)
            read_num = m.group(2)
        else:
            sample_name = fname.split(".")[0]
            read_num = "1"

        if sample_name not in samples:
            samples[sample_name] = {"sample": sample_name, "fastq_1": "", "fastq_2": ""}

        if read_num == "1":
            samples[sample_name]["fastq_1"] = fpath
        else:
            samples[sample_name]["fastq_2"] = fpath

    return list(samples.values())


# ══════════════════════════════════════════════════════════════
# 3. 스마트 인터랙티브 매핑 엔진
# ══════════════════════════════════════════════════════════════

def _is_auto_mappable(col_name: str) -> bool:
    """파일 스캔으로 자동 매핑 가능한 컬럼인지 판별."""
    return col_name in AUTO_FILE_COLUMNS or col_name in AUTO_INFER_COLUMNS


def _get_prompt_hint(col_name: str, col_schema: dict) -> Tuple[str, Optional[str]]:
    """컬럼의 enum/type 힌트와 기본값을 추출한다.

    Returns (hint_text, default_value_str)
    """
    enum_vals = col_schema.get("enum")
    default_val = col_schema.get("default")
    col_type = col_schema.get("type", "string")

    hints = []
    if enum_vals:
        hints.append(f"가능한 값: {', '.join(str(v) for v in enum_vals)}")
    elif col_type == "integer":
        hints.append("정수값")
    elif col_type == "number":
        hints.append("숫자값")

    default_str = None
    if default_val is not None:
        default_str = str(default_val)

    hint = " / ".join(hints) if hints else None
    return hint, default_str


def _cast_value(raw: str, col_schema: dict) -> Any:
    """사용자 입력값을 스키마 타입에 맞게 캐스팅한다."""
    col_type = col_schema.get("type", "string")
    if col_type == "integer":
        try:
            return int(raw)
        except ValueError:
            return raw
    if col_type == "number":
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def interactive_fill(rows: List[dict], schema: dict) -> List[dict]:
    """필수 컬럼 중 자동 추론 불가능한 것들을 인터랙티브하게 채운다."""
    props = schema["properties"]
    required = schema["required"]

    # 인터랙티브로 물어봐야 하는 필수 컬럼
    interactive_cols = [c for c in required if not _is_auto_mappable(c)]

    if not interactive_cols:
        return rows

    print("📝 자동 추론할 수 없는 필수 컬럼에 대해 값을 입력해 주세요.\n")

    for col in interactive_cols:
        col_schema = props.get(col, {})
        hint, default = _get_prompt_hint(col, col_schema)

        hint_str = f" ({hint})" if hint else ""
        default_str = f" [Enter → '{default}']" if default else ""

        # 첫 번째 샘플에 대해 물어보기
        first_sample = rows[0]["sample"]
        prompt = (
            f"  '{first_sample}'의 '{col}' 값을 입력하세요"
            f"{hint_str}{default_str}: "
        )
        answer = input(prompt).strip()
        if not answer and default:
            answer = default

        rows[0][col] = _cast_value(answer, col_schema)

        # 여러 샘플이면 일괄 적용 여부 확인
        if len(rows) > 1:
            apply_q = input(
                f"  → 나머지 {len(rows) - 1}개 샘플에도 "
                f"'{col}={answer}'를 동일 적용할까요? [Y/n]: "
            ).strip()

            if apply_q.lower() != "n":
                # 일괄 적용
                casted = _cast_value(answer, col_schema)
                for row in rows[1:]:
                    row[col] = casted
            else:
                # 개별 입력
                for row in rows[1:]:
                    p = (
                        f"  '{row['sample']}'의 '{col}' 값을 입력하세요"
                        f"{hint_str}{default_str}: "
                    )
                    val = input(p).strip()
                    if not val and default:
                        val = default
                    row[col] = _cast_value(val, col_schema)

    print()
    return rows


# ══════════════════════════════════════════════════════════════
# 4. Sarek 특화 로직 (Tumor/Normal 추론 & 검증)
# ══════════════════════════════════════════════════════════════

def infer_status(sample_name: str) -> Optional[int]:
    """파일명 키워드로 Tumor(1)/Normal(0)을 추론. 불확실하면 None.

    Delegates to shared utils.sample_inference for consistent logic.
    """
    result = infer_tumor_normal_status(sample_name)
    if result is not None:
        return result
    # Fallback to local regex patterns
    if TUMOR_KEYWORDS.search(sample_name):
        return 1
    if NORMAL_KEYWORDS.search(sample_name):
        return 0
    return None


def ask_status(sample_name: str) -> int:
    """추론 실패 시 유저에게 직접 물어본다."""
    while True:
        answer = input(
            f"  '{sample_name}'은(는) Tumor(1)인가요, Normal(0)인가요? [0/1]: "
        ).strip()
        if answer in ("0", "1"):
            return int(answer)
        print("  0 또는 1을 입력해 주세요.")


def infer_patient(sample_name: str) -> str:
    """샘플명에서 patient ID를 추론한다."""
    m = re.match(
        r"^(.+?)[-_](?:tumor|tumour|normal|blood|cancer|control|germline|"
        r"metastasis|met\d|relapse|recurrence|healthy|buffy|pbmc)",
        sample_name, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return sample_name


def enrich_for_sarek(rows: List[dict]) -> List[dict]:
    """Sarek용으로 patient, status, lane을 추론/주입한다."""
    print("[Sarek 특화] Tumor/Normal 상태를 자동 추론합니다.\n")

    for row in rows:
        name = row["sample"]

        status = infer_status(name)
        if status is not None:
            label = "Tumor" if status == 1 else "Normal"
            print(f"  ✅ '{name}' → {label} (status={status}) [자동 추론]")
        else:
            print(f"  ❓ '{name}' → 키워드로 추론 불가")
            status = ask_status(name)
            label = "Tumor" if status == 1 else "Normal"
            print(f"     → {label} (status={status}) [수동 지정]")

        row["status"] = status
        row["patient"] = infer_patient(name)
        row.setdefault("lane", "lane_1")

    print()
    return rows


def validate_sarek(rows: List[dict]):
    """Sarek 사전 검증: Tumor-Normal 페어 누락 경고."""
    patients: Dict[str, Dict[str, List[str]]] = {}
    for row in rows:
        pid = row.get("patient", "unknown")
        status = row.get("status")
        if pid not in patients:
            patients[pid] = {"tumor": [], "normal": []}
        if status == 1:
            patients[pid]["tumor"].append(row["sample"])
        elif status == 0:
            patients[pid]["normal"].append(row["sample"])

    has_warning = False
    for pid, groups in patients.items():
        if groups["tumor"] and not groups["normal"]:
            if not has_warning:
                print("-" * 60)
                has_warning = True
            print(
                f"  ⚠️  [경고] Patient '{pid}': Tumor 샘플은 있지만 Normal이 없습니다.\n"
                f"          Somatic variant calling은 Tumor-Normal 페어가 있을 때\n"
                f"          가장 정확합니다. Normal 샘플이 누락되었습니다."
            )
        if groups["normal"] and not groups["tumor"]:
            if not has_warning:
                print("-" * 60)
                has_warning = True
            print(
                f"  ℹ️  [참고] Patient '{pid}': Normal 샘플만 존재합니다.\n"
                f"          Germline-only 분석으로 진행됩니다."
            )
    if has_warning:
        print("-" * 60)
        print()


# ══════════════════════════════════════════════════════════════
# 5. CSV 생성 (메인 로직)
# ══════════════════════════════════════════════════════════════

def generate(pipeline: str, data_dir: str, output: str = "samplesheet.csv"):
    # 1) 스키마 동적 가져오기
    print(f"\n🔍 '{pipeline}' 스키마를 GitHub에서 가져오는 중...")
    schema = fetch_schema(pipeline)

    # 2) 요구사항 출력
    display_schema_requirements(pipeline, schema)

    props = schema["properties"]
    required = schema["required"]
    is_sarek = pipeline == "nf-core/sarek"

    # 3) 파일 스캔
    files = scan_fastqs(data_dir)
    if not files:
        print(f"[ERROR] '{data_dir}'에서 .fastq.gz / .fq.gz 파일을 찾을 수 없습니다.")
        sys.exit(1)

    print(f"📁 '{data_dir}'에서 {len(files)}개의 FASTQ 파일을 발견했습니다.")

    # 4) 페어링
    rows = pair_files(files)
    print(f"🧬 {len(rows)}개의 샘플로 페어링 완료.\n")

    # 5) Sarek 특화 OR 범용 인터랙티브 매핑
    if is_sarek:
        rows = enrich_for_sarek(rows)
        validate_sarek(rows)
    else:
        rows = interactive_fill(rows, schema)

    # 6) 헤더 결정: 필수 컬럼 우선 + 데이터가 존재하는 선택 컬럼
    headers = list(required)
    for col in props:
        if col not in headers:
            if any(row.get(col) for row in rows):
                headers.append(col)

    # 7) 기본값 적용 & CSV 쓰기
    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            for h in headers:
                if h not in row or row[h] is None or row[h] == "":
                    default = props.get(h, {}).get("default")
                    row[h] = default if default is not None else ""
            writer.writerow(row)

    print(f"✅ {output} 생성 완료 ({len(rows)}개 샘플)\n")

    # 8) 사후 검증 (utils.validators 사용)
    pipeline_short = pipeline.split("/")[-1] if "/" in pipeline else pipeline
    validation = validate_samplesheet(rows, pipeline_short)
    if not validation.valid:
        print("[WARN] 샘플시트 검증 문제 발견:")
        print(validation.summary())
    elif validation.warnings:
        print("[INFO] 검증 통과 (경고 있음):")
        print(validation.summary())

    # 미리보기
    with open(output) as f:
        for i, line in enumerate(f):
            if i >= len(rows) + 1:
                break
            print(line, end="")
    print()


def main():
    if len(sys.argv) < 3:
        print("Usage: python generate_samplesheet.py <pipeline_name> <data_folder_path>")
        print("예시:  python generate_samplesheet.py nf-core/rnaseq ./data")
        sys.exit(1)

    pipeline = sys.argv[1]
    data_dir = sys.argv[2]

    if not os.path.isdir(data_dir):
        print(f"[ERROR] 디렉토리가 존재하지 않습니다: {data_dir}")
        sys.exit(1)

    generate(pipeline, data_dir)


if __name__ == "__main__":
    main()
