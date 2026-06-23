import {buildQuery, getSession} from '../api/client';
import {fetchRealtimeCallConfig} from '../api/relaxroom';
import type {RealtimeCallConfig} from '../types/dialogue';
import {startAndroidAudioCapture} from './androidAudioCapture';
import {
  playAndroidPcm16Base64,
  startAndroidAudioOutput,
  stopAndroidAudioOutput,
} from './androidAudioOutput';

export type RealtimeCallPhase =
  | 'idle'
  | 'connecting'
  | 'live'
  | 'ai_speaking'
  | 'user_speaking'
  | 'ending'
  | 'error';

export type RealtimeCallSnapshot = {
  phase: RealtimeCallPhase;
  statusText: string;
  elapsedSeconds: number;
  muted: boolean;
  cameraEnabled: boolean;
  configured: boolean;
  model: string;
  voice: string;
  providerId: string;
};

type UnityCallEvent =
  | 'begin'
  | 'end'
  | 'ai_speaking_start'
  | 'ai_speaking_stop'
  | 'user_speaking_start'
  | 'user_speaking_stop';

type ControllerOptions = {
  onSnapshot: (snapshot: RealtimeCallSnapshot) => void;
  onUnityEvent: (event: UnityCallEvent) => void;
};

const EMPTY_CONFIG: RealtimeCallConfig = {};
const DEFAULT_MODEL = 'qwen3.5-omni-flash-realtime-2026-03-15';
const DEFAULT_VOICE = 'Momo';

function toWebSocketBaseUrl(baseUrl: string): string {
  const trimmed = baseUrl.replace(/\/$/, '');
  if (trimmed.startsWith('https://')) {
    return `wss://${trimmed.slice('https://'.length)}`;
  }
  if (trimmed.startsWith('http://')) {
    return `ws://${trimmed.slice('http://'.length)}`;
  }
  return trimmed;
}

function realtimeWsUrl(config: RealtimeCallConfig): string {
  const session = getSession();
  const endpoint = config.transport?.websocket_endpoint || '/api/realtime/call/ws';
  const query = buildQuery({
    user_id: session.userId,
    character_id: session.characterId,
    session_id: session.sessionId,
  });
  return `${toWebSocketBaseUrl(session.backendBaseUrl)}${endpoint}?${query}`;
}

export class RealtimeCallController {
  private readonly onSnapshot: ControllerOptions['onSnapshot'];
  private readonly onUnityEvent: ControllerOptions['onUnityEvent'];
  private phase: RealtimeCallPhase = 'idle';
  private startedAt = 0;
  private muted = false;
  private cameraEnabled = true;
  private config: RealtimeCallConfig = EMPTY_CONFIG;
  private relaySocket: WebSocket | null = null;
  private tickTimer: ReturnType<typeof setInterval> | null = null;
  private pulseTimer: ReturnType<typeof setTimeout> | null = null;
  private aiStopTimer: ReturnType<typeof setTimeout> | null = null;
  private endTimer: ReturnType<typeof setTimeout> | null = null;
  private demoPulseMode = false;
  private lastErrorText = '';
  private hasSentAudio = false;
  private stopAudioCapture: (() => Promise<void>) | null = null;
  private audioOutputStarted = false;

  constructor(options: ControllerOptions) {
    this.onSnapshot = options.onSnapshot;
    this.onUnityEvent = options.onUnityEvent;
  }

  async start() {
    if (this.phase !== 'idle' && this.phase !== 'error') {
      return;
    }

    this.clearTimers();
    this.closeRelaySocket();
    void this.stopAudioCaptureIfNeeded();
    void this.stopAudioOutputIfNeeded();
    this.demoPulseMode = false;
    this.lastErrorText = '';
    this.hasSentAudio = false;
    this.phase = 'connecting';
    this.startedAt = Date.now();
    this.emit('Connecting Qwen Realtime...');

    try {
      this.config = await fetchRealtimeCallConfig();
    } catch (error) {
      this.config = {
        configured: false,
        reason: error instanceof Error ? error.message : 'Realtime config unavailable',
      };
    }

    this.onUnityEvent('begin');

    if (this.config.configured) {
      try {
        await this.connectRelay();
        return;
      } catch (error) {
        this.phase = 'error';
        this.lastErrorText =
          error instanceof Error ? error.message : 'Qwen relay connection failed';
        this.onUnityEvent('end');
        this.emit(this.lastErrorText);
        return;
      }
    }

    this.demoPulseMode = true;
    this.phase = 'live';
    this.startTicking();
    this.scheduleAiPulse(1200);
    this.emit(this.config.reason || 'Demo mode: Qwen Realtime provider is not configured');
  }

