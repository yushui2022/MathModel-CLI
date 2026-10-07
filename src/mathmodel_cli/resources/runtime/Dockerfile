FROM node:22-bookworm-slim
ARG CODEX_VERSION=0.160.1
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv libreoffice-writer fonts-noto-cjk fonts-liberation \
    ca-certificates git tini && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/matplotlib XDG_CACHE_HOME=/tmp/cache \
    HOME=/home/agent CODEX_HOME=/home/agent/.codex
COPY requirements.txt /opt/mathmodel/requirements.txt
RUN pip install --no-cache-dir -r /opt/mathmodel/requirements.txt && pip check \
    && npm install -g @openai/codex@${CODEX_VERSION} && npm cache clean --force
COPY agent.py verify.py proxy.py /opt/mathmodel/
RUN mkdir -p /workspace /verification /home/agent && chown -R 1000:1000 /home/agent
WORKDIR /workspace
USER 1000:1000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/opt/mathmodel/agent.py"]
