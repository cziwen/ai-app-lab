# Frontend Call UI Architecture & API Contract

## 1. Goal and Boundaries
- Goal: build a standalone frontend "meeting call" page for AI interviewer experience.
- Boundary: frontend-only in v1. Backend integration is optional and can be turned on by switching mode.
- Default runtime: `mock` mode so UI can run without WebSocket server.
- Existing real-time hooks are retained for future integration (`real` mode).
- Access gating is enabled:
  - `token` is required for both `/check-in` and `/`.
  - `session=s1` is only allowed on `localhost / 127.0.0.1 / ::1`; otherwise the app shows invalid-link page.
- Device check-in requires 4 checks before entering interview:
  - speaker
  - microphone
  - camera
  - screen sharing
- Device checks are strictly sequential (later step buttons are hidden until previous step passes):
  - Speaker test: dialog with output device selector + playback + waveform line + user confirmation.
  - Microphone test: dialog with input device selector + real-time waveform line.
  - Camera test: dialog with camera selector + live preview confirmation.
  - Screen sharing: must select entire screen (`displaySurface = monitor`) or it is rejected.
- On hang up, app navigates to a result page that only shows final notice text (no action buttons).
- On `进入面试`, a final full re-check runs before navigation:
  - screen-share track must still be live and still be entire-screen sharing
  - selected speaker must still be available
  - microphone + camera are reacquired and must both succeed
  - if any check fails, app stays on check-in page and shows an error

## 2. Interface Overview

### 2.1 UI Component Interfaces
- `CallParticipantCardProps`
- `CallControlBarProps`
- `LiveSubtitleBarProps`
- `TranscriptListProps`
- `DebugDrawerProps`

### 2.2 State and Controller Interfaces
- `CallMode`
- `CallParticipant`
- `CallUiState`
- `DebugPanelState`
- `TranscriptItem`
- `CallControlAction`
- `CallController`
- `MockCallScript`

### 2.3 WebSocket Contract Interfaces (typed for frontend)
- `WsEventEnvelope<TPayload>`
- `WsReadyPayload`
- `WsSentencePayload`
- `WsContractEventMap`

### 2.4 Runtime Config Interfaces
- `CallMode` for `mock | real` switching.
- `DebugPanelState.wsUrl` for runtime WebSocket URL.
- Public runtime endpoints are configurable via env vars:
  - `MODERN_PUBLIC_WS_URL` (default: `ws://127.0.0.1:8888`)
  - `MODERN_PUBLIC_LOG_URL` (default: `http://127.0.0.1:8889/api/frontend-logs`)

## 3. Exported API Surface

All interfaces and components are exposed in:
- `frontend/src/call-ui/index.ts`

Exports include:
- Components:
  - `CallInterviewPage`
  - `CallControlBar`
  - `CallParticipantCard`
  - `LiveSubtitleBar`
  - `DebugDrawer`
  - `TranscriptList`
- Hooks/helpers:
  - `useCallController`
  - `formatDuration`
- Types:
  - `CallControlAction`
  - `CallControlBarProps`
  - `CallController`
  - `CallMode`
  - `CallParticipant`
  - `CallParticipantCardProps`
  - `CallUiState`
  - `DebugDrawerProps`
  - `DebugPanelState`
  - `LiveSubtitleBarProps`
  - `MockCallScript`
  - `TranscriptItem`
  - `TranscriptListProps`
  - `WsContractEventMap`
  - `WsEventEnvelope`
  - `WsReadyPayload`
  - `WsSentencePayload`

## 4. TypeScript Contract Details

### 4.1 Core UI State
```ts
type CallMode = 'mock' | 'real';

interface CallUiState {
  mode: CallMode;
  isConnected: boolean;
  isInCall: boolean;
  micOn: boolean;
  camOn: boolean;
  shareOn: boolean;
  elapsedSec: number;
  subtitle: string;
  interviewer: CallParticipant;
  user: CallParticipant;
}
```

### 4.2 Controller
```ts
interface CallController {
  uiState: CallUiState;
  debugState: DebugPanelState;
  transcripts: TranscriptItem[];
  debugOpen: boolean;
  messagePanelOpen: boolean;
  setWsUrl: (nextUrl: string) => void;
  onControlAction: (action: CallControlAction) => void;
}
```

