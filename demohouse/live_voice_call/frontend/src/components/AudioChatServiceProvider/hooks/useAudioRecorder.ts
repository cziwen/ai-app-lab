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

import { useContext, useRef } from 'react';
import { AudioChatServiceContext } from '@/components/AudioChatServiceProvider/context';
import Recorder from 'recorder-core';
import { BIT_RATE, FRAME_SIZE, SAMPLE_RATE } from '@/constant';
import { EventType } from '@/types';
import { useLogContent } from '@/components/AudioChatServiceProvider/hooks/useLogContent';
import { useAudioChatState } from '@/components/AudioChatProvider/hooks/useAudioChatState';
import { useSessionAuth } from '@/auth/context';

const AUDIO_TRACK_SET = {
  autoGainControl: true,
  echoCancellation: true,
  noiseSuppression: true,
  channelCount: 1,
};

const resolveEnvMs = (value: string | undefined, fallback: number) => {
  const parsed = Number.parseInt(String(value || '').trim(), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return fallback;
  }
  return parsed;
};

export const resolveRecorderPrerollDropMs = () =>
  resolveEnvMs(
    process.env.MODERN_PUBLIC_RECORDER_PREROLL_DROP_MS ||
      process.env.RECORDER_PREROLL_DROP_MS,
    300,
  );

const RECORDER_PREROLL_DROP_MS = resolveRecorderPrerollDropMs();

