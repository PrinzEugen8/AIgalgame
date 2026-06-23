import type {RefObject} from 'react';
import type UnityView from '@azesmway/react-native-unity';
import type {
  DialogueLine,
  SessionConfig,
  UnityBridgeEvent,
} from '../types/dialogue';
import {
  logStartupStage,
  mergeUnityTimeline,
  maybeLogStartupSummary,
  recordMilestone,
} from './startupDiagnostics';

const BRIDGE_OBJECT = 'RelaxRoomBridge';
const BRIDGE_METHOD = 'OnCommand';

type UnityViewRef = RefObject<UnityView | null>;

type QueuedCommand = {
  cmd: string;
  payload: Record<string, unknown>;
};

let unityRef: UnityViewRef | null = null;
let bridgeReady = false;
let roomReady = false;
const commandQueue: QueuedCommand[] = [];
const listeners = new Set<(event: UnityBridgeEvent) => void>();

export function bindUnityRef(ref: UnityViewRef) {
  unityRef = ref;
}

export function subscribeUnityEvents(listener: (event: UnityBridgeEvent) => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function sendCommand(cmd: string, payload: Record<string, unknown> = {}) {
  const message = JSON.stringify({
    cmd,
    id: `${Date.now()}`,
    ...payload,
  });
  unityRef?.current?.postMessage(BRIDGE_OBJECT, BRIDGE_METHOD, message);
}

function flushCommandQueue() {
  const pending = commandQueue.splice(0, commandQueue.length);
  if (pending.length > 0) {
    logStartupStage('bridge_queue_flushed', `count=${pending.length}`);
  }
  for (const item of pending) {
    sendCommand(item.cmd, item.payload);
  }
}

function postCommand(cmd: string, payload: Record<string, unknown> = {}) {
  if (!bridgeReady) {
    commandQueue.push({cmd, payload});
    return;
  }
  sendCommand(cmd, payload);
}

export function handleUnityMessage(raw: string) {
  try {
    const event = JSON.parse(raw) as UnityBridgeEvent;
    if (event.evt === 'startup_milestone') {
      recordMilestone(
        event.source === 'unity' ? 'unity' : 'native',
        event.stage ?? 'unknown',
        event.elapsed_ms ?? 0,
        event.detail,
      );
      listeners.forEach(listener => listener(event));
      return;
    }

    if (event.evt === 'startup_timeline' && event.stages) {
      mergeUnityTimeline(event.stages);
      listeners.forEach(listener => listener(event));
      return;
    }

    if (event.evt === 'room_ready' || event.evt === 'ready') {
      if (!roomReady) {
        roomReady = true;
        logStartupStage('room_ready', event.state);
      }
      bridgeReady = true;
      flushCommandQueue();
    }
    if (event.evt === 'environment_ready') {
      logStartupStage('environment_ready', event.state);
    }
    if (event.evt === 'stable_frame') {
      logStartupStage('stable_frame', event.state);
      maybeLogStartupSummary('stable_frame');
    }
    listeners.forEach(listener => listener(event));
  } catch (error) {
    listeners.forEach(listener =>
      listener({
        evt: 'error',
        text: error instanceof Error ? error.message : 'Invalid Unity message',
      }),
    );
  }
}

export function configureUnity(session: SessionConfig) {
  logStartupStage('configure_sent');
  postCommand('configure', {
    backend_base_url: session.backendBaseUrl,
    user_id: session.userId,
    character_id: session.characterId,
    session_id: session.sessionId,
    appearance_id: session.appearanceId,
  });
}

export function beginReply() {
  postCommand('avatar.reply.begin');
}

export function receivedReply(hasDialogue: boolean) {
  postCommand('avatar.reply.received', {has_dialogue: hasDialogue});
}

export function endReply() {
  postCommand('avatar.reply.end');
}

export function playLine(line: DialogueLine) {
  postCommand('avatar.play_line', {line});
}

export function playIdleAction(motion: string, durationSeconds?: number) {
  postCommand('motion.idle_action', {
    motion,
    duration_seconds: durationSeconds ?? 0,
  });
}

export function playPhoneNotification() {
  postCommand('motion.phone_notification');
}

export function enterSleepMode() {
  postCommand('sleep.enter');
}

export function exitSleepMode() {
  postCommand('sleep.exit');
}

export function notifyWhileSleeping(durationSeconds?: number) {
  postCommand('sleep.notify', {
    duration_seconds: durationSeconds ?? 0,
  });
}

export function switchCameraPrevious() {
  postCommand('camera.prev');
}

export function switchCameraNext() {
  postCommand('camera.next');
}

export function setUnityCamera(name: string) {
  postCommand('camera.set', {text: name});
}

export function beginVideoCall() {
  postCommand('video_call.begin');
}

export function endVideoCall() {
  postCommand('video_call.end');
}

export function setVideoCallAiSpeaking(active: boolean) {
  postCommand(active ? 'video_call.ai_speaking_start' : 'video_call.ai_speaking_stop');
}

export function setVideoCallUserSpeaking(active: boolean) {
  postCommand(active ? 'video_call.user_speaking_start' : 'video_call.user_speaking_stop');
}

export function setVideoCallExpression(emotion: string, intensity = 0.75) {
  postCommand('video_call.expression', {
    emotion,
    intensity,
  });
}

export function startLocalAsr(): string {
  const id = `asr-${Date.now()}`;
  postCommand('asr.start', {id});
  return id;
}

export function stopLocalAsr(id: string) {
  postCommand('asr.stop', {id});
}

export function waitForLocalAsrResult(
  id: string,
  timeoutMs = 30000,
): Promise<{ok: boolean; text?: string; error?: string}> {
  return new Promise(resolve => {
    let settled = false;
    const finish = (result: {ok: boolean; text?: string; error?: string}) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      unsubscribe();
      resolve(result);
    };

    const unsubscribe = subscribeUnityEvents(event => {
      if (event.id !== id) {
        return;
      }
      if (event.evt === 'asr.finished') {
        const text = event.text?.trim();
        if (text) {
          finish({ok: true, text});
        } else {
          finish({ok: false, error: '没有识别出内容'});
        }
        return;
      }
      if (event.evt === 'asr.error') {
        finish({ok: false, error: event.text || '语音识别失败'});
      }
    });

    const timer = setTimeout(() => {
      finish({ok: false, error: '语音识别超时'});
    }, timeoutMs);
  });
}

export function setAvatarIdle() {
  postCommand('avatar.idle');
}

export async function playLinesSequentially(
  lines: DialogueLine[],
  onLineFinished?: (line: DialogueLine) => void,
): Promise<void> {
  if (lines.length === 0) {
    endReply();
    return;
  }

  receivedReply(true);

  for (const line of lines) {
    await new Promise<void>(resolve => {
      let settled = false;
      const unsubscribe = subscribeUnityEvents(event => {
        if (event.evt !== 'line.finished' || settled) {
          return;
        }
        if (!line.line_id || event.line_id === line.line_id) {
          settled = true;
          unsubscribe();
          onLineFinished?.(line);
          resolve();
        }
      });
      playLine(line);
      setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        unsubscribe();
        resolve();
      }, 30000);
    });
  }

  endReply();
}
