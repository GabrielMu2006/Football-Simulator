FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 QT_QPA_PLATFORM=offscreen \
    PYTHONUTF8=1 PYTHONFAULTHANDLER=1 HOME=/tmp/fs-home
RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 libgl1 libopengl0 libglib2.0-0 libdbus-1-3 libxkbcommon0 \
    libxcb-cursor0 libfontconfig1 libfreetype6 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
COPY wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r /tmp/requirements.txt \
    && rm -rf /tmp/wheels
USER 10001:10001
WORKDIR /workspace
CMD ["python", "scripts/docker_check.py", "core"]
