# ============================================================
# APEX BOT Dockerfile
# CUDA 12.4 + Python 3.11 (RTX 5060 지원)
# ============================================================
FROM pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime

WORKDIR /app

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ git curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# TA-Lib 설치 (기술지표 라이브러리)
RUN curl -L https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz | tar xz \
    && cd ta-lib && ./configure --prefix=/usr && make && make install \
    && cd .. && rm -rf ta-lib

# Python 패키지
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# 비루트 사용자
RUN useradd -m -u 1000 apexbot
RUN chown -R apexbot:apexbot /app
USER apexbot

# 필요 디렉토리 생성
RUN mkdir -p /app/database /app/logs /app/models/saved

EXPOSE 8888

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8888/api/health || exit 1

CMD ["python", "main.py", "--mode", "paper"]
