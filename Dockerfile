FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Dependencias necesarias para Chromium (Playwright)
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libxshmfence1 \
    libxfixes3 \
    libgtk-3-0 \
    libdrm2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 🔥 ESTA ES LA LÍNEA CLAVE
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]