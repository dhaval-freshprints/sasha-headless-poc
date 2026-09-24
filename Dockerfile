FROM mcr.microsoft.com/playwright:v1.63.0-noble

RUN npm install --global @openai/codex@alpha playwright@1.63.0 \
    && mkdir -p /workspace

ENV NODE_PATH=/usr/lib/node_modules

WORKDIR /workspace

CMD ["sleep", "infinity"]
