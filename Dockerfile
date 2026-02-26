FROM python:3.11-slim

# Evita buffering y mejora logs en Render
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Instalar dependencias necesarias para Chromium
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
    libxkbcommon0 \
    libpango-1.0-0 \
    libcairo2 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar primero requirements para aprovechar cache de Docker
COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Instalar solo Chromium (SIN --with-deps)
RUN playwright install chromium

# Copiar el resto del proyecto
COPY . .

EXPOSE 5000

CMD ["python", "app.py"]