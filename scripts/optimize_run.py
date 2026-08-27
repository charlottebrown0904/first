"""Run the parameter search.  python scripts/optimize_run.py [n_random]"""
import sys, os, time, warnings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.optimize import search

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    t0 = time.perf_counter()
    out = search(n_random=n)
    print(f"\n완료: {time.perf_counter()-t0:.0f}초")

    b = out["benchmarks"]
    print("\n── 벤치마크 (전체구간) " + "─" * 40)
    for v in b.values():
        f = v.get("full", {})
        print(f"  {v['label']:16s} CAGR {f.get('cagr',0)*100:6.2f}%   "
              f"MDD {f.get('mdd',0)*100:7.2f}%   최종 {f.get('final_value',0)/1e8:9.1f}억")

    print("\n── 목표별 최적 규칙 " + "─" * 43)
    names = {"max_return": "최고 수익률", "balanced": "균형 (칼마 최대)", "min_loss": "최소 손실"}
    for k, v in out.get("picks", {}).items():
        print(f"\n  [{names.get(k,k)}]")
        print("    " + "  ".join(f"{a}={bv}" for a, bv in v["params"].items()
                                 if a not in ("initial_krw", "monthly_krw", "cost_bps")))
        for w in ("train", "test", "full"):
            m = v.get(w) or {}
            if not m: continue
            print(f"    {w:5s} CAGR {m.get('cagr',0)*100:6.2f}%  MDD {m.get('mdd',0)*100:7.2f}%  "
                  f"칼마 {m.get('calmar',0):5.2f}  회피 {m.get('pct_days_sheltered',0)*100:4.1f}%  "
                  f"최종 {m.get('final_value',0)/1e8:8.1f}억")

    print("\n── 검증구간까지 살아남은 상위 규칙 " + "─" * 28)
    print(f"{'#':>2} {'학습CAGR':>8} {'학습MDD':>8} {'검증CAGR':>8} {'검증MDD':>8} "
          f"{'전체CAGR':>8} {'전체MDD':>8} {'회피%':>6} {'안정성':>7} {'생존':>4}")
    for i, r in enumerate(out["results"][:15], 1):
        tr, te, fu = r["train"], r["test"], r["full"]
        print(f"{i:>2} {tr.get('cagr',0)*100:7.2f}% {tr.get('mdd',0)*100:7.1f}% "
              f"{te.get('cagr',0)*100:7.2f}% {te.get('mdd',0)*100:7.1f}% "
              f"{fu.get('cagr',0)*100:7.2f}% {fu.get('mdd',0)*100:7.1f}% "
              f"{fu.get('pct_days_sheltered',0)*100:5.1f}% {r.get('robustness') or 0:7.3f} "
              f"{'O' if r['holds_up'] else 'X':>4}")
    print(f"\n파레토 프론티어 {len(out.get('frontier',[]))}개 규칙")
