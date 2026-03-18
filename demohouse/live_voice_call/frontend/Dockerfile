FROM node:20-alpine AS builder

WORKDIR /app/frontend

RUN corepack enable

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
