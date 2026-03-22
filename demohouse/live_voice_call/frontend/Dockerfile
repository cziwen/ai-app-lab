FROM node:20-alpine AS builder

WORKDIR /app/frontend

RUN corepack enable

# Keep frontend build memory bounded on low-memory servers.
ARG FRONTEND_NODE_OPTIONS="--max-old-space-size=640"
ENV NODE_OPTIONS="${FRONTEND_NODE_OPTIONS}"

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

FROM nginx:1.27-alpine

RUN apk add --no-cache openssl

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/10-init-dummy-cert.sh /docker-entrypoint.d/10-init-dummy-cert.sh
RUN chmod +x /docker-entrypoint.d/10-init-dummy-cert.sh
COPY --from=builder /app/frontend/dist /usr/share/nginx/html

EXPOSE 80 443
