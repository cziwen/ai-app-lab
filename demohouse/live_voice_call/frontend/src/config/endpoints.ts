const DEFAULT_WS_URL = 'ws://127.0.0.1:8888';
const DEFAULT_LOG_URL = 'http://127.0.0.1:8889/api/frontend-logs';
const DEFAULT_API_URL = 'http://localhost:8890';

const readEnv = (name: string) => {
  if (typeof process === 'undefined' || !process.env) {
    return undefined;
  }
  const value = process.env[name];
  return typeof value === 'string' && value.trim() ? value : undefined;
};

export const WS_URL = readEnv('MODERN_PUBLIC_WS_URL') ?? DEFAULT_WS_URL;
export const LOG_URL = readEnv('MODERN_PUBLIC_LOG_URL') ?? DEFAULT_LOG_URL;
export const API_URL = readEnv('MODERN_PUBLIC_API_URL') ?? DEFAULT_API_URL;