export const useAudioRecorder = () => {
  const {
    serviceRef,
    recorderRef,
    sendLastFrameRef,
    sendChunkRef,
    sendPcmBufferRef,
  } = useContext(AudioChatServiceContext);
  const { selectedMicId } = useSessionAuth();
  const { log } = useLogContent();
  const { setUserSpeaking, setUserAudioLevel } = useAudioChatState();
  const prerollDropSamplesRef = useRef(0);
  const handleReset = () => {
    sendPcmBufferRef.current = new Int16Array(0);
    sendChunkRef.current = null;
    sendLastFrameRef.current = null;
    prerollDropSamplesRef.current = 0;
  };
  const handleSend = (pcmFrame: Int16Array, isClose: boolean) => {
    if (isClose && pcmFrame.length === 0) {
      const len = sendLastFrameRef.current
        ? sendLastFrameRef.current.length
        : Math.round((SAMPLE_RATE / 1000) * 50);
      pcmFrame = new Int16Array(len);
    }
    sendLastFrameRef.current = pcmFrame;

    const blob = new Blob([pcmFrame.buffer], { type: 'audio/pcm' }); //这是裸pcm，无前44字节wav头字节wav头

    serviceRef.current?.sendMessage({
      event: EventType.UserAudio,
      data: blob,
    });
    log('send | event:' + EventType.UserAudio + ' payload: ...');
  };

  const handleProcess = (
    buffers: (Int16Array | null)[],
    bufferSampleRate: number,
    isClose: boolean,
  ) => {
    let pcm = new Int16Array(0);
    if (buffers.length > 0) {
      // 把 pcm列表（二维数组）展开成一维
      const chunk = Recorder.SampleData(
        buffers,
        bufferSampleRate,
        SAMPLE_RATE,
        sendChunkRef.current,
      );
      sendChunkRef.current = chunk;

      pcm = chunk.data;
      if (prerollDropSamplesRef.current > 0 && pcm.length > 0) {
        const dropSamples = Math.min(prerollDropSamplesRef.current, pcm.length);
        prerollDropSamplesRef.current -= dropSamples;
        pcm = new Int16Array(pcm.subarray(dropSamples));
      }
    }

    let pcmBuffer = sendPcmBufferRef.current;
    const tmp = new Int16Array(pcmBuffer.length + pcm.length);
    tmp.set(pcmBuffer, 0);
    tmp.set(pcm, pcmBuffer.length);
    pcmBuffer = tmp;

    const chunkSize = FRAME_SIZE / (BIT_RATE / 8);

    // 按 timeSlice 切分
    while (true) {
      if (pcmBuffer.length >= chunkSize) {
        const frame = new Int16Array(pcmBuffer.subarray(0, chunkSize));
        pcmBuffer = new Int16Array(pcmBuffer.subarray(chunkSize));

        let closeVal = false;
        if (isClose && pcmBuffer.length === 0) {
          closeVal = true;
        }
        handleSend(frame, closeVal);
        if (!closeVal) continue;
      } else if (isClose) {
        const frame = new Int16Array(chunkSize);
        frame.set(pcmBuffer);
        pcmBuffer = new Int16Array(0);
        handleSend(frame, true);
      }
      break;
    }
    sendPcmBufferRef.current = pcmBuffer;
  };

  const recStart = () => {
    if (recorderRef.current) {
      recorderRef.current.close();
    }

    handleReset();
    let clearBufferIdx = 0;
    const normalizedSelectedMicId = selectedMicId.trim();
    prerollDropSamplesRef.current = Math.max(
      0,
      Math.round((SAMPLE_RATE * RECORDER_PREROLL_DROP_MS) / 1000),
    );
    if (prerollDropSamplesRef.current > 0) {
      log(
        `mic preroll drop enabled drop_ms=${RECORDER_PREROLL_DROP_MS} drop_samples=${prerollDropSamplesRef.current}`,
      );
    }

    const buildRecorder = (
      audioTrackSet:
        | typeof AUDIO_TRACK_SET
        | (typeof AUDIO_TRACK_SET & { deviceId: { exact: string } }),
    ) =>
      Recorder({
        type: 'unknown',
        audioTrackSet,
        onProcess: (
          buffers: (Int16Array | null)[],
          powerLevel: unknown,
          bufferDuration: unknown,
          bufferSampleRate: number,
          newBufferIdx: number,
          // asyncEnd
        ) => {
          const numericPower =
            typeof powerLevel === 'number' && Number.isFinite(powerLevel)
              ? powerLevel
              : 0;
          const normalizedLevel = Math.max(
            0,
            Math.min(1, (numericPower - 0.02) / 0.28),
          );
          setUserAudioLevel(normalizedLevel);
          for (let i = clearBufferIdx; i < newBufferIdx; i++) {
            buffers[i] = null;
          }
          clearBufferIdx = newBufferIdx;

          handleProcess(buffers, bufferSampleRate, false);
        },
      });

    const openRecorder = (preferSelectedMic: boolean) => {
      const shouldUseSelectedMic = preferSelectedMic && !!normalizedSelectedMicId;
      const audioTrackSet = shouldUseSelectedMic
        ? {
            ...AUDIO_TRACK_SET,
            deviceId: { exact: normalizedSelectedMicId },
          }
        : AUDIO_TRACK_SET;

      if (shouldUseSelectedMic) {
        log(`mic inherit apply device=${normalizedSelectedMicId}`);
      }

      const recorder = buildRecorder(audioTrackSet);
      recorderRef.current = recorder;
      recorder.open(
        () => {
          try {
            const stream = (
              recorder as unknown as {
                stream?: MediaStream;
              }
            ).stream;
            const track = stream?.getAudioTracks?.()[0];
            if (track) {
              const settings = track.getSettings();
              const echoCancellation = settings.echoCancellation;
              const noiseSuppression = settings.noiseSuppression;
              const autoGainControl = settings.autoGainControl;
              const channelCount = settings.channelCount;
              log(
                `mic settings channel_count=${String(channelCount ?? '-')}` +
                  ` aec=${String(echoCancellation ?? '-')}` +
                  ` ns=${String(noiseSuppression ?? '-')}` +
                  ` agc=${String(autoGainControl ?? '-')}`,
              );
              if (
                echoCancellation === false ||
                noiseSuppression === false ||
                autoGainControl === false
              ) {
                log('mic risk: aec_or_ns_or_agc_disabled');
              }
            }
          } catch (_error) {
            // best effort only
          }
          recorder.start();
          setUserSpeaking(true);
        },
        (msg: string, isUserNotAllow: boolean) => {
          if (shouldUseSelectedMic) {
            log('mic inherit fallback default');
            recorder.close();
            if (recorderRef.current === recorder) {
              recorderRef.current = null;
            }
            openRecorder(false);
            return;
          }
          setUserAudioLevel(0);
          console.error(
            (isUserNotAllow ? 'UserNotAllow，' : '') + '无法录音:' + msg,
          );
        },
      );
    };

    openRecorder(true);
  };

  const recStop = () => {
    if (!recorderRef.current) {
      setUserAudioLevel(0);
      return;
    }
    setUserSpeaking(false);
    setUserAudioLevel(0);
    recorderRef.current.close();
    handleProcess([], 0, true);
  };

  return {
    recStart,
    recStop,
  };
};
