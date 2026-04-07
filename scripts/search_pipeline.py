"""nf-core pipeline multi-keyword search script."""

import json
import sys
import urllib.request

API_URL = "https://nf-co.re/pipelines.json"
MAX_RESULTS = 10


def fetch_pipelines():
    with urllib.request.urlopen(API_URL) as resp:
        return json.loads(resp.read().decode())["remote_workflows"]


def search(keywords):
    keywords = [k.lower() for k in keywords]
    scored = []
    for p in fetch_pipelines():
        name = (p.get("name") or "").lower()
        desc = (p.get("description") or "").lower()
        topics = [t.lower() for t in (p.get("topics") or [])]
        searchable = name + " " + desc + " " + " ".join(topics)

        score = sum(1 for kw in keywords if kw in searchable)
        if score == 0:
            continue

        releases = p.get("releases", [])
        latest = releases[0]["tag_name"] if releases else "N/A"
        scored.append((score, p.get("name", ""), p.get("description", ""), latest))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:MAX_RESULTS]


def main():
    if len(sys.argv) < 2:
        print("Usage: python search_pipeline.py <keyword1> [keyword2] [keyword3] ...")
        sys.exit(1)

    keywords = sys.argv[1:]
    results = search(keywords)

    if not results:
        print(f"No pipelines found for: {' '.join(keywords)}")
        sys.exit(0)

    header = f"{'#':>3}  {'Score':>5}  {'Pipeline':<30} {'Version':<12} Description"
    print(f"Found {len(results)} pipeline(s) for keywords: {' '.join(keywords)}\n")
    print(header)
    print("-" * len(header) + "-" * 20)
    for i, (score, name, desc, version) in enumerate(results, 1):
        desc_short = (desc[:55] + "...") if len(desc) > 58 else desc
        print(f"{i:>3}  {score:>5}  nf-core/{name:<21} {version:<12} {desc_short}")

    sys.exit(0)


if __name__ == "__main__":
    main()
