@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║        ⚡  APEX BOT v2.0.0 - 전체 자동 설치 스크립트  ⚡  ║
echo ║                                                          ║
echo ║  포함 항목:                                               ║
echo ║   ✅ 핵심 패키지 (pandas, numpy, pyupbit ...)            ║
echo ║   ✅ ML 앙상블 (scikit-learn, xgboost, lightgbm)         ║
echo ║   ✅ Walk-Forward 최적화 (optuna)                         ║
echo ║   ✅ PPO 강화학습 (gymnasium, stable-baselines3)          ║
echo ║   ✅ 뉴스 감성 분석 (nltk, transformers)                  ║
echo ║   ✅ PyTorch GPU (RTX 자동 감지 + CUDA 버전 자동 선택)    ║
echo ║   ✅ 대시보드, 텔레그램, 스케줄러 등                      ║
echo ╚══════════════════════════════════════════════════════════╝
echo.
echo [설치 시작] 약 5~15분 소요 (GPU PyTorch ~2GB)
echo.

:: ──────────────────────────────────────────────────────────────
:: Step 0: Python 버전 확인
:: ──────────────────────────────────────────────────────────────
echo [0/9] Python 버전 확인...
python --version 2>nul
if %errorlevel% neq 0 (
    echo ❌ Python이 설치되지 않았거나 PATH에 없습니다.
    echo    https://python.org 에서 Python 3.12 설치 후 재실행하세요.
    pause
    exit /b 1
)

python -c "import sys; v=sys.version_info; exit(0 if v.major==3 and v.minor>=10 else 1)"
if %errorlevel% neq 0 (
    echo ❌ Python 3.10 이상이 필요합니다. 현재 버전을 업그레이드하세요.
    pause
    exit /b 1
)
echo [OK] Python 버전 확인 완료

:: ──────────────────────────────────────────────────────────────
:: Step 1: pip 업그레이드
:: ──────────────────────────────────────────────────────────────
echo.
echo [1/9] pip 업그레이드...
python -m pip install --upgrade pip setuptools wheel --quiet
if %errorlevel% neq 0 (
    echo ❌ pip 업그레이드 실패
    pause
    exit /b 1
)
echo [OK] pip 업그레이드 완료

:: ──────────────────────────────────────────────────────────────
:: Step 2: 핵심 패키지 설치
:: ──────────────────────────────────────────────────────────────
echo.
echo [2/9] 핵심 패키지 설치 중... (비동기, 업비트, 데이터, DB, 웹)
pip install --quiet ^
  asyncio-throttle aiohttp aiofiles websockets httpx requests tenacity ^
  pyupbit "python-jose[cryptography]" PyJWT ^
  "pandas>=2.2.2" "numpy>=1.26.4" scipy polars pyarrow ^
  pandas-ta ta ^
  sqlalchemy aiosqlite alembic ^
  cachetools ^
  fastapi "uvicorn[standard]" jinja2 python-multipart ^
  python-telegram-bot loguru prometheus-client apscheduler ^
  python-dotenv pydantic pydantic-settings PyYAML ^
  colorama rich tqdm pytz psutil python-dateutil ^
  plotly matplotlib seaborn ^
  httpx lxml ^
  pytest pytest-asyncio pytest-cov
if %errorlevel% neq 0 (
    echo ❌ 핵심 패키지 설치 실패 (인터넷 연결 확인 후 재시도)
    pause
    exit /b 1
)
echo [OK] 핵심 패키지 설치 완료

:: ──────────────────────────────────────────────────────────────
:: Step 3: ML 앙상블 패키지
:: ──────────────────────────────────────────────────────────────
echo.
echo [3/9] ML 앙상블 패키지 설치 중... (scikit-learn, xgboost, lightgbm)
pip install --quiet scikit-learn xgboost lightgbm
if %errorlevel% neq 0 (
    echo [WARN] ML 패키지 일부 설치 실패 - 계속 진행
) else (
    echo [OK] ML 앙상블 패키지 완료
)

