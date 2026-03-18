// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// Licensed under the 【火山方舟】原型应用软件自用许可协议
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//     https://www.volcengine.com/docs/82379/1433703
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { LOG_URL } from '@/config/endpoints';

const LOG_ENDPOINT = LOG_URL;
const FLUSH_INTERVAL_MS = 5000;
const MAX_BATCH_SIZE = 50;
const MAX_RETRIES = 3;

type BufferedLog = {
  token: string;
  message: string;
};

class LogSender {
  private buffer: BufferedLog[] = [];
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private retryCountByToken = new Map<string, number>();

  constructor() {
    this.startFlushTimer();
    if (typeof window !== 'undefined') {
      window.addEventListener('beforeunload', () => this.flushSync());
    }
  }

  enqueue(entry: string, token?: string | null): void {
    const normalizedToken = token?.trim();
    if (!normalizedToken) {
      console.warn('Log sender: missing interview token, drop entry');
      return;
    }
    this.buffer.push({
      token: normalizedToken,
      message: entry,
    });
    if (this.buffer.length >= MAX_BATCH_SIZE) {
      this.flush();
    }
  }

  private buildEndpoint(token: string): string {
    if (typeof window !== 'undefined') {
      const resolved = new URL(LOG_ENDPOINT, window.location.href);
      resolved.searchParams.set('token', token);
      return resolved.toString();
    }
    const separator = LOG_ENDPOINT.includes('?') ? '&' : '?';
    return `${LOG_ENDPOINT}${separator}token=${encodeURIComponent(token)}`;
  }

  private groupByToken(batch: BufferedLog[]): Map<string, string[]> {
    const grouped = new Map<string, string[]>();
    for (const item of batch) {
      const current = grouped.get(item.token) ?? [];
      current.push(item.message);
      grouped.set(item.token, current);
    }
    return grouped;
  }

  private startFlushTimer(): void {
    this.flushTimer = setInterval(() => this.flush(), FLUSH_INTERVAL_MS);
  }

  async flush(): Promise<void> {
    if (this.buffer.length === 0) return;
    const batch = [...this.buffer];
    this.buffer = [];

    const grouped = this.groupByToken(batch);
    const failedEntries: BufferedLog[] = [];

    for (const [token, entries] of grouped) {
      try {
        const response = await fetch(this.buildEndpoint(token), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(entries),
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        this.retryCountByToken.delete(token);
      } catch {
        const retryCount = (this.retryCountByToken.get(token) ?? 0) + 1;
        if (retryCount < MAX_RETRIES) {
          this.retryCountByToken.set(token, retryCount);
          for (const message of entries) {
            failedEntries.push({ token, message });
          }
        } else {
          console.warn(`Log sender: max retries exceeded for token=${token}, dropping batch`);
          this.retryCountByToken.delete(token);
        }
      }
    }

    if (failedEntries.length > 0) {
      this.buffer = [...failedEntries, ...this.buffer];
    }
  }

  private flushSync(): void {
    if (this.buffer.length === 0) return;
    const grouped = this.groupByToken(this.buffer);
    for (const [token, entries] of grouped) {
      const blob = new Blob([JSON.stringify(entries)], {
        type: 'application/json',
      });
      navigator.sendBeacon(this.buildEndpoint(token), blob);
    }
    this.buffer = [];
  }

  dispose(): void {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
    }
    this.flush();
  }
}

export const logSender = new LogSender();
