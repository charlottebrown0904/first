"""온라인에서 모든 지표를 새로 받아 기록합니다.

작업 스케줄러/cron 에 걸어두면 됩니다. 미국장 마감 후(한국시간 아침)가 적당합니다.

    python scripts/update_data.py
"""
import sys, os, warnings, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

from src import datasource, leaders

if __name__ == "__main__":
    print(f"=== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 지표 갱신 ===")
    datasource.refresh_all(force=True, quiet=False)

    panel = datasource.build_panel(force=True)
    last = panel.index[-1]
    row = panel.loc[last]
    print(f"\n최종 종가 {last.date()}")
    print(f"  나스닥   {row['nasdaq']:>12,.2f}")
    print(f"  VIX      {row['vix']:>12,.2f}" + ("  (대용치)" if row["vix_is_proxy"] else ""))
    print(f"  1등주    {row['leader']:>6s} {row['leader_px']:>10,.2f}")
    print(f"  국채펀드 {row['bond']:>12,.2f}")

    tl = leaders.load_timeline()
    print(f"\n현재 시총 1위: {tl[-1]['ticker']} ({tl[-1]['from']} 부터)")
    for r in leaders.live_ranking()[:5]:
        print(f"  {r['ticker']:<6s} ${r['market_cap']/1e12:.2f}T  {r['name']}")
