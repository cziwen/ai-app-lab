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
const LOG_BUFFER_MAX_ENTRIES = 2000;
const LOG_DROP_WARN_EVERY = 100;

type BufferedLog = {
  token: string;
  message: string;
};

class LogSender {
  private buffer: BufferedLog[] = [];
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private retryCountByToken = new Map<string, number>();
  private flushing = false;
  private pendingFlush = false;
  private droppedByBufferLimit = 0;

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
    this.enforceBufferLimit();
    if (this.buffer.length >= MAX_BATCH_SIZE) {
      void this.flush();
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
    this.flushTimer = setInterval(() => {
      void this.flush();
    }, FLUSH_INTERVAL_MS);
  }

  async flush(): Promise<void> {
    if (this.flushing) {
      this.pendingFlush = true;
      return;
    }
    this.flushing = true;
    try {
      do {
        this.pendingFlush = false;
        await this.flushOnce();
      } while (this.pendingFlush || this.buffer.length >= MAX_BATCH_SIZE);
    } finally {
      this.flushing = false;
    }
  }

  private async flushOnce(): Promise<void> {
    if (this.buffer.length === 0) return;
    const batch = this.buffer.splice(0, MAX_BATCH_SIZE);

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
      this.enforceBufferLimit();
    }
  }

  private flushSync(): void {
    if (this.buffer.length === 0) return;
    if (typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') {
      console.warn('Log sender: sendBeacon unavailable, dropping buffered logs');
      this.buffer = [];
      return;
    }
    const grouped = this.groupByToken(this.buffer);
    let failedTokenCount = 0;
    for (const [token, entries] of grouped) {
      const blob = new Blob([JSON.stringify(entries)], {
        type: 'application/json',
      });
      const ok = navigator.sendBeacon(this.buildEndpoint(token), blob);
      if (!ok) {
        failedTokenCount += 1;
      }
    }
    if (failedTokenCount > 0) {
      console.warn(
        `Log sender: sendBeacon failed for ${failedTokenCount} token groups, dropping buffered logs`,
      );
    }
    this.buffer = [];
  }

  dispose(): void {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
    }
    void this.flush();
  }

  private enforceBufferLimit(): void {
    const overflow = this.buffer.length - LOG_BUFFER_MAX_ENTRIES;
    if (overflow <= 0) {
      return;
    }
    this.buffer.splice(0, overflow);
    this.droppedByBufferLimit += overflow;
    if (
      this.droppedByBufferLimit === overflow ||
      this.droppedByBufferLimit % LOG_DROP_WARN_EVERY === 0
    ) {
      console.warn(
        `Log sender: dropped ${this.droppedByBufferLimit} buffered entries due to buffer limit`,
      );
    }
  }
}

export const logSender = new LogSender();
