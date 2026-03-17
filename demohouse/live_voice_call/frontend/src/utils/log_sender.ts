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

const LOG_ENDPOINT = 'http://localhost:8889/api/frontend-logs';
const FLUSH_INTERVAL_MS = 5000;
const MAX_BATCH_SIZE = 50;
const MAX_RETRIES = 3;

class LogSender {
  private buffer: string[] = [];
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private retryCount = 0;

  constructor() {
    this.startFlushTimer();
    if (typeof window !== 'undefined') {
      window.addEventListener('beforeunload', () => this.flushSync());
    }
  }

  enqueue(entry: string): void {
    this.buffer.push(entry);
    if (this.buffer.length >= MAX_BATCH_SIZE) {
      this.flush();
    }
  }

  private startFlushTimer(): void {
    this.flushTimer = setInterval(() => this.flush(), FLUSH_INTERVAL_MS);
  }

  async flush(): Promise<void> {
    if (this.buffer.length === 0) return;
    const batch = [...this.buffer];
    this.buffer = [];

    try {
      const response = await fetch(LOG_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(batch),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      this.retryCount = 0;
    } catch {
      this.retryCount++;
      if (this.retryCount < MAX_RETRIES) {
        this.buffer = [...batch, ...this.buffer];
      } else {
        console.warn('Log sender: max retries exceeded, dropping batch');
        this.retryCount = 0;
      }
    }
  }

  private flushSync(): void {
    if (this.buffer.length === 0) return;
    const blob = new Blob([JSON.stringify(this.buffer)], {
      type: 'application/json',
    });
    navigator.sendBeacon(LOG_ENDPOINT, blob);
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