  end() {
    if (this.phase === 'idle') {
      return;
    }

    this.clearTimers();
    this.phase = 'ending';
    this.emit('Ending call...');
    this.sendRelayEvent({type: 'call.end'});
    void this.stopAudioCaptureIfNeeded();
    void this.stopAudioOutputIfNeeded();
    this.closeRelaySocket();
    this.onUnityEvent('ai_speaking_stop');
    this.onUnityEvent('user_speaking_stop');
    this.onUnityEvent('end');

    this.endTimer = setTimeout(() => {
      this.endTimer = null;
      this.phase = 'idle';
      this.startedAt = 0;
      this.lastErrorText = '';
      this.hasSentAudio = false;
      this.emit('');
    }, 220);
  }

  toggleMute() {
    this.muted = !this.muted;
    if (this.phase === 'idle') {
      this.emit('');
      return;
    }
    if (this.muted) {
      this.sendRelayEvent({type: 'input_audio_buffer.clear'});
      this.onUnityEvent('user_speaking_stop');
    }
    this.emit(this.muted ? 'Mic muted' : 'Mic live');
  }

  toggleCamera() {
    this.cameraEnabled = !this.cameraEnabled;
    this.emit(this.cameraEnabled ? 'Camera snapshots enabled' : 'Camera snapshots paused');
  }

  sendAudioPcm16Base64(audio: string) {
    if (this.muted || this.phase === 'idle' || this.phase === 'ending') {
      return;
    }
    if (audio.length > 0) {
      this.hasSentAudio = true;
    }
    this.sendRelayEvent({type: 'input_audio_buffer.append', audio});
  }

  commitAudioTurn() {
    this.sendRelayEvent({type: 'input_audio_buffer.commit'});
    this.sendRelayEvent({type: 'response.create'});
  }

  sendImageJpegBase64(image: string) {
    if (!this.cameraEnabled || this.phase === 'idle' || this.phase === 'ending') {
      return;
    }
    if (!this.hasSentAudio) {
      return;
    }
    this.sendRelayEvent({type: 'input_image_buffer.append', image});
  }

  dispose() {
    this.clearTimers();
    void this.stopAudioCaptureIfNeeded();
    void this.stopAudioOutputIfNeeded();
    this.closeRelaySocket();
  }

  private async startAudioCaptureIfNeeded() {
    if (this.stopAudioCapture != null) {
      return;
    }

    this.stopAudioCapture = await startAndroidAudioCapture({
      onFrame: audio => {
        this.sendAudioPcm16Base64(audio);
      },
      onError: message => {
        if (this.phase === 'idle' || this.phase === 'ending') {
          return;
        }
        this.lastErrorText = message;
        this.emit(message);
      },
    });
  }

  private async stopAudioCaptureIfNeeded() {
    const stop = this.stopAudioCapture;
    if (stop == null) {
      return;
    }

    this.stopAudioCapture = null;
    await stop();
  }

  private async startAudioOutputIfNeeded() {
    if (this.audioOutputStarted) {
      return;
    }

    try {
      await startAndroidAudioOutput(this.config.audio?.output_sample_rate || 24000);
      this.audioOutputStarted = true;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Android audio output failed';
      if (this.phase !== 'idle' && this.phase !== 'ending') {
        this.lastErrorText = message;
        this.emit(message);
      }
    }
  }

  private async stopAudioOutputIfNeeded() {
    this.audioOutputStarted = false;
    await stopAndroidAudioOutput();
  }