### 4.3 Control Actions
```ts
type CallControlAction =
  | 'toggleMic'
  | 'toggleCam'
  | 'toggleShare'
  | 'hangUp'
  | 'connect'
  | 'toggleDebug'
  | 'toggleMessagePanel'
  | 'switchMode';
```

### 4.4 WebSocket Event Types
```ts
type WsContractEventMap = {
  BotReady: WsEventEnvelope<WsReadyPayload>;
  SentenceRecognized: WsEventEnvelope<WsSentencePayload>;
  TTSSentenceStart: WsEventEnvelope<WsSentencePayload>;
  TTSDone: WsEventEnvelope<Record<string, never>>;
  BotError: WsEventEnvelope<{
    error?: { code?: string | number; message?: string };
  }>;
  BotUpdateConfig: WsEventEnvelope<{ speaker?: string }>;
  UserAudio: WsEventEnvelope<Record<string, never>>;
};
```

### 4.5 Runtime Endpoint Config
```ts
const WS_URL =
  process.env.MODERN_PUBLIC_WS_URL ?? 'ws://127.0.0.1:8888';
const LOG_URL =
  process.env.MODERN_PUBLIC_LOG_URL ??
  'http://127.0.0.1:8889/api/frontend-logs';
```

## 5. Event Flow (Connect -> Call -> Recognize -> Reply -> Hangup)
1. `connect`
   - `mock`: sets local connected state to true.
   - `real`: calls `handleConnect()` from existing voice service hook.
2. `toggleMic`
   - `mock`: enters call simulation and starts scripted transcript flow.
   - `real`: toggles `recStart()` / `recStop()`.
3. Recognition phase
   - `mock`: fills `currentUserSentence` from script.
   - `real`: reads from `SentenceRecognized`.
4. Reply phase
   - `mock`: types out bot sentence progressively.
   - `real`: reads streaming text from `TTSSentenceStart` and completion from `TTSDone`.
5. `hangUp`
   - stops active recording/simulation and clears active subtitle state.
   - redirects to `/hangup-result` and displays "结果已收到，HR 后续联系" notice page.

## 6. Mock Contract
- Script source: `DEFAULT_SCRIPT` in `useCallController`.
- Each script cycle emits:
  - one user transcript item
  - one bot transcript item
- UI behavior in mock:
  - no backend required
  - timer increments while in call
  - subtitle shows latest user/bot sentence
  - logs are generated with `mock | ...` prefix

Example script entry:
```ts
{
  userSentence: '你好，我准备好了，可以开始今天的面试吗？',
  botSentence: '当然可以。先做一个一分钟的自我介绍吧。'
}
```

## 7. Extension Guide (Integrate Real Backend Later)
- Keep page orchestration unchanged; only switch to `real` mode in debug drawer.
- Reuse existing providers and hooks already wired in route:
  - `AudioChatProvider`
  - `AudioChatServiceProvider`
- Keep UI component contracts stable; backend integration should update only controller behavior, not component prop shapes.
- If new WS events are added, extend `WsContractEventMap` first, then controller mapping.

## 8. Quick Frontend Test Steps
1. `cd frontend`
2. `pnpm install`
3. `pnpm run dev`
4. open `http://localhost:8080/check-in?token=t1&session=s1`
   - optional: set endpoint env vars before start:
     - `MODERN_PUBLIC_WS_URL`
     - `MODERN_PUBLIC_LOG_URL`
5. complete sequential check-in:
   - speaker dialog -> play + waveform + confirm
   - microphone dialog -> waveform reacts to voice
   - camera dialog -> preview works and confirm
   - screen share -> select entire screen
6. verify in call page:
   - subtitle updates
   - debug drawer opens
   - transcript panel toggles
   - mic/camera/share switches are not user-operable on main page
7. click hang up:
   - app redirects to `/hangup-result`
   - page only contains result notice text, with no buttons
8. validation:
   - open `/` without token -> invalid link page
   - in non-localhost domain with `session=s1` -> invalid link page