:: ──────────────────────────────────────────────────────────────
:: Step 4: Walk-Forward 최적화 (Optuna)
:: ──────────────────────────────────────────────────────────────
echo.
echo [4/9] Walk-Forward 최적화 패키지 (optuna) 설치 중...
pip install --quiet "optuna>=3.6.1"
if %errorlevel% neq 0 (
    echo [WARN] optuna 설치 실패 - 그리드서치 모드로 대체 작동
) else (
    echo [OK] Optuna Bayesian 최적화 완료
)

:: ──────────────────────────────────────────────────────────────
:: Step 5: PPO 강화학습 (gymnasium + stable-baselines3)
:: ──────────────────────────────────────────────────────────────
echo.
echo [5/9] PPO 강화학습 패키지 설치 중... (gymnasium, stable-baselines3)
echo       (시간이 조금 걸립니다 ~200MB)
pip install --quiet "gymnasium>=0.29.1" "stable-baselines3>=2.3.2" shimmy
if %errorlevel% neq 0 (
    echo [WARN] RL 패키지 설치 실패 - PPO 기능 비활성화 (나중에 수동 설치 가능)
    echo        수동: pip install gymnasium stable-baselines3
) else (
    echo [OK] PPO 강화학습 패키지 완료
)

:: ──────────────────────────────────────────────────────────────
:: Step 6: 뉴스 감성 분석 (NLTK, transformers)
:: ──────────────────────────────────────────────────────────────
echo.
echo [6/9] 뉴스 감성 분석 패키지 설치 중... (nltk, transformers)
pip install --quiet "nltk>=3.8.1" "transformers>=4.41.2"
if %errorlevel% neq 0 (
    echo [WARN] NLP 패키지 설치 실패 - 키워드 규칙 기반으로 대체 작동
) else (
    echo [OK] NLTK + Transformers 설치 완료
    :: VADER 사전 자동 다운로드
    python -c "import nltk; nltk.download('vader_lexicon', quiet=True); print('[OK] VADER 사전 다운로드')" 2>nul
)

:: ──────────────────────────────────────────────────────────────
:: Step 7: GPU 감지 + PyTorch 설치
:: ──────────────────────────────────────────────────────────────
echo.
echo [7/9] GPU 감지 중...
echo.

:: nvidia-smi로 GPU 이름 확인
python -c "import subprocess,sys; r=subprocess.run(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],capture_output=True,text=True); print(r.stdout.strip() if r.returncode==0 else 'NO_GPU')" > %TEMP%\apex_gpu.txt 2>nul
set /p GPU_NAME=<%TEMP%\apex_gpu.txt

if "%GPU_NAME%"=="NO_GPU" goto NO_GPU_FOUND
if "%GPU_NAME%"=="" goto NO_GPU_FOUND

echo [감지] GPU: %GPU_NAME%
echo.

:: ─────── RTX 50xx (Blackwell, CUDA 12.8 Nightly) ─────────────
echo %GPU_NAME% | findstr /C:"RTX 50" /C:"5060" /C:"5070" /C:"5080" /C:"5090" /C:"5050" >nul 2>&1
if %errorlevel%==0 (
    echo [RTX 50xx Blackwell] CUDA 12.8 Nightly PyTorch 설치 중...
    echo 참고: Blackwell 아키텍처는 Nightly 빌드 필요 (~2GB, 3-5분)
    pip install --pre torch torchvision torchaudio ^
        --index-url https://download.pytorch.org/whl/nightly/cu128 --quiet
    if !errorlevel! equ 0 (
        echo [OK] RTX 50xx PyTorch cu128 Nightly 설치 완료
        goto TORCH_DONE
    )
    echo [WARN] Nightly 실패 → cu124 Stable 폴백...
)

:: ─────── RTX 40xx (Ada Lovelace, CUDA 12.4) ──────────────────
echo %GPU_NAME% | findstr /C:"RTX 40" /C:"4060" /C:"4070" /C:"4080" /C:"4090" /C:"4050" >nul 2>&1
if %errorlevel%==0 (
    echo [RTX 40xx] CUDA 12.4 PyTorch 설치 중...
    pip install torch torchvision torchaudio ^
        --index-url https://download.pytorch.org/whl/cu124 --quiet
    if !errorlevel! equ 0 (
        echo [OK] RTX 40xx PyTorch cu124 설치 완료
        goto TORCH_DONE
    )
    echo [WARN] cu124 실패 → cu121 폴백...
)

