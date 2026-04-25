import pathlib, subprocess

ROOT = pathlib.Path(".")
deleted = []
skipped = []

def safe_delete(fp):
    p = pathlib.Path(fp)
    if p.exists():
        size = p.stat().st_size
        p.unlink()
        deleted.append((fp, size))
        print(f"  ✅ 삭제: {fp} ({size/1024:.1f}KB)")
    else:
        print(f"  -- 없음: {fp}")

print("=" * 55)
print("[1] .bak 백업 파일")
print("=" * 55)
safe_delete("core/engine_cycle.py.bak_20260419_2325")
safe_delete("core/engine_cycle.py.bak_20260420_1841")
safe_delete("core/engine_schedule.py.bak_20260419_2325")

print()
print("=" * 55)
print("[2] ML 모델 백업 4개 (각 47MB)")
print("=" * 55)
safe_delete("models/saved/ensemble_best_backup_20260421_030357.pt")
safe_delete("models/saved/ensemble_best_backup_20260422_205051.pt")
safe_delete("models/saved/ensemble_best_backup_20260423_061337.pt")
safe_delete("models/saved/ensemble_best_backup_20260425_030349.pt")

print()
print("=" * 55)
print("[3] 루트 임시 파일")
print("=" * 55)
safe_delete("fix_log_restore.py")
safe_delete("proc_watch.log")
safe_delete("project_structure.txt")
safe_delete("hold 0.45-0.35, ML-BLOCK 0.42-0.65")

print()
print("=" * 55)
print("[4] 빈 로그 파일 (0B)")
print("=" * 55)
for fn in [
    "logs/error_2026-04-15.log",
    "logs/error_2026-04-16.log",
    "logs/error_2026-04-17.log",
    "logs/trades_2026-03.log",
    "logs/train_v3.log",
    "logs/train_v3_err.log",
    "logs/train_v4.log",
    "logs/train_wait.log",
]:
    safe_delete(fn)

print()
print("=" * 55)
print("[5] zip 있는 구버전 로그")
print("=" * 55)
safe_delete("logs/apex_bot_2026-04-03.log")
safe_delete("logs/apex_bot_2026-04-04.log")

print()
print("=" * 55)
print("[6] debug 임시 로그")
print("=" * 55)
safe_delete("logs/debug_run.txt")
safe_delete("logs/debug_run2.txt")
safe_delete("logs/start_output.txt")

print()
print("=" * 55)
print("[7] 오래된 DB 백업 (04-03, 04-04)")
print("=" * 55)
safe_delete("database/apex_bot_backup_20260403_1915.db")
safe_delete("database/apex_bot_backup_20260404_1822.db")
# 04-22 최신 백업은 유지
print("  -- 유지: database/apex_bot_backup_20260422_183147.db (최신)")

print()
print("=" * 55)
print("[8] PPO 중복 모델")
print("=" * 55)
safe_delete("models/saved/ppo/ppo_trading.zip")
print("  -- 유지: models/saved/ppo/best_model.zip")

# 총 절약 용량
print()
print("=" * 55)
total_bytes = sum(size for _, size in deleted)
print(f"  삭제 완료: {len(deleted)}개 파일")
print(f"  절약 용량: {total_bytes/1024/1024:.1f} MB")

# git에서도 제거
print()
print("=" * 55)
print("git 정리")
print("=" * 55)
subprocess.run(["git", "add", "-A"], capture_output=True)
cr = subprocess.run(
    ["git", "commit", "-m",
     "chore: 불필요 파일 정리 — bak/ML백업/빈로그/임시파일/구버전DB 삭제 (~190MB)"],
    capture_output=True, text=True, encoding="utf-8", errors="replace"
)
ok = cr.returncode == 0
print(f"  git commit: {'OK ✅' if ok else 'NG'} {cr.stdout.strip()[:80] if ok else cr.stderr.strip()[:60]}")

if ok:
    pr = subprocess.run(
        ["git", "push", "origin", "main"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    print(f"  git push:   {'OK ✅' if pr.returncode==0 else 'NG'}")
