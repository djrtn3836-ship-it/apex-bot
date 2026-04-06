# fix_ml_pred.py
with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# L1080 = index 1079 (0-based)
# _get_ml_prediction 함수의 try: 블록 찾기
func_start = None
for i, line in enumerate(lines):
    if '_get_ml_prediction' in line and 'def ' in line:
        func_start = i
        print(f"함수 발견: L{i+1}")
        break

if func_start is None:
    print("ERROR: _get_ml_prediction 함수를 찾지 못했습니다")
    exit(1)

# 함수 내부에서 잘못된 순서의 블록 탐지
# _ml_pred_data 최초 참조 위치 (할당 전 사용)
first_ref = None   # dashboard_state에서 _ml_pred_data 참조하는 줄
assign_line = None  # _ml_pred_data = { 할당하는 줄

for i in range(func_start, func_start + 100):
    if i >= len(lines):
        break
    stripped = lines[i].strip()
    # 할당보다 먼저 나오는 참조 탐지
    if '_ml_pred_data' in stripped and '= {' in stripped and assign_line is None:
        assign_line = i
        print(f"_ml_pred_data 할당 위치: L{i+1}")
    elif '_ml_pred_data' in stripped and assign_line is None and first_ref is None:
        first_ref = i
        print(f"_ml_pred_data 최초 참조 위치(할당 전): L{i+1} → {stripped}")

# 핵심 수정: if result: 블록 시작 찾아서 블록 전체 재작성
if_result_line = None
for i in range(func_start, func_start + 50):
    if i >= len(lines):
        break
    if lines[i].strip() == 'if result:':
        if_result_line = i
        print(f"if result: 위치: L{i+1}")
        break

if if_result_line is None:
    print("ERROR: 'if result:' 라인을 찾지 못했습니다")
    exit(1)

# if result: 블록의 들여쓰기 파악
indent = len(lines[if_result_line]) - len(lines[if_result_line].lstrip())
inner_indent = ' ' * (indent + 4)
block_indent = ' ' * indent

# if result: 블록 끝 찾기 (들여쓰기 기준)
block_end = if_result_line + 1
for i in range(if_result_line + 1, func_start + 100):
    if i >= len(lines):
        break
    line = lines[i]
    if line.strip() == '':
        block_end = i + 1
        continue
    cur_indent = len(line) - len(line.lstrip())
    if cur_indent <= indent and line.strip() not in ('', ):
        block_end = i
        break
    block_end = i + 1

print(f"if result: 블록 범위: L{if_result_line+1} ~ L{block_end}")

# 올바른 순서로 새 블록 작성
new_block = (
    f"{block_indent}if result:\n"
    f"{inner_indent}from monitoring.dashboard import dashboard_state\n"
    f"{inner_indent}from datetime import datetime\n"
    f"{inner_indent}# 1) 먼저 _ml_pred_data 초기화 (UnboundLocalError 방지)\n"
    f"{inner_indent}_sig  = result.get('signal', 'HOLD')\n"
    f"{inner_indent}_conf = result.get('confidence', 0.0)\n"
    f"{inner_indent}_bp   = result.get('buy_prob',  0.0)\n"
    f"{inner_indent}_sp   = result.get('sell_prob', 0.0)\n"
    f"{inner_indent}_ml_pred_data = {{\n"
    f"{inner_indent}    'signal':     _sig,\n"
    f"{inner_indent}    'confidence': round(float(_conf), 3),\n"
    f"{inner_indent}    'buy_prob':   round(float(_bp),   3),\n"
    f"{inner_indent}    'sell_prob':  round(float(_sp),   3),\n"
    f"{inner_indent}    'market':     market,\n"
    f"{inner_indent}}}\n"
    f"{inner_indent}# 2) 대시보드 상태 업데이트\n"
    f"{inner_indent}if 'ml_predictions' not in dashboard_state.signals:\n"
    f"{inner_indent}    dashboard_state.signals['ml_predictions'] = {{}}\n"
    f"{inner_indent}dashboard_state.signals['ml_predictions'][market] = {{\n"
    f"{inner_indent}    'signal':          result.get('signal'),\n"
    f"{inner_indent}    'confidence':      round(result.get('confidence', 0), 4),\n"
    f"{inner_indent}    'buy_prob':        round(result.get('buy_prob', 0), 4),\n"
    f"{inner_indent}    'hold_prob':       round(result.get('hold_prob', 0), 4),\n"
    f"{inner_indent}    'sell_prob':       round(result.get('sell_prob', 0), 4),\n"
    f"{inner_indent}    'model_agreement': round(result.get('model_agreement', 0), 4),\n"
    f"{inner_indent}    'inference_ms':    round(result.get('inference_ms', 0), 2),\n"
    f"{inner_indent}    'updated_at':      datetime.now().strftime('%H:%M:%S'),\n"
    f"{inner_indent}}}\n"
    f"{inner_indent}dashboard_state.signals['ml_predictions'][market] = _ml_pred_data\n"
    f"{inner_indent}dashboard_state.signals['ml_prediction']  = _ml_pred_data\n"
    f"{inner_indent}dashboard_state.signals['ml_last_updated'] = datetime.now().isoformat()\n"
    f"{inner_indent}dashboard_state.signals['ml_model_loaded'] = self._ml_predictor._is_loaded\n"
)

# 백업 후 교체
import shutil
shutil.copy('core/engine.py', 'core/engine.py.bak_mlpred')
print("백업 완료: core/engine.py.bak_mlpred")

new_lines = lines[:if_result_line] + [new_block] + lines[block_end:]

with open('core/engine.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"✅ 수정 완료: L{if_result_line+1}~L{block_end} → 재작성됨")
print("수정 내용: _ml_pred_data를 dashboard_state 참조 이전에 먼저 초기화")
