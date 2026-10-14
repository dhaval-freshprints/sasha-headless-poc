FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/lookup-python \
    && /opt/lookup-python/bin/pip install --no-cache-dir PyMySQL==1.2.3 python-dotenv==1.2.1 \
    && npm install --global @openai/codex@alpha

ENV PATH="/opt/lookup-python/bin:${PATH}"
WORKDIR /workspace
CMD ["sleep", "infinity"]
