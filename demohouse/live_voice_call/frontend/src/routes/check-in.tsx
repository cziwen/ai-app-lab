import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from '@modern-js/runtime/router';
import { useSessionAuth } from '@/auth/context';
import { API_URL } from '@/config/endpoints';
import type { PermissionKey, PermissionState, PermissionStatus } from '@/auth/types';
import { logSender } from '@/utils/log_sender';

type CheckStep = PermissionKey;
type ModalType = Extract<CheckStep, 'speaker' | 'mic' | 'camera'> | null;
type PermissionBootstrapState = 'idle' | 'requesting' | 'granted' | 'failed';

const ORDERED_STEPS: CheckStep[] = ['speaker', 'mic', 'camera', 'screen'];
const DEFAULT_REQUIRED_STEPS: CheckStep[] = ['speaker', 'mic'];

const STEP_LABEL: Record<CheckStep, string> = {
  speaker: '扬声器',
  mic: '麦克风',
  camera: '摄像头',
  screen: '屏幕共享',
};

const statusTextMap: Record<PermissionStatus, string> = {
  pending: '待检测',
  granted: '已通过',
  denied: '失败',
};

const stopStream = (stream: MediaStream | null) => {
  if (!stream) {
    return;
  }
  for (const track of stream.getTracks()) {
    track.stop();
  }
};

const allGranted = (permissions: PermissionState, requiredSteps: CheckStep[]) =>
  requiredSteps.every(step => permissions[step] === 'granted');

const normalizeRequiredCheckins = (input: unknown): CheckStep[] => {
  if (!Array.isArray(input)) {
    return [...DEFAULT_REQUIRED_STEPS];
  }
  const seen = new Set<CheckStep>();
  for (const item of input) {
    if (typeof item !== 'string') {
      continue;
    }
    const normalized = item.trim() as CheckStep;
    if (ORDERED_STEPS.includes(normalized)) {
      seen.add(normalized);
    }
  }
  return ORDERED_STEPS.filter(step => seen.has(step));
};

const isMobileBrowser = () => {
  if (typeof navigator === 'undefined') {
    return false;
  }
  return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
};

const isSafariBrowser = () => {
  if (typeof navigator === 'undefined') {
    return false;
  }
  const ua = navigator.userAgent;
  return /Safari/i.test(ua) && !/Chrome|CriOS|EdgiOS|FxiOS/i.test(ua);
};

const getPermissionHint = () => {
  if (isSafariBrowser()) {
    return '请在 iPhone 设置 -> Safari -> 麦克风/相机 中允许访问，并在地址栏网站设置中允许权限后重试。';
  }
  return '请在手机系统设置和浏览器站点权限中允许麦克风/相机访问，然后重新申请权限。';
};

