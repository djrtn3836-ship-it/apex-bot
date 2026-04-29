import pathlib, sqlite3
import numpy as np

ROOT = pathlib.Path(".")

# ══════════════════════════════════════════════════════════════
# TASK 1: ML 모델 파일 상태 확인
# ══════════════════════════════════════════════════════════════
print("=" * 60)
print("TASK 1: ML 모델 파일 상태")
print("=" * 60)
model_paths = [
    "models/saved/ppo/best_model.zip",
    "models/saved/mlp",
    "models/saved/tft",
    "models/saved/ensemble",
]
import os, time
for mp in model_paths:
    p = pathlib.Path(mp)
    if p.exists():
        if p.is_file():
            size = p.stat().st_size / 1024
            mtime = time.ctime(p.stat().st_mtime)
            print(f"  ✅ {mp} | {size:.1f}KB | 수정: {mtime}")
        else:
            files = list(p.rglob("*"))
            total = sum(f.stat().st_size for f in files if f.is_file()) / 1024
            print(f"  ✅ {mp}/ | {len(files)}개 파일 | 총 {total:.1f}KB")
    else:
        print(f"  ❌ {mp} 없음")

# ══════════════════════════════════════════════════════════════
# TASK 2: DB에서 ML 예측 결과 분포 분석
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TASK 2: DB trade_history ML 신호 분포")
print("=" * 60)
conn = sqlite3.connect("database/apex_bot.db")
cur = conn.cursor()

# 전체 BUY 트레이드의 strategy 분포
cur.execute("""
    SELECT strategy, COUNT(*) as cnt,
           ROUND(AVG(profit_rate), 3) as avg_profit,
           SUM(CASE WHEN profit_rate > 0.1 THEN 1 ELSE 0 END) as wins,
           SUM(CASE WHEN profit_rate < -0.1 THEN 1 ELSE 0 END) as losses
    FROM trade_history
    WHERE side = 'SELL'
    GROUP BY strategy
    ORDER BY cnt DESC
""")
rows = cur.fetchall()
print(f"  전략별 성과:")
for r in rows:
    total = r[3] + r[4]
    wr = r[3]/total*100 if total > 0 else 0
    print(f"  {str(r[0]):30s} | {r[1]:3d}건 | 평균={r[2]:+.3f}% | 승률={wr:.0f}%({r[3]}승/{r[4]}패)")

# reason 분포 (ML익절 vs 기본익절 vs 손절)
print("\n  매도 reason 분포:")
cur.execute("""
    SELECT
        CASE
            WHEN reason LIKE 'ML익절%' THEN 'ML익절'
            WHEN reason LIKE '기본익절%' THEN '기본익절'
            WHEN reason LIKE '기본손절%' THEN '기본손절'
            WHEN reason LIKE '%트레일링%' THEN '트레일링'
            WHEN reason LIKE 'BUY signal%' THEN 'BUY signal(오류)'
            ELSE reason
        END as reason_group,
        COUNT(*) as cnt,
        ROUND(AVG(profit_rate), 3) as avg_profit
    FROM trade_history
    WHERE side = 'SELL'
    GROUP BY reason_group
    ORDER BY cnt DESC
""")
for r in cur.fetchall():
    print(f"  {str(r[0]):25s} | {r[1]:3d}건 | 평균={r[2]:+.3f}%")

conn.close()

# ══════════════════════════════════════════════════════════════
# TASK 3: 최신 로그에서 ML confidence 분포 분석
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TASK 3: 로그 ML confidence 분포 분석")
print("=" * 60)
import re, glob
log_files = sorted(glob.glob("logs/apex_bot_*.log"))
if not log_files:
    print("  ❌ 로그 파일 없음")
else:
    log_file = log_files[-1]
    print(f"  로그: {log_file}")
    
    buy_confidences = []
    hold_confidences = []
    sell_confidences = []
    ppo_values = []
    
    pattern = re.compile(
        r"ML\+PPO.*?ML=(BUY|HOLD|SELL)\(([0-9.]+)\).*?PPO=(BUY|HOLD|SELL)\(([0-9.]+)\)"
    )
    
    with open(log_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                ml_sig, ml_conf, ppo_sig, ppo_conf = m.groups()
                conf = float(ml_conf)
                ppo  = float(ppo_conf)
                if ml_sig == "BUY":
                    buy_confidences.append(conf)
                elif ml_sig == "HOLD":
                    hold_confidences.append(conf)
                else:
                    sell_confidences.append(conf)
                ppo_values.append(ppo)
    
    total = len(buy_confidences) + len(hold_confidences) + len(sell_confidences)
    print(f"\n  ML 신호 분포 (총 {total}건):")
    print(f"  BUY  : {len(buy_confidences):4d}건 ({len(buy_confidences)/total*100:.1f}%)", end="")
    if buy_confidences:
        print(f" | conf 평균={np.mean(buy_confidences):.3f} min={min(buy_confidences):.3f} max={max(buy_confidences):.3f}")
    else:
        print()
    print(f"  HOLD : {len(hold_confidences):4d}건 ({len(hold_confidences)/total*100:.1f}%)", end="")
    if hold_confidences:
        print(f" | conf 평균={np.mean(hold_confidences):.3f} min={min(hold_confidences):.3f} max={max(hold_confidences):.3f}")
    else:
        print()
    print(f"  SELL : {len(sell_confidences):4d}건 ({len(sell_confidences)/total*100:.1f}%)", end="")
    if sell_confidences:
        print(f" | conf 평균={np.mean(sell_confidences):.3f} min={min(sell_confidences):.3f} max={max(sell_confidences):.3f}")
    else:
        print()
    
    if ppo_values:
        print(f"\n  PPO 값 분포:")
        print(f"  평균={np.mean(ppo_values):.3f} | min={min(ppo_values):.3f} | max={max(ppo_values):.3f}")
        unique_ppo = len(set(round(v,2) for v in ppo_values))
        print(f"  고유값 수: {unique_ppo}개 (적을수록 수렴 의심)")

    # confidence 히스토그램
    print(f"\n  ML confidence 히스토그램 (HOLD 기준):")
    bins = [0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 1.0]
    for i in range(len(bins)-1):
        cnt = sum(1 for c in hold_confidences if bins[i] <= c < bins[i+1])
        bar = "█" * (cnt // 2)
        print(f"  {bins[i]:.2f}~{bins[i+1]:.2f}: {cnt:4d}건 {bar}")

# ══════════════════════════════════════════════════════════════
# TASK 4: models/inference/predictor.py 핵심 구조 확인
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TASK 4: predictor.py predict() 핵심 로직")
print("=" * 60)
pred_path = pathlib.Path("models/inference/predictor.py")
if pred_path.exists():
    lines = pred_path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"  총 {len(lines)}줄")
    # predict 함수 찾기
    in_predict = False
    for i, line in enumerate(lines, 1):
        if "def predict" in line:
            in_predict = True
        if in_predict:
            print(f"  L{i}: {line.rstrip()}")
        if in_predict and i > 0 and line.strip() == "" and i > 10:
            # 빈 줄 연속 2개면 함수 끝
            pass
        if in_predict and "def " in line and "predict" not in line and i > 5:
            break
        if in_predict and i > 250:
            break
else:
    print("  ❌ predictor.py 없음")