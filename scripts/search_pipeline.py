"""nf-core 파이프라인 키워드 검색 스크립트."""

import json
import sys
import urllib.request

API_URL = "https://nf-co.re/pipelines.json"


def fetch_pipelines():
    with urllib.request.urlopen(API_URL) as resp:
        return json.loads(resp.read().decode())["remote_workflows"]


def search(keyword: str):
    keyword = keyword.lower()
    results = []
    for p in fetch_pipelines():
        name = p.get("name") or ""
        desc = p.get("description") or ""
        if keyword in name.lower() or keyword in desc.lower():
            releases = p.get("releases", [])
            latest = releases[0]["tag_name"] if releases else "N/A"
            results.append((name, desc, latest))
    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python search_pipeline.py <keyword>")
        sys.exit(1)

    keyword = sys.argv[1]
    results = search(keyword)

    if not results:
        print(f"No pipelines found for '{keyword}'.")
        return

    # 표 형태 출력
    header = f"{'#':>3}  {'Pipeline':<30} {'Version':<12} Description"
    print(f"Found {len(results)} pipeline(s) for '{keyword}':\n")
    print(header)
    print("-" * len(header) + "-" * 30)
    for i, (name, desc, version) in enumerate(results, 1):
        desc_short = (desc[:60] + "...") if len(desc) > 63 else desc
        print(f"{i:>3}  nf-core/{name:<21} {version:<12} {desc_short}")

    # 인터랙티브 선택
    print()
    while True:
        choice = input("원하는 파이프라인 번호를 선택하세요 (종료: q): ").strip()
        if choice.lower() == "q":
            print("종료합니다.")
            break
        try:
            idx = int(choice)
            if idx < 1 or idx > len(results):
                raise ValueError
        except ValueError:
            print(f"1~{len(results)} 사이의 번호를 입력해 주세요.")
            continue

        name, desc, version = results[idx - 1]
        print(f"\n>>> 선택된 파이프라인: nf-core/{name}")
        print(f">>> 최신 버전: {version}")
        break


if __name__ == "__main__":
    main()
