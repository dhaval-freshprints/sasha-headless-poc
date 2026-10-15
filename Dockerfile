FROM mcr.microsoft.com/playwright:v1.63.0-noble

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/customer-links-python \
    && /opt/customer-links-python/bin/pip install --no-cache-dir PyMySQL==1.2.3 python-dotenv==1.2.1 Pillow==12.3.0

RUN npm install --global @openai/codex@alpha playwright@1.63.0 \
    && mkdir -p /workspace

ENV NODE_PATH=/usr/lib/node_modules
ENV PATH="/opt/customer-links-python/bin:${PATH}"

WORKDIR /workspace

CMD ["sleep", "infinity"]
