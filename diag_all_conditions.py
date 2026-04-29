# diag_all_conditions.py
import os

base = os.path.dirname(os.path.abspath(__file__))
fpath = os.path.join(base, 'core', 'engine_cycle.py')

lines = open(fpath, 'r', encoding='utf-8').readlines()
print(f'총 {len(lines)}줄 - 청산 조건 전체 스캔')
print('='*70)

keywords = [
    'held_hours', 'profit_rate', 'pnl_pct', 'execute_sell',
    'SURGE', 'TIME-EXIT', 'stop_loss', 'take_profit',
    'trail', 'sl_price', 'tp_price', 'consecutive'
]

for i, line in enumerate(lines, start=1):
    stripped = line.rstrip()
    if any(k in stripped for k in keywords):
        print(f'L{i}: {stripped}')
