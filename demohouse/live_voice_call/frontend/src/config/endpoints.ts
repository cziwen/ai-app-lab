const readEnv = (name: string) => {
  const processEnv =
    typeof process !== 'undefined' ? process.env : undefined;
  const globalProcessEnv = (
    globalThis as { process?: { env?: Record<string, string | undefined> } }
  ).process?.env;
  const value = processEnv?.[name] ?? globalProcessEnv?.[name];
  return typeof value === 'string' && value.trim() ? value : undefined;
};

const fallbackOrigin = () => {
  if (typeof window === 'undefined' || !window.location) {
    return 'http://localhost';
  }
  return window.location.origin;
};

const fallbackWsUrl = () => {
  if (typeof window === 'undefined' || !window.location) {
    return 'ws://localhost/ws';
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws`;
};

const ensureTrailingSlash = (url: string) => (url.endsWith('/') ? url : `${url}/`);

export const WS_URL = readEnv('MODERN_PUBLIC_WS_URL') ?? fallbackWsUrl();
export const LOG_URL = readEnv('MODERN_PUBLIC_LOG_URL') ?? `${fallbackOrigin()}/api/frontend-logs`;
export const API_URL = readEnv('MODERN_PUBLIC_API_URL') ?? fallbackOrigin();
export const OBSERVABILITY_URL = ensureTrailingSlash(
  readEnv('MODERN_PUBLIC_GRAFANA_PUBLIC_URL') ??
    readEnv('GRAFANA_PUBLIC_URL') ??
    `${fallbackOrigin()}/observability/`,
);