const getAudioContextClass = () => {
  return (
    window.AudioContext ||
    ((window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext as typeof AudioContext | undefined)
  );
};

export const CheckInPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { token, setCheckInPassed, permissions, setPermissions, mediaStreamsRef } =
    useSessionAuth();
  const logCheckin = useCallback(
    (message: string) => {
      const entry = `[${new Date().toLocaleTimeString()}]\t[CheckIn] ${message}`;
      logSender.enqueue(entry, token);
    },
    [token],
  );

  const [loadingCheckinConfig, setLoadingCheckinConfig] = useState(true);
  const [requiredSteps, setRequiredSteps] = useState<CheckStep[]>([
    ...DEFAULT_REQUIRED_STEPS,
  ]);
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [errorMessage, setErrorMessage] = useState('');
  const [checking, setChecking] = useState(false);
  const [modalType, setModalType] = useState<ModalType>(null);
  const [permissionBootstrapState, setPermissionBootstrapState] =
    useState<PermissionBootstrapState>('idle');
  const [permissionBootstrapError, setPermissionBootstrapError] = useState('');
  const [audioOutputVisibility, setAudioOutputVisibility] = useState<
    'unknown' | 'visible' | 'likely_hidden_by_platform'
  >('unknown');

  const [speakerDevices, setSpeakerDevices] = useState<MediaDeviceInfo[]>([]);
  const [micDevices, setMicDevices] = useState<MediaDeviceInfo[]>([]);
  const [cameraDevices, setCameraDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedSpeaker, setSelectedSpeaker] = useState('');
  const [selectedMic, setSelectedMic] = useState('');
  const [selectedCamera, setSelectedCamera] = useState('');
  const [speakerReady, setSpeakerReady] = useState(false);
  const [micReady, setMicReady] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);

  const [speakerPlaying, setSpeakerPlaying] = useState(false);
  const [micWaveActive, setMicWaveActive] = useState(false);

  const speakerCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const micCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const cameraVideoRef = useRef<HTMLVideoElement | null>(null);

  const testAnimationRef = useRef<number | null>(null);
  const testAudioContextRef = useRef<AudioContext | null>(null);
  const testSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const testAnalyserRef = useRef<AnalyserNode | null>(null);
  const testAudioRef = useRef<HTMLAudioElement | null>(null);
  const testMediaStreamRef = useRef<MediaStream | null>(null);
  const bootstrapMediaStreamRef = useRef<MediaStream | null>(null);

  const canEnter = useMemo(
    () => allGranted(permissions, requiredSteps),
    [permissions, requiredSteps],
  );
  const bootstrapReady = permissionBootstrapState === 'granted';
  const requiredStepsKey = useMemo(() => requiredSteps.join(','), [requiredSteps]);

  const cleanupTestMedia = useCallback(() => {
    if (testAnimationRef.current) {
      window.cancelAnimationFrame(testAnimationRef.current);
      testAnimationRef.current = null;
    }
    testSourceRef.current?.disconnect();
    testSourceRef.current = null;
    testAnalyserRef.current = null;
    if (
      testAudioContextRef.current &&
      testAudioContextRef.current.state !== 'closed'
    ) {
      testAudioContextRef.current.close();
    }
    testAudioContextRef.current = null;
    if (testAudioRef.current) {
      testAudioRef.current.pause();
      testAudioRef.current.srcObject = null;
      testAudioRef.current = null;
    }
    stopStream(testMediaStreamRef.current);
    testMediaStreamRef.current = null;
    setSpeakerPlaying(false);
    setMicWaveActive(false);
  }, []);

  const cleanupBootstrapMedia = useCallback(() => {
    stopStream(bootstrapMediaStreamRef.current);
    bootstrapMediaStreamRef.current = null;
  }, []);

  const resetAll = () => {
    cleanupTestMedia();
    cleanupBootstrapMedia();
    stopStream(mediaStreamsRef.current.userMedia);
    stopStream(mediaStreamsRef.current.displayMedia);
    mediaStreamsRef.current.userMedia = null;
    mediaStreamsRef.current.displayMedia = null;
    setPermissions({
      speaker: 'pending',
      mic: 'pending',
      camera: 'pending',
      screen: 'pending',
    });
    setCurrentStepIndex(0);
    setSpeakerReady(false);
    setMicReady(false);
    setCameraReady(false);
    setErrorMessage('');
    setModalType(null);
    setPermissionBootstrapState('idle');
    setPermissionBootstrapError('');
    setAudioOutputVisibility('unknown');
  };

  useEffect(() => {
    let active = true;
    const loadCheckinConfig = async () => {
      if (!token) {
        if (active) {
          setRequiredSteps([...DEFAULT_REQUIRED_STEPS]);
          setLoadingCheckinConfig(false);
        }
        return;
      }
      setLoadingCheckinConfig(true);
      try {
        const response = await fetch(
          `${API_URL}/api/public/interviews/${encodeURIComponent(token)}/access`,
        );
        if (!response.ok) {
          throw new Error('面试链接无效或已失效');
        }
        const data = await response.json();
        if (!active) {
          return;
        }
        const nextRequired = normalizeRequiredCheckins(
          data?.interview?.required_checkins,
        );
        setRequiredSteps(nextRequired);
      } catch (_error) {
        if (!active) {
          return;
        }
        setRequiredSteps([...DEFAULT_REQUIRED_STEPS]);
        setErrorMessage('读取面试检查项失败，已按默认检查项（扬声器/麦克风）处理。');
      } finally {
        if (active) {
          setLoadingCheckinConfig(false);
        }
      }
    };
    loadCheckinConfig();
    return () => {
      active = false;
    };
  }, [token]);

  const loadDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      return;
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const outputs = devices.filter(item => item.kind === 'audiooutput');
      const inputs = devices.filter(item => item.kind === 'audioinput');
      const cameras = devices.filter(item => item.kind === 'videoinput');
      setSpeakerDevices(outputs);
      setMicDevices(inputs);
      setCameraDevices(cameras);
      setSelectedSpeaker(prev => prev || outputs[0]?.deviceId || '');
      setSelectedMic(prev => prev || inputs[0]?.deviceId || '');
      setSelectedCamera(prev => prev || cameras[0]?.deviceId || '');
      if (outputs.length > 0) {
        setAudioOutputVisibility('visible');
      } else if (inputs.length > 0 || cameras.length > 0) {
        setAudioOutputVisibility('likely_hidden_by_platform');
      } else {
        setAudioOutputVisibility('unknown');
      }
      logCheckin(
        `enumerate devices outputs=${outputs.length} inputs=${inputs.length} cameras=${cameras.length}`,
      );
    } catch (_error) {
      logCheckin('enumerate devices failed');
      setErrorMessage('无法读取设备列表，请检查浏览器权限。');
    }
  }, [logCheckin]);

  const bootstrapPermissions = useCallback(async () => {
    setPermissionBootstrapState('requesting');
    setPermissionBootstrapError('');
    logCheckin(`permission bootstrap start required_steps=${requiredSteps.join(',')}`);
    cleanupBootstrapMedia();
    if (!navigator.mediaDevices?.getUserMedia) {
      setPermissionBootstrapState('failed');
      setPermissionBootstrapError('当前浏览器不支持权限初始化，请更换浏览器。');
      logCheckin('permission bootstrap failed unsupported getUserMedia');
      return;
    }

    const needAudio = requiredSteps.includes('mic');
    const needVideo = requiredSteps.includes('camera');
    if (!needAudio && !needVideo) {
      setPermissionBootstrapState('granted');
      await loadDevices();
      logCheckin('permission bootstrap skip media request (no mic/camera required)');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: needAudio,
        video: needVideo,
      });
      bootstrapMediaStreamRef.current = stream;
      await loadDevices();
      setPermissionBootstrapState('granted');
      logCheckin(
        `permission bootstrap granted audio=${needAudio ? '1' : '0'} video=${needVideo ? '1' : '0'}`,
      );
    } catch (_error) {
      setPermissionBootstrapState('failed');
      setPermissionBootstrapError(getPermissionHint());
      logCheckin(
        `permission bootstrap failed audio=${needAudio ? '1' : '0'} video=${needVideo ? '1' : '0'}`,
      );
    }
  }, [
    cleanupBootstrapMedia,
    logCheckin,
    loadDevices,
    requiredSteps,
  ]);

  useEffect(() => {
    if (
      requiredSteps.some(
        step => step === 'speaker' || step === 'mic' || step === 'camera',
      )
    ) {
      bootstrapPermissions();
    } else {
      setPermissionBootstrapState('granted');
    }
    return () => {
      cleanupTestMedia();
      cleanupBootstrapMedia();
    };
  }, [requiredSteps, bootstrapPermissions, cleanupTestMedia, cleanupBootstrapMedia]);

  useEffect(() => {
    setCurrentStepIndex(0);
    setCheckInPassed(false);
    setPermissions({
      speaker: 'pending',
      mic: 'pending',
      camera: 'pending',
      screen: 'pending',
    });
  }, [requiredStepsKey, setCheckInPassed, setPermissions]);

  const drawWave = (analyser: AnalyserNode, canvas: HTMLCanvasElement) => {
    const context = canvas.getContext('2d');
    if (!context) {
      return;
    }
    const dataArray = new Uint8Array(analyser.fftSize);

    const draw = () => {
      analyser.getByteTimeDomainData(dataArray);
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.lineWidth = 2;
      context.strokeStyle = '#7ed8b7';
      context.beginPath();
      const sliceWidth = canvas.width / dataArray.length;
      let x = 0;
      for (let i = 0; i < dataArray.length; i += 1) {
        const v = dataArray[i] / 128.0;
        const y = (v * canvas.height) / 2;
        if (i === 0) {
          context.moveTo(x, y);
        } else {
          context.lineTo(x, y);
        }
        x += sliceWidth;
      }
      context.lineTo(canvas.width, canvas.height / 2);
      context.stroke();
      testAnimationRef.current = window.requestAnimationFrame(draw);
    };
    testAnimationRef.current = window.requestAnimationFrame(draw);
  };

  const startSpeakerTest = async () => {
    setErrorMessage('');
    cleanupTestMedia();
    const AudioContextClass = getAudioContextClass();
    if (!AudioContextClass || !speakerCanvasRef.current) {
      setErrorMessage('当前浏览器不支持扬声器可视化测试。');
      return;
    }
    try {
      const audioContext = new AudioContextClass();
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();
      const analyser = audioContext.createAnalyser();
      const destination = audioContext.createMediaStreamDestination();
      const testAudio = new Audio();

      oscillator.type = 'sine';
      oscillator.frequency.value = 620;
      gainNode.gain.value = 0.07;
      oscillator.connect(gainNode);
      gainNode.connect(analyser);
      analyser.connect(destination);

      testAudio.srcObject = destination.stream;
      const testAudioWithSink = testAudio as HTMLAudioElement & {
        setSinkId?: (id: string) => Promise<void>;
      };
      if (selectedSpeaker && testAudioWithSink.setSinkId) {
        await testAudioWithSink.setSinkId(selectedSpeaker);
      }
      await testAudio.play();

      testAudioRef.current = testAudio;
      testAudioContextRef.current = audioContext;
      testAnalyserRef.current = analyser;
      setSpeakerPlaying(true);
      drawWave(analyser, speakerCanvasRef.current);
      oscillator.start();
      window.setTimeout(() => {
        oscillator.stop();
        cleanupTestMedia();
        setSpeakerReady(true);
      }, 900);
    } catch (_error) {
      cleanupTestMedia();
      setErrorMessage('扬声器测试失败，请检查输出设备或浏览器设置。');
    }
  };

  const startMicTest = async () => {
    setErrorMessage('');
    cleanupTestMedia();
    const AudioContextClass = getAudioContextClass();
    if (!AudioContextClass || !micCanvasRef.current) {
      setErrorMessage('当前浏览器不支持麦克风可视化测试。');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: selectedMic ? { deviceId: { exact: selectedMic } } : true,
        video: false,
      });
      const audioContext = new AudioContextClass();
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      const source = audioContext.createMediaStreamSource(stream);
      source.connect(analyser);

      testMediaStreamRef.current = stream;
      testAudioContextRef.current = audioContext;
      testSourceRef.current = source;
      testAnalyserRef.current = analyser;
      setMicWaveActive(true);
      setMicReady(true);
      drawWave(analyser, micCanvasRef.current);
    } catch (_error) {
      cleanupTestMedia();
      setMicReady(false);
      setErrorMessage('麦克风测试失败，请检查麦克风权限或设备。');
    }
  };

  const startCameraPreview = async () => {
    setErrorMessage('');
    cleanupTestMedia();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: selectedCamera ? { deviceId: { exact: selectedCamera } } : true,
      });
      testMediaStreamRef.current = stream;
      if (cameraVideoRef.current) {
        cameraVideoRef.current.srcObject = stream;
        await cameraVideoRef.current.play();
      }
      setCameraReady(true);
    } catch (_error) {
      setCameraReady(false);
      setErrorMessage('摄像头预览失败，请检查摄像头权限或设备。');
    }
  };

  const passStep = (step: CheckStep) => {
    const maxIndex = Math.max(requiredSteps.length - 1, 0);
    setPermissions(prev => ({ ...prev, [step]: 'granted' }));
    setCurrentStepIndex(prev => Math.min(prev + 1, maxIndex));
    cleanupTestMedia();
    setModalType(null);
  };

  const failStep = (step: CheckStep, message: string) => {
    setPermissions(prev => ({ ...prev, [step]: 'denied' }));
    const index = requiredSteps.indexOf(step);
    setCurrentStepIndex(index >= 0 ? index : 0);
    setErrorMessage(message);
  };

  const startScreenShareStep = async () => {
    setChecking(true);
    setErrorMessage('');
    if (!navigator.mediaDevices?.getDisplayMedia) {
      failStep('screen', '当前浏览器不支持屏幕共享，请使用电脑端 Chrome 或 Edge。');
      setChecking(false);
      return;
    }
    if (isMobileBrowser()) {
      failStep('screen', '手机浏览器不支持“整个屏幕”共享，请改用电脑浏览器完成面试。');
      setChecking(false);
      return;
    }
    try {
      const displayMedia = await navigator.mediaDevices.getDisplayMedia({
        video: { displaySurface: 'monitor' },
        audio: false,
      } as MediaStreamConstraints);
      const videoTrack = displayMedia.getVideoTracks()[0];
      const displaySurface = videoTrack?.getSettings().displaySurface;
      if (displaySurface !== 'monitor') {
        stopStream(displayMedia);
        failStep('screen', '请共享“整个屏幕”，不要选择窗口或标签页。');
        return;
      }
      stopStream(mediaStreamsRef.current.displayMedia);
      mediaStreamsRef.current.displayMedia = displayMedia;
      passStep('screen');
    } catch (_error) {
      failStep('screen', '屏幕共享失败，请允许并选择“整个屏幕”。');
    } finally {
      setChecking(false);
    }
  };

  const openStepTest = (step: CheckStep) => {
    if (permissionBootstrapState === 'requesting') {
      setErrorMessage('正在初始化权限，请稍候。');
      return;
    }
    if (permissionBootstrapState === 'failed') {
      setErrorMessage('权限初始化失败，请先重新申请权限。');
      return;
    }
    if (requiredSteps[currentStepIndex] !== step) {
      return;
    }
    if (step === 'screen') {
      startScreenShareStep();
      return;
    }
    setModalType(step);
  };

  const validateSpeakerAtEnter = async () => {
    let outputs: MediaDeviceInfo[] = [];
    if (navigator.mediaDevices?.enumerateDevices) {
      const devices = await navigator.mediaDevices.enumerateDevices();
      outputs = devices.filter(item => item.kind === 'audiooutput');
    }
    if (
      outputs.length > 0 &&
      selectedSpeaker &&
      !outputs.find(item => item.deviceId === selectedSpeaker)
    ) {
      logCheckin('speaker validate failed selected speaker not found in output devices');
      failStep('speaker', '扬声器设备不可用，请重新完成扬声器测试。');
      throw new Error('speaker_failed');
    }

    const AudioContextClass = getAudioContextClass();
    if (!AudioContextClass) {
      failStep('speaker', '浏览器不支持扬声器校验，请更换浏览器。');
      throw new Error('speaker_failed');
    }

    const audioContext = new AudioContextClass();
    const oscillator = audioContext.createOscillator();
    const gainNode = audioContext.createGain();
    const destination = audioContext.createMediaStreamDestination();
    const testAudio = new Audio();

    oscillator.type = 'sine';
    oscillator.frequency.value = 560;
    gainNode.gain.value = 0.03;
    oscillator.connect(gainNode);
    gainNode.connect(destination);
    testAudio.srcObject = destination.stream;

    const testAudioWithSink = testAudio as HTMLAudioElement & {
      setSinkId?: (id: string) => Promise<void>;
    };
    if (selectedSpeaker && testAudioWithSink.setSinkId) {
      await testAudioWithSink.setSinkId(selectedSpeaker);
    }
    try {
      await testAudio.play();
    } catch (_error) {
      logCheckin('speaker validate play failed');
      failStep('speaker', '扬声器校验播放失败，请检查系统音量或输出设备。');
      await audioContext.close();
      throw new Error('speaker_failed');
    }
    oscillator.start();
    await new Promise(resolve => window.setTimeout(resolve, 220));
    oscillator.stop();
    testAudio.pause();
    testAudio.srcObject = null;
    await audioContext.close();
  };

  const validateBeforeEnter = async () => {
    if (requiredSteps.includes('screen')) {
      const screenTrack = mediaStreamsRef.current.displayMedia
        ?.getVideoTracks()
        ?.at(0);
      if (!screenTrack || screenTrack.readyState !== 'live') {
        failStep('screen', '屏幕共享已关闭，请重新共享“整个屏幕”。');
        throw new Error('screen_failed');
      }
      const displaySurface = screenTrack.getSettings().displaySurface;
      if (displaySurface !== 'monitor') {
        failStep('screen', '请确保当前仍在共享“整个屏幕”。');
        throw new Error('screen_failed');
      }
    }

    if (requiredSteps.includes('speaker')) {
      await validateSpeakerAtEnter();
    }

    const bootstrapStream = bootstrapMediaStreamRef.current;
    let audioStream: MediaStream | null = null;
    let videoStream: MediaStream | null = null;
    if (requiredSteps.includes('mic')) {
      const audioTrack = bootstrapStream?.getAudioTracks()[0];
      if (audioTrack?.readyState === 'live') {
        audioStream = new MediaStream([audioTrack.clone()]);
      } else {
        try {
          audioStream = await navigator.mediaDevices.getUserMedia({
            audio: selectedMic ? { deviceId: { exact: selectedMic } } : true,
            video: false,
          });
        } catch (_error) {
          failStep('mic', '麦克风状态异常，请重新完成麦克风测试。');
          throw new Error('mic_failed');
        }
      }
    }

    if (requiredSteps.includes('camera')) {
      const videoTrack = bootstrapStream?.getVideoTracks()[0];
      if (videoTrack?.readyState === 'live') {
        videoStream = new MediaStream([videoTrack.clone()]);
      } else {
        try {
          videoStream = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: selectedCamera ? { deviceId: { exact: selectedCamera } } : true,
          });
        } catch (_error) {
          stopStream(audioStream);
          failStep('camera', '摄像头状态异常，请重新完成摄像头测试。');
          throw new Error('camera_failed');
        }
      }
    }

    const tracks = [
      ...(audioStream?.getAudioTracks() || []),
      ...(videoStream?.getVideoTracks() || []),
    ];
    if (tracks.length > 0) {
      const mergedStream = new MediaStream(tracks);
      stopStream(mediaStreamsRef.current.userMedia);
      mediaStreamsRef.current.userMedia = mergedStream;
    }
    stopStream(audioStream);
    stopStream(videoStream);
  };

  const handleEnter = async () => {
    if (!allGranted(permissions, requiredSteps)) {
      setCheckInPassed(false);
      setErrorMessage('请完成当前面试配置的设备检查后再进入面试。');
      return;
    }
    setChecking(true);
    setErrorMessage('');
    try {
      if (!bootstrapReady) {
        throw new Error('权限尚未初始化完成，请先完成权限初始化。');
      }
      await validateBeforeEnter();
      setCheckInPassed(true);
      navigate(`/${location.search}`);
    } catch (error) {
      setCheckInPassed(false);
      setErrorMessage(
        error instanceof Error
          ? error.message
          : '设备状态检查未通过，请重新确认设备。',
      );
    } finally {
      setChecking(false);
    }
  };

  const currentStep = requiredSteps[currentStepIndex];
  const needPhysicalDevices = requiredSteps.some(
    step => step === 'speaker' || step === 'mic' || step === 'camera',
  );

  return (
    <main className="gate-page">
      <section className="gate-card">
        <h1>面试设备检查</h1>
        {loadingCheckinConfig && <p>正在加载本场面试检查项...</p>}
        {!loadingCheckinConfig && requiredSteps.length > 0 && (
          <p>
            请按顺序完成：
            {requiredSteps.map((step, index) => (
              <span key={step}>
                {index > 0 ? ' -> ' : ' '}
                {STEP_LABEL[step]}
              </span>
            ))}
            。
          </p>
        )}
        {!loadingCheckinConfig && requiredSteps.length === 0 && (
          <p>本场面试无需设备检查，可直接进入面试。</p>
        )}

        {!loadingCheckinConfig && requiredSteps.length > 0 && (
          <>
            <p className="gate-hint">
              权限初始化状态：
              {permissionBootstrapState === 'requesting' && '初始化中...'}
              {permissionBootstrapState === 'granted' && '已完成'}
              {permissionBootstrapState === 'failed' && '失败'}
              {permissionBootstrapState === 'idle' && '未开始'}
            </p>
            {permissionBootstrapError && (
              <p className="gate-error">{permissionBootstrapError}</p>
            )}
          </>
        )}

        {!loadingCheckinConfig && requiredSteps.length > 0 && (
          <ul className="permission-list">
            {requiredSteps.map((step, index) => {
              const status = permissions[step];
              const isCurrent = index === currentStepIndex;
              const isLocked = index > currentStepIndex;
              return (
                <li className="permission-item" key={step}>
                  <span>{STEP_LABEL[step]}</span>
                  <div className="permission-right">
                    <strong className={`permission-status is-${status}`}>
                      {status === 'granted' ? '已通过' : statusTextMap[status]}
                    </strong>
                    {!isLocked && (
                      <button
                        type="button"
                        className="permission-test-btn"
                        disabled={checking}
                        onClick={() => openStepTest(step)}
                      >
                        {step === 'screen'
                          ? '共享全屏'
                          : isCurrent
                            ? '测试'
                            : '已完成'}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {errorMessage && <p className="gate-error">{errorMessage}</p>}
        {!errorMessage && !loadingCheckinConfig && requiredSteps.length > 0 && (
          <p className="gate-hint">
            当前步骤：{currentStep ? STEP_LABEL[currentStep] : '已完成'}
          </p>
        )}

        <div className="gate-actions">
          {requiredSteps.length > 0 && (
            <button
              type="button"
              className="gate-btn is-secondary"
              onClick={bootstrapPermissions}
              disabled={permissionBootstrapState === 'requesting'}
            >
              {permissionBootstrapState === 'requesting'
                ? '权限初始化中...'
                : '重新申请权限'}
            </button>
          )}
          {needPhysicalDevices && (
            <button
              type="button"
              className="gate-btn is-secondary"
              onClick={loadDevices}
            >
              刷新设备列表
            </button>
          )}
          {requiredSteps.length > 0 && (
            <button
              type="button"
              className="gate-btn is-secondary"
              onClick={resetAll}
            >
              重新检查
            </button>
          )}
          <button
            type="button"
            className="gate-btn is-enter"
            onClick={handleEnter}
            disabled={
              loadingCheckinConfig ||
              checking ||
              !canEnter ||
              permissionBootstrapState !== 'granted'
            }
          >
            {checking ? '校验中...' : '进入面试'}
          </button>
        </div>
        {audioOutputVisibility === 'likely_hidden_by_platform' && (
          <p className="gate-hint">
            当前浏览器未暴露输出设备列表（常见于 iOS），将使用系统默认扬声器进行测试。
          </p>
        )}
      </section>

      {modalType && (
        <div className="test-modal-mask" role="presentation">
          <section className="test-modal" role="dialog" aria-modal="true">
            <button
              type="button"
              className="modal-close-btn"
              aria-label="关闭测试窗口"
              onClick={() => {
                cleanupTestMedia();
                setModalType(null);
              }}
            >
              ×
            </button>
            <h2>{STEP_LABEL[modalType]}测试</h2>
            {modalType === 'speaker' && (
              <>
                <label className="device-label" htmlFor="speaker-select">
                  输出设备
                </label>
                <select
                  id="speaker-select"
                  className="device-select"
                  value={selectedSpeaker}
                  onChange={event => {
                    setSelectedSpeaker(event.target.value);
                    setSpeakerReady(false);
                  }}
                >
                  {speakerDevices.length === 0 && (
                    <option value="">系统默认输出</option>
                  )}
                  {speakerDevices.map(device => (
                    <option key={device.deviceId} value={device.deviceId}>
                      {device.label || `扬声器 ${device.deviceId.slice(0, 6)}`}
                    </option>
                  ))}
                </select>
                <p>点击播放测试音，观察波形线并确认听到声音。</p>
                <canvas
                  className="mic-wave-canvas"
                  ref={speakerCanvasRef}
                  width={460}
                  height={140}
                />
                <div className="gate-actions">
                  <button
                    type="button"
                    className="gate-btn is-secondary"
                    onClick={startSpeakerTest}
                  >
                    {speakerPlaying ? '播放中...' : '播放测试音'}
                  </button>
                  <button
                    type="button"
                    className="gate-btn is-enter"
                    onClick={() => passStep('speaker')}
                    disabled={!speakerReady}
                  >
                    我听到了声音
                  </button>
                </div>
              </>
            )}

            {modalType === 'mic' && (
              <>
                <label className="device-label" htmlFor="mic-select">
                  输入设备
                </label>
                <select
                  id="mic-select"
                  className="device-select"
                  value={selectedMic}
                  onChange={event => {
                    setSelectedMic(event.target.value);
                    setMicReady(false);
                  }}
                >
                  {micDevices.map(device => (
                    <option key={device.deviceId} value={device.deviceId}>
                      {device.label || `麦克风 ${device.deviceId.slice(0, 6)}`}
                    </option>
                  ))}
                </select>
                <p>请说话，观察线条是否随音量波动。</p>
                <canvas
                  className="mic-wave-canvas"
                  ref={micCanvasRef}
                  width={460}
                  height={140}
                />
                <p className="gate-hint">
                  {micWaveActive ? '检测中...' : '点击“开始测试”后开始检测'}
                </p>
                <div className="gate-actions">
                  <button
                    type="button"
                    className="gate-btn is-secondary"
                    onClick={startMicTest}
                  >
                    开始测试
                  </button>
                  <button
                    type="button"
                    className="gate-btn is-enter"
                    onClick={() => passStep('mic')}
                    disabled={!micReady}
                  >
                    波形正常，继续
                  </button>
                </div>
              </>
            )}

            {modalType === 'camera' && (
              <>
                <label className="device-label" htmlFor="camera-select">
                  摄像头设备
                </label>
                <select
                  id="camera-select"
                  className="device-select"
                  value={selectedCamera}
                  onChange={event => {
                    setSelectedCamera(event.target.value);
                    setCameraReady(false);
                  }}
                >
                  {cameraDevices.map(device => (
                    <option key={device.deviceId} value={device.deviceId}>
                      {device.label || `摄像头 ${device.deviceId.slice(0, 6)}`}
                    </option>
                  ))}
                </select>
                <p>请确认预览画面正常。</p>
                <video
                  className="camera-preview"
                  ref={cameraVideoRef}
                  autoPlay
                  muted
                  playsInline
                />
                <div className="gate-actions">
                  <button
                    type="button"
                    className="gate-btn is-secondary"
                    onClick={startCameraPreview}
                  >
                    开始预览
                  </button>
                  <button
                    type="button"
                    className="gate-btn is-enter"
                    onClick={() => {
                      stopStream(testMediaStreamRef.current);
                      testMediaStreamRef.current = null;
                      passStep('camera');
                    }}
                    disabled={!cameraReady}
                  >
                    画面正常，继续
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </main>
  );
};
