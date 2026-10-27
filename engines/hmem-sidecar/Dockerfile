FROM node:20-slim
WORKDIR /srv
COPY engines/hmem /srv/hmem
COPY engines/hmem-sidecar/package.json /srv/hmem-sidecar/package.json
RUN cd /srv/hmem-sidecar && npm install --omit=dev --no-audit --no-fund
COPY engines/hmem-sidecar/server.ts /srv/hmem-sidecar/server.ts
WORKDIR /srv/hmem-sidecar
ENV HMEM_HOST=0.0.0.0 HMEM_PORT=8787 HMEM_PERSIST_DIR=/data
RUN mkdir -p /data && chown node:node /data
USER node
EXPOSE 8787
CMD ["npx", "tsx", "server.ts"]
