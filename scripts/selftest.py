"""Correctness checks for the engine.  python scripts/selftest.py"""
import sys, os, warnings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

import numpy as np, pandas as pd
from src.datasource import build_panel
from src.engine import run, buy_and_hold, build_signals, _shift_signals
from src.config import Params

panel = build_panel()
fails = []
def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok: fails.append(name)

print("1. 미래 정보 누출 (truncation invariance)")
# Trades made before date D must not change when data after D is removed.
D = "2015-06-30"
p = Params()
full  = run(panel, p)
trunc = run(panel.loc[:D], p)
a = [t for t in full["trades"]  if t["date"] <= D]
b = [t for t in trunc["trades"] if t["date"] <= D]
common = min(len(a), len(b))
same = all(a[-common+i]["date"] == b[-common+i]["date"] for i in range(common)) if common else False
check("잘라낸 데이터로 돌려도 그 이전 매매가 동일", same,
      f"공통 {common}건 비교")

# equity on date D must match to the cent
ea, eb = float(full["_equity"].loc[:D].iloc[-1]), float(trunc["_equity"].iloc[-1])
check("D 시점 평가액 일치", abs(ea - eb) / max(ea, 1) < 1e-9, f"{ea:,.2f} vs {eb:,.2f}")

print("\n2. 신호 시프트")
sig = build_signals(panel, p)
sh  = _shift_signals(sig)
check("shock 이 정확히 하루 밀려 있음",
      bool((sh["shock"].iloc[1:].to_numpy() == sig["shock"].iloc[:-1].to_numpy()).all()))

print("\n3. 회계 항등식")
# with no shocks possible and no trim, the strategy IS buy&hold on the leader
flat = Params.from_dict({**p.to_dict(), "crash_threshold": -999., "vix_threshold": 0.,
                         "trim_threshold": -999., "trim_fraction": 0.})
r1 = run(panel, flat); r2 = buy_and_hold(panel, p, "leader")
check("충격 규칙을 끄면 1등주 계속보유와 동일",
      abs(r1["final_value"] - r2["final_value"]) / r2["final_value"] < 1e-9)

# contributions: 1 initial + one per month boundary
months = len(pd.Series(1, index=panel.index).resample("MS").first())
expect = p.initial_krw + p.monthly_krw * (months - 1)
check("납입 원금이 회차 수와 일치", abs(r1["total_contributed"] - expect) < 1,
      f"{r1['total_contributed']:,.0f} vs {expect:,.0f}")

print("\n4. 1등주 교체 처리")
# no single-day TWR move beyond what the underlying stock itself did
tw = r2["_twr"].pct_change().dropna()
lead = panel["leader"]
sw = lead[lead != lead.shift(1)].index[1:]
bad = [(str(d.date()), float(tw.get(d, 0))) for d in sw if abs(tw.get(d, 0)) > 0.15]
check("교체일에 가짜 손익 점프 없음", not bad, str(bad[:3]))

print("\n5. 회피자산 데이터 없는 구간")
p2 = Params.from_dict({**p.to_dict(), "gold_weight": 1.0})
r3 = run(panel, p2, None, "1985-12-31")
check("1986년 이전 금 비중이 0", float(r3["_w_gold"].max()) == 0.0,
      f"max gold weight {float(r3['_w_gold'].max()):.4f}")
check("그 구간에서도 회피가 동작(국채로)", float(r3["_w_bond"].max()) > 0.5,
      f"max bond weight {float(r3['_w_bond'].max()):.3f}")

print("\n6. 비중 합")
tot = (r1["_w_stock"] + r1["_w_gold"] + r1["_w_bond"])
check("주식+금+국채 = 1", bool(((tot - 1).abs() < 1e-6).all()),
      f"max dev {float((tot-1).abs().max()):.2e}")

print("\n" + ("모든 검사 통과" if not fails else f"실패 {len(fails)}건: {fails}"))
sys.exit(1 if fails else 0)
