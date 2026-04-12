# Observability Stack (V1)

This project includes a lightweight observability stack for ECS single-host deployment:

- Grafana (`/observability/` via Nginx)
- Prometheus (metrics)
- Loki + Promtail (logs)
- Node Exporter (host metrics)
- Redis Exporter (Redis metrics)

## Included log streams

Promtail scrapes:

- `backend/logs/backend-*.log` as `job=backend-server`
- `backend/data/storage/interview_logs/*/backend.log` as `job=interview-logs,stream=backend`
- `backend/data/storage/interview_logs/*/frontend.log` as `job=interview-logs,stream=frontend`

For interview logs, `token` is extracted from file path and attached as a label.

## First-run checks

1. `docker compose up -d`
2. Open `https://<domain>/observability/` and log in with `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`.
3. In Grafana:
   - Data sources should contain `Prometheus` and `Loki`.
   - Dashboard folder `Live Voice` should contain `Live Voice Observability Overview`.
4. In Explore (Loki), query by token:
   - `{job="interview-logs", token="INT-xxxx"}`

## Notes

- Official dashboards are downloaded at Grafana startup when network is available:
  - Node Exporter Full (ID 1860)
  - Redis Overview (ID 11835)
- Prometheus retention defaults to 15 days.
- Loki retention defaults to 168h (7 days).
