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
- Audio self-check buttons are provided in check-in:
  - Checks are sequential; only current step can be tested and passed.
  - Speaker test: select output device in dialog, play test tone, verify waveform, then confirm.
  - Microphone test: select input device in dialog and verify real-time waveform.
  - Camera test: select video device and verify preview in dialog.
  - Screen sharing step requires selecting the entire screen (displaySurface = monitor).

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
  BotError: WsEventEnvelope<{ code?: number; message?: string }>;
  BotUpdateConfig: WsEventEnvelope<{ speaker?: string }>;
  UserAudio: WsEventEnvelope<Record<string, never>>;
};
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
5. grant mic/camera/screen, then run speaker test and confirm audio is heard
6. verify in call page:
   - subtitle updates
   - debug drawer opens
   - transcript panel toggles
7. validation:
   - open `/` without token -> invalid link page
   - in non-localhost domain with `session=s1` -> invalid link page