:: ─────── RTX 30xx (Ampere, CUDA 12.1) ────────────────────────
echo %GPU_NAME% | findstr /C:"RTX 30" /C:"3060" /C:"3070" /C:"3080" /C:"3090" /C:"3050" >nul 2>&1
if %errorlevel%==0 (
    echo [RTX 30xx] CUDA 12.1 PyTorch 설치 중...
    pip install torch torchvision torchaudio ^
        --index-url https://download.pytorch.org/whl/cu121 --quiet
    if !errorlevel! equ 0 (
        echo [OK] RTX 30xx PyTorch cu121 설치 완료
        goto TORCH_DONE
    )
)

:: ─────── 기타 NVIDIA (기본 CUDA 12.4) ───────────────────────
echo [기타 NVIDIA GPU] CUDA 12.4 PyTorch 설치 중...
pip install torch torchvision torchaudio ^
    --index-url https://download.pytorch.org/whl/cu124 --quiet
if %errorlevel% equ 0 (
    echo [OK] PyTorch CUDA 12.4 설치 완료
    goto TORCH_DONE
)
echo [WARN] CUDA PyTorch 실패 → CPU 버전 폴백
goto CPU_TORCH

:NO_GPU_FOUND
echo [INFO] NVIDIA GPU 미감지 → CPU 전용 PyTorch 설치

:CPU_TORCH
echo [CPU] PyTorch CPU 버전 설치 중...
pip install torch torchvision torchaudio --quiet
if %errorlevel% equ 0 (
    echo [OK] PyTorch CPU 설치 완료
) else (
    echo [WARN] PyTorch 설치 실패 - ML/PPO 기능 비활성화
)

:TORCH_DONE

:: ──────────────────────────────────────────────────────────────
:: Step 8: TA-Lib (선택사항)
:: ──────────────────────────────────────────────────────────────
echo.
echo [8/9] TA-Lib 설치 시도 중... (실패해도 pandas-ta로 대체 작동)
pip install TA-Lib --quiet 2>nul
if %errorlevel% neq 0 (
    echo [SKIP] TA-Lib 자동 설치 불가 (선택사항 - 없어도 정상 작동)
    echo        수동 설치: https://github.com/cgohlke/talib-build/releases
) else (
    echo [OK] TA-Lib 설치 완료
)

:: ──────────────────────────────────────────────────────────────
:: Step 9: 설치 검증 + 자동 초기화
:: ──────────────────────────────────────────────────────────────
echo.
echo [9/9] 설치 검증 및 초기화 중...
echo.

python -c "import pandas, numpy, pyupbit, loguru, fastapi, apscheduler; print('  [OK] 핵심 패키지')" 2>nul || echo "  [FAIL] 핵심 패키지 일부 문제"
python -c "import sklearn, xgboost, lightgbm; print('  [OK] ML 앙상블 패키지')" 2>nul || echo "  [SKIP] ML 패키지"
python -c "import optuna; print(f'  [OK] Optuna {optuna.__version__} (Walk-Forward 최적화)')" 2>nul || echo "  [SKIP] Optuna"
python -c "import gymnasium; print(f'  [OK] Gymnasium {gymnasium.__version__} (PPO 강화학습)')" 2>nul || echo "  [SKIP] gymnasium"
python -c "import stable_baselines3; print(f'  [OK] stable-baselines3 {stable_baselines3.__version__} (PPO)')" 2>nul || echo "  [SKIP] stable-baselines3"
python -c "import nltk; print(f'  [OK] NLTK {nltk.__version__} (뉴스 감성 분석)')" 2>nul || echo "  [SKIP] nltk"
python -c "import transformers; print(f'  [OK] Transformers {transformers.__version__} (FinBERT)')" 2>nul || echo "  [SKIP] transformers"
python -c "import pandas_ta; print(f'  [OK] pandas-ta (기술지표)')" 2>nul || echo "  [SKIP] pandas-ta"

echo.
echo [PyTorch / GPU 상태]
python -c "
import torch
v = torch.__version__
cuda = torch.cuda.is_available()
if cuda:
    gpu = torch.cuda.get_device_name(0)
    mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f'  [OK] PyTorch {v} | CUDA: {cuda} | GPU: {gpu} | VRAM: {mem:.1f}GB')
