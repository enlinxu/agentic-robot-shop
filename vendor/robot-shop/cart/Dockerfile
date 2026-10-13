FROM node:22-bookworm-slim
WORKDIR /opt/server
COPY package*.json ./
RUN npm ci --omit=dev
COPY server.js ./
ENV NODE_OPTIONS="--require @opentelemetry/auto-instrumentations-node/register"
CMD ["node", "server.js"]
