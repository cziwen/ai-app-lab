const readEnv = (name: string) => {
  if (typeof process === 'undefined' || !process.env) {
    return undefined;
  }
  const value = process.env[name];
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

export const WS_URL = readEnv('MODERN_PUBLIC_WS_URL') ?? fallbackWsUrl();
export const LOG_URL = readEnv('MODERN_PUBLIC_LOG_URL') ?? `${fallbackOrigin()}/api/frontend-logs`;
export const API_URL = readEnv('MODERN_PUBLIC_API_URL') ?? fallbackOrigin();