else:
    print(f'  [OK] PyTorch {v} | CPU 모드 (GPU 없음 또는 CUDA 미지원)')
" 2>nul || echo "  [SKIP] PyTorch"

echo.
echo [NLTK 데이터 다운로드]
python -c "
import nltk
nltk.download('vader_lexicon', quiet=True)
nltk.download('punkt', quiet=True)
nltk.download('stopwords', quiet=True)
print('  [OK] VADER / punkt / stopwords 다운로드')
" 2>nul || echo "  [SKIP] NLTK 데이터 (뉴스 분석 시 자동 재시도)"

echo.
echo [GPU 설정 최적화]
python -c "
import os
os.environ.setdefault('UPBIT_ACCESS_KEY','test')
os.environ.setdefault('UPBIT_SECRET_KEY','test')
try:
    from utils.gpu_utils import setup_gpu, log_gpu_status
    dev = setup_gpu(use_gpu=True, benchmark=True, tf32=True)
    if dev == 'cuda':
        log_gpu_status()
        print(f'  [OK] GPU 최적화 완료: {dev}')
    else:
        print(f'  [INFO] CPU 모드')
except Exception as e:
    print(f'  [SKIP] GPU 초기화: {e}')
" 2>nul

echo.
echo [PPO 의존성 확인]
python -c "
try:
    from models.rl.ppo_agent import check_ppo_dependencies
    import os; os.environ.setdefault('UPBIT_ACCESS_KEY','test'); os.environ.setdefault('UPBIT_SECRET_KEY','test')
    deps = check_ppo_dependencies()
    ok = all(deps.values())
    for k,v in deps.items():
        print(f'  {'[OK]' if v else '[SKIP]'} {k}')
    if ok:
        print('  → PPO 강화학습 완전 활성화 ✅')
    else:
        print('  → PPO 일부 기능 비활성화 (핵심 기능에는 영향 없음)')
except: pass
" 2>nul

:: ──────────────────────────────────────────────────────────────
:: 완료 안내
:: ──────────────────────────────────────────────────────────────
echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║                    ✅  설치 완료!                         ║
echo ╠══════════════════════════════════════════════════════════╣
echo ║  다음 단계:                                               ║
echo ║                                                          ║
echo ║  1. 초기 설정 (최초 1회)                                  ║
echo ║     python main.py --setup                               ║
echo ║     → .env 파일 생성 후 API 키 입력                       ║
echo ║                                                          ║
echo ║  2. 페이퍼 트레이딩 시작 (추천)                           ║
echo ║     python main.py --mode paper                          ║
echo ║     → 자동 실행 목록:                                     ║
echo ║       • 전략 신호 생성 (8개 전략 + ML 앙상블)             ║
echo ║       • PPO 강화학습 자동 훈련 (시작 10분 후)             ║
echo ║       • Walk-Forward 파라미터 최적화 (시작 30분 후)       ║
echo ║       • 뉴스 감성 분석 (30분마다)                         ║
echo ║       • 공포탐욕/김치프리미엄 모니터링 (자동)             ║
echo ║       • 24시간마다 성과 리포트 자동 생성                  ║
echo ║                                                          ║
echo ║  3. 성과 리포트 확인                                      ║
echo ║     python main.py --mode report                         ║
echo ║                                                          ║
echo ║  4. GPU 상태 확인                                         ║
echo ║     python main.py --gpu-check                           ║
echo ║                                                          ║
echo ║  [자동 실행 스케줄 전체 목록]                             ║
echo ║    매  1분 : 가격 업데이트                                ║
echo ║    매 30분 : 뉴스 감성 갱신                               ║
echo ║    매  1시간: 공포탐욕 지수 갱신                          ║
echo ║    매  6시간: 김치 프리미엄 갱신                          ║
echo ║    매 24시간: 성과 리포트 + ML 재학습                     ║
echo ║    매주 월 02:00: Walk-Forward 최적화                     ║
echo ║    매주 월 03:00: PPO 강화학습 재훈련                     ║
echo ╚══════════════════════════════════════════════════════════╝
echo.
pause
