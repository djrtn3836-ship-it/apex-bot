"""
APEX BOT 완전 패치 마스터 실행기
실행 방법: python master_patch.py
"""
import sys, pathlib, datetime, importlib.util

def run_patch(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.patch()

PATCHES = [
    ("patch_01", "patch_01_predictor.py"),
    ("patch_02", "patch_02_signal_combiner.py"),
    ("patch_03", "patch_03_ensemble_engine.py"),
    ("patch_04", "patch_04_engine_sell.py"),
    ("patch_05", "patch_05_v2_layer.py"),
    ("patch_06", "patch_06_engine_buy.py"),
    ("patch_07", "patch_07_signal_combiner_dataclass.py"),
]

def main():
    print("=" * 60)
    print("  APEX BOT 완전 패치 마스터 실행기")
    print(f"  실행 시각: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 60)
    
    results = {}
    for name, path in PATCHES:
        p = pathlib.Path(path)
        if not p.exists():
            print(f"\n[MISSING] {path} 파일 없음 — 스킵")
            results[name] = "MISSING"
            continue
        print(f"\n{'─'*40}")
        print(f"  {name}: {path}")
        print(f"{'─'*40}")
        try:
            r = run_patch(name, path)
            results[name] = "OK" if r else "SKIP"
        except Exception as e:
            print(f"[FAIL] {e}")
            results[name] = "FAIL"
    
    print("\n" + "=" * 60)
    print("  패치 결과 요약")
    print("=" * 60)
    ok    = sum(1 for v in results.values() if v == "OK")
    skip  = sum(1 for v in results.values() if v == "SKIP")
    fail  = sum(1 for v in results.values() if v in ("FAIL", "MISSING"))
    for name, status in results.items():
        icon = "✅" if status == "OK" else "⏭" if status == "SKIP" else "❌"
        print(f"  {icon} {name}: {status}")
    print(f"\n  OK={ok} SKIP={skip} FAIL={fail}")
    
    if fail > 0:
        print("\n  ⚠️  FAIL 항목은 수동 검토 필요")
        sys.exit(1)
    
    print("\n  ✅ 모든 패치 완료 — 다음 단계:")
    print("  1. python main.py --mode paper 로 재시작")
    print("  2. 6~12시간 후 로그에서 BUY 신호 통과 여부 확인")
    print("  3. Temperature 1.5 적용 후 ML 신호 분포 재확인")

if __name__ == "__main__":
    main()
