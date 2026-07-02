FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 讓 settings.py 在 /app 下找到 overpass_cats.yaml、Logo_ATRDC.png 等共用資料檔
ENV ATRDC_BASE_DIR=/app
ENV HOST=0.0.0.0
ENV PORT=5013

EXPOSE 5013

CMD ["python", "run.py"]
