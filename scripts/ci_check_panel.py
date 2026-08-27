"""GitHub Actions 가 구운 데이터를 커밋해도 되는지 판단합니다.

    python scripts/ci_check_panel.py before    갱신 전 상태를 GITHUB_OUTPUT 형식으로
    python scripts/ci_check_panel.py after     구운 결과를 검사 (문제가 있으면 exit 1)
    python scripts/ci_check_panel.py last      최종 거래일만 출력
    python scripts/ci_check_panel.py summary   실행 요약을 마크다운으로

워크플로에서 파이썬을 heredoc 으로 밀어 넣지 않고 이 파일로 뺀 이유는 두 가지입니다.
YAML 안의 heredoc 은 들여쓰기 한 칸에 깨지고, `${{ }}` 를 코드에 직접 붙이면
값이 그대로 소스에 삽입됩니다. 여기서는 환경변수로만 받습니다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / "docs" / "data" / "panel.json"
META = ROOT / "docs" / "data" / "meta.json"

MIN_ROWS = 11_000          # 1980년부터면 11,700행쯤 됩니다
TAIL_DAYS = 20             # 최근 이만큼에서 결측을 봅니다
MAX_TAIL_GAPS = 5


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_before() -> int:
    d = _load(PANEL)
    print(f"last={d['dates'][-1] if d else ''}")
    print(f"rows={len(d['dates']) if d else 0}")
    return 0


def cmd_last() -> int:
    d = _load(PANEL)
    if not d:
        print("")
        return 1
    print(d["dates"][-1])
    return 0


def cmd_after() -> int:
    d = _load(PANEL)
    if not d:
        print("panel.json 이 만들어지지 않았습니다.")
        return 1

    dates = d["dates"]
    rows = len(dates)
    before_rows = int(os.environ.get("BEFORE_ROWS") or 0)
    before_last = os.environ.get("BEFORE_LAST") or ""

    fail: list[str] = []

    if rows < MIN_ROWS:
        fail.append(f"행 수가 너무 적습니다: {rows:,} < {MIN_ROWS:,}")
    if before_rows and rows < before_rows:
        fail.append(f"행이 줄었습니다: {before_rows:,} → {rows:,}")
    if before_last and dates[-1] < before_last:
        fail.append(f"최종일이 과거로 갔습니다: {before_last} → {dates[-1]}")

    for key in ("nasdaq", "vix", "bond"):
        series = d.get(key) or []
        gaps = sum(1 for v in series[-TAIL_DAYS:] if v is None)
        if gaps > MAX_TAIL_GAPS:
            fail.append(f"{key}: 최근 {TAIL_DAYS}일 중 {gaps}일이 비어 있습니다")

    leader = d["leaderNames"][d["leaderIdx"][-1]]
    px = (d["tickers"].get(leader) or [None])[-1]
    if px is None:
        fail.append(f"현재 1위({leader}) 종가가 비어 있습니다")

    if fail:
        print("데이터 검사 실패 — 커밋하지 않습니다:")
        for f in fail:
            print(f"  - {f}")
        return 1

    moved = "" if dates[-1] == before_last else f"  (직전 {before_last or '없음'})"
    print(f"검사 통과: {rows:,}행 · {dates[0]} ~ {dates[-1]}{moved} · 1위 {leader} {px}")
    return 0


def cmd_summary() -> int:
    d, m = _load(PANEL), _load(META)
    print("### 시세 갱신 결과")
    if not d:
        print("- panel.json 이 없습니다. 앞 단계가 실패했는지 확인하세요.")
        return 0

    print(f"- 기간: `{d['dates'][0]}` ~ `{d['dates'][-1]}` ({len(d['dates']):,}일)")
    if m:
        print(f"- 현재 시총 1위: **{m['leaders'][-1]['ticker']}**")
        rank = m.get("live_ranking") or []
        if rank:
            top = ", ".join(f"{r['ticker']} ${r['market_cap'] / 1e12:.2f}T" for r in rank[:3])
            print(f"- 실시간 순위: {top}")
        else:
            print("- 실시간 시가총액을 받지 못해 큐레이션 표를 그대로 씁니다.")
    return 0


COMMANDS = {"before": cmd_before, "after": cmd_after,
            "last": cmd_last, "summary": cmd_summary}

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = COMMANDS.get(arg)
    if not fn:
        print(f"사용법: {Path(__file__).name} [{' | '.join(COMMANDS)}]")
        sys.exit(2)
    sys.exit(fn())
