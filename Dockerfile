# 碎片連鎖計算核心（/game）：Rust 單機版的 debris_engine.rs，原始碼由 tools/sync_debris.py 複製到 rust/debris_cli/。
# 用 bookworm 編譯，glibc 較舊，在 python:3.11-slim（bookworm 或更新）上都能執行。
FROM rust:1-slim-bookworm AS debris
WORKDIR /src
COPY rust/debris_cli/ ./
RUN cargo build --release --locked

FROM python:3.11-slim

WORKDIR /app

# fonts-noto-cjk：/api/rpo/<p>/<s>/report.png 中文版需 CJK 字型（缺字型時自動退回英文版）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=debris /src/target/release/debris_cli /app/bin/debris_cli

# 讓 settings.py 在 /app 下找到 overpass_cats.yaml、Logo_ATRDC.png 等共用資料檔
ENV ATRDC_BASE_DIR=/app
ENV HOST=0.0.0.0
ENV PORT=5013

EXPOSE 5013

CMD ["python", "run.py"]
