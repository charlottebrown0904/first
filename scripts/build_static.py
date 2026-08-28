"""GitHub Pages 용 정적 사이트를 docs/ 에 만듭니다.

    python scripts/build_static.py

Pages 는 정적 파일만 서빙하므로 Flask API 를 쓸 수 없습니다. 그래서 백테스트에
필요한 시세를 통째로 JSON 으로 굽고, 계산은 브라우저의 web/engine.js 가 합니다
(파이썬 엔진과 같은 규칙을 옮긴 것이며, scripts/verify_js_engine.py 로 두
구현이 같은 값을 내는지 확인합니다).

굽는 것:
    docs/data/panel.json    시세 전량 (약 0.7MB, gzip 후 0.2MB)
    docs/data/meta.json     출처·1위 이력·프리셋·기본 파라미터
    docs/data/optimization.json  탐색 결과 (있으면 복사)
"""
from __future__ import annotations

import json
import shutil
import sys
import os
import datetime as dt
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np

from src import datasource, engine, leaders
from src.config import Params, ROOT, RESULTS, SEARCH_GRID, TRAIN_END, TEST_START

DOCS = ROOT / "docs"
WEB = ROOT / "web"


def _num(v, sig: int = 10):
    """Round to `sig` significant digits; NaN becomes null.

    10 digits keeps the browser's results identical to Python's to well past
    any decimal a person will read, while cutting the payload roughly in half
    against full float repr.
    """
    if v is None or not np.isfinite(v):
        return None
    return float(f"{float(v):.{sig}g}")


def build_panel_json() -> dict:
    panel = datasource.build_panel()
    tickers = sorted({t for t in panel["leader"].unique() if t in panel.columns})

    names = list(dict.fromkeys(panel["leader"].tolist()))
    idx_of = {n: i for i, n in enumerate(names)}

    out = {
        "dates": [str(d.date()) for d in panel.index],
        "nasdaq": [_num(v) for v in panel["nasdaq"]],
        "vix": [_num(v) for v in panel["vix"]],
        "bond": [_num(v) for v in panel["bond"]],
        "vixProxy": [int(bool(v)) for v in panel["vix_is_proxy"]],
        "leaderNames": names,
        "leaderIdx": [idx_of[t] for t in panel["leader"]],
        "tickers": {t: [_num(v) for v in panel[t]] for t in tickers},
    }
    return out


def build_meta_json() -> dict:
    panel = datasource.build_panel()
    return {
        "provenance": datasource.provenance(),
        "leaders": leaders.load_timeline(),
        "live_ranking": leaders.live_ranking(),
        "defaults": Params().to_dict(),
        "presets": engine.preset_stats(panel),
        "grid": SEARCH_GRID,
        "train_end": TRAIN_END,
        "test_start": TEST_START,
        "panel_start": str(panel.index[0].date()),
        "panel_end": str(panel.index[-1].date()),
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "static": True,
    }


def main(fetch: bool = True) -> None:
    """fetch=False 는 방금 시세를 받아온 호출자용입니다 (app.py 의 /api/refresh).

    fetch=True 일 때 refresh_all 을 부르는 이유는 캐시 때문만이 아닙니다.
    실시간 시가총액 1위는 거기서만 갱신되는데, CI 러너에는 캐시가 없어
    이걸 건너뛰면 배포본의 순위표가 통째로 비어 버립니다.
    """
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data").mkdir(exist_ok=True)

    if fetch:
        print("· 시세 확인 (캐시가 최신이면 건너뜁니다)")
        datasource.refresh_all(quiet=True)
        live = leaders.load_timeline()[-1]
        print(f"  현재 1위 {live['ticker']}")

    print("· 시세 패널 굽는 중")
    panel_json = build_panel_json()
    pf = DOCS / "data" / "panel.json"
    pf.write_text(json.dumps(panel_json, separators=(",", ":")), encoding="utf-8")
    n_series = len(panel_json["tickers"]) + 3      # nasdaq, vix, bond
    print(f"  {pf.name}  {pf.stat().st_size/1e6:.2f} MB  "
          f"{len(panel_json['dates']):,}일 × {n_series}계열")

    print("· 메타데이터")
    mf = DOCS / "data" / "meta.json"
    mf.write_text(json.dumps(build_meta_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {mf.name}  {mf.stat().st_size/1e3:.0f} KB")

    opt = RESULTS / "optimization.json"
    if opt.exists():
        shutil.copy2(opt, DOCS / "data" / "optimization.json")
        print(f"  optimization.json  {opt.stat().st_size/1e3:.0f} KB")
    else:
        print("  ! optimization.json 없음 - '최적 매매법' 탭이 비어 보입니다")

    print("· 프론트엔드 복사")
    for name in ("index.html", "style.css", "app.js", "engine.js", "backend.js", "verify.html"):
        src = WEB / name
        if not src.exists():
            print(f"  ! {name} 없음")
            continue
        shutil.copy2(src, DOCS / name)
        print(f"  {name}  {src.stat().st_size/1e3:.0f} KB")

    # The published site carries its own proof that the browser engine agrees
    # with the Python one, so verify.html is never stale against the data.
    print("· 엔진 대조 기준값")
    from scripts.verify_js_engine import main as bake_ref
    bake_ref()

    # Jekyll would otherwise ignore files it does not recognise
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print(f"\n완료 → {DOCS}")
    print("배포는 .github/workflows/pages.yml 이 합니다 "
          "(Settings → Pages → Source: GitHub Actions).")


if __name__ == "__main__":
    main()