  private connectRelay(): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(realtimeWsUrl(this.config));
      this.relaySocket = ws;
      let settled = false;
      const settle = (fn: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        fn();
      };
      const timeout = setTimeout(() => {
        settle(() => {
          this.closeRelaySocket();
          reject(new Error('Qwen relay connection timed out'));
        });
      }, 12000);

      ws.onopen = () => {
        settle(() => {
          clearTimeout(timeout);
          this.phase = 'live';
          this.startTicking();
          void this.startAudioCaptureIfNeeded();
          void this.startAudioOutputIfNeeded();
          this.sendRelayEvent({type: 'client.ping'});
          this.emit(`Qwen relay connected: ${this.config.model || DEFAULT_MODEL}`);
          resolve();
        });
      };

      ws.onmessage = event => this.handleRelayMessage(event.data);

      ws.onerror = () => {
        settle(() => {
          clearTimeout(timeout);
          this.closeRelaySocket();
          reject(new Error('Qwen relay connection failed'));
        });
      };

      ws.onclose = event => {
        clearTimeout(timeout);
        if (this.phase !== 'ending' && this.phase !== 'idle' && this.phase !== 'error') {
          this.phase = 'error';
          void this.stopAudioCaptureIfNeeded();
          void this.stopAudioOutputIfNeeded();
          const closeEvent = event as WebSocketCloseEvent;
          this.lastErrorText =
            closeEvent.reason ||
            `Qwen relay disconnected${closeEvent.code ? ` (code ${closeEvent.code})` : ''}`;
          this.clearAiStopTimer();
          this.onUnityEvent('ai_speaking_stop');
          this.onUnityEvent('user_speaking_stop');
          this.onUnityEvent('end');
          this.emit(this.lastErrorText);
        }
      };
    });
  }

  private handleRelayMessage(data: unknown) {
    if (typeof data !== 'string') {
      return;
    }

    let event: Record<string, unknown>;
    try {
      event = JSON.parse(data) as Record<string, unknown>;
    } catch {
      return;
    }

    const eventType = String(event.type || '');
    if (eventType === 'relay.ready') {
      this.emit(`Qwen session ready: ${this.config.voice || DEFAULT_VOICE}`);
      return;
    }
    if (eventType === 'relay.error' || eventType === 'error') {
      this.phase = 'error';
      this.lastErrorText = String(event.message || 'Qwen realtime error');
      void this.stopAudioCaptureIfNeeded();
      void this.stopAudioOutputIfNeeded();
      this.clearAiStopTimer();
      this.onUnityEvent('ai_speaking_stop');
      this.onUnityEvent('user_speaking_stop');
      this.onUnityEvent('end');
      this.emit(this.lastErrorText);
      return;
    }
    if (eventType === 'session.updated') {
      this.emit('Qwen session updated');
      return;
    }
    if (eventType === 'input_audio_buffer.speech_started') {
      this.phase = 'user_speaking';
      this.onUnityEvent('ai_speaking_stop');
      this.onUnityEvent('user_speaking_start');
      this.emit('You are speaking');
      return;
    }
    if (
      eventType === 'input_audio_buffer.speech_stopped' ||
      eventType === 'conversation.item.input_audio_transcription.completed'
    ) {
      this.phase = 'live';
      this.onUnityEvent('user_speaking_stop');
      this.emit(this.statusForPhase());
      return;
    }
    if (
      eventType === 'response.audio.delta' ||
      eventType === 'response.output_audio.delta'
    ) {
      playAndroidPcm16Base64(this.audioDeltaFromEvent(event));
      this.markAiSpeaking();
      return;
    }
    if (
      eventType === 'response.created' ||
      eventType === 'response.audio_transcript.delta' ||
      eventType === 'response.output_audio_transcript.delta' ||
      eventType === 'response.text.delta'
    ) {
      this.markAiSpeaking();
      return;
    }
    if (
      eventType === 'response.audio.done' ||
      eventType === 'response.output_audio.done' ||
      eventType === 'response.audio_transcript.done' ||
      eventType === 'response.output_audio_transcript.done' ||
      eventType === 'response.text.done' ||
      eventType === 'response.done'
    ) {
      this.clearAiSpeakingSoon(420);
    }
  }

  private audioDeltaFromEvent(event: Record<string, unknown>): string {
    const directFields = ['delta', 'audio', 'output_audio', 'data'];
    for (const field of directFields) {
      const value = event[field];
      if (typeof value === 'string' && value.length > 0) {
        return value;
      }
    }
    return '';
  }

  private markAiSpeaking() {
    if (this.phase === 'ending' || this.phase === 'idle') {
      return;
    }
    this.clearAiStopTimer();
    if (this.phase !== 'ai_speaking') {
      this.phase = 'ai_speaking';
      this.onUnityEvent('user_speaking_stop');
      this.onUnityEvent('ai_speaking_start');
    }
    this.emit('AI speaking');
  }

  private clearAiSpeakingSoon(delayMs: number) {
    this.clearAiStopTimer();
    this.aiStopTimer = setTimeout(() => {
      this.aiStopTimer = null;
      if (this.phase === 'ai_speaking' || this.phase === 'user_speaking') {
        this.phase = this.phase === 'user_speaking' ? 'user_speaking' : 'live';
      }
      this.onUnityEvent('ai_speaking_stop');
      this.emit(this.statusForPhase());
    }, delayMs);
  }

  private sendRelayEvent(event: Record<string, unknown>) {
    if (this.relaySocket?.readyState === WebSocket.OPEN) {
      this.relaySocket.send(JSON.stringify(event));
    }
  }

  private closeRelaySocket() {
    if (this.relaySocket == null) {
      return;
    }
    const ws = this.relaySocket;
    this.relaySocket = null;
    ws.onopen = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.onclose = null;
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  }

  private startTicking() {
    if (this.tickTimer != null) {
      return;
    }
    this.tickTimer = setInterval(() => this.emit(this.statusForPhase()), 1000);
  }

  private scheduleAiPulse(delayMs: number) {
    this.clearPulseTimer();
    if (!this.demoPulseMode || this.phase === 'idle' || this.phase === 'ending') {
      return;
    }
    this.pulseTimer = setTimeout(() => {
      if (!this.demoPulseMode || this.phase !== 'live') {
        return;
      }
      this.phase = 'ai_speaking';
      this.onUnityEvent('ai_speaking_start');
      this.emit('AI speaking');
      this.pulseTimer = setTimeout(() => {
        if (this.phase === 'ai_speaking') {
          this.phase = 'live';
          this.onUnityEvent('ai_speaking_stop');
          this.emit(this.statusForPhase());
          this.scheduleAiPulse(9000);
        }
      }, 2600);
    }, delayMs);
  }

  private clearPulseTimer() {
    if (this.pulseTimer != null) {
      clearTimeout(this.pulseTimer);
      this.pulseTimer = null;
    }
  }

  private clearAiStopTimer() {
    if (this.aiStopTimer != null) {
      clearTimeout(this.aiStopTimer);
      this.aiStopTimer = null;
    }
  }

  private clearTimers() {
    if (this.tickTimer != null) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
    this.clearPulseTimer();
    this.clearAiStopTimer();
    if (this.endTimer != null) {
      clearTimeout(this.endTimer);
      this.endTimer = null;
    }
  }

  private statusForPhase(): string {
    if (this.phase === 'ai_speaking') {
      return 'AI speaking';
    }
    if (this.phase === 'user_speaking') {
      return 'You are speaking';
    }
    if (this.muted) {
      return 'Mic muted';
    }
    if (this.phase === 'error') {
      return this.lastErrorText || 'Realtime error';
    }
    if (!this.config.configured) {
      return this.config.reason || 'Demo mode';
    }
    return 'Qwen realtime live';
  }

  private emit(statusText: string) {
    this.onSnapshot({
      phase: this.phase,
      statusText,
      elapsedSeconds: this.startedAt > 0 ? Math.floor((Date.now() - this.startedAt) / 1000) : 0,
      muted: this.muted,
      cameraEnabled: this.cameraEnabled,
      configured: Boolean(this.config.configured),
      model: this.config.model || DEFAULT_MODEL,
      voice: this.config.voice || DEFAULT_VOICE,
      providerId: this.config.provider_id || '',
    });
  }
}
