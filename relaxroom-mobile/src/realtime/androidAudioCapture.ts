import {
  NativeEventEmitter,
  NativeModules,
  Platform,
  type EmitterSubscription,
} from 'react-native';

type AudioCaptureNativeModule = {
  start: (sampleRate: number, chunkMs: number) => Promise<void>;
  stop: () => Promise<void>;
  addListener: (eventName: string) => void;
  removeListeners: (count: number) => void;
};

type AudioFrameEvent = {
  audio?: string;
  sampleRate?: number;
  bytes?: number;
};

type AudioErrorEvent = {
  message?: string;
};

type AudioCaptureHandlers = {
  onFrame: (audioBase64: string) => void;
  onError: (message: string) => void;
};

const NativeAudioCapture =
  Platform.OS === 'android'
    ? (NativeModules.RelaxRoomAudioCapture as AudioCaptureNativeModule | undefined)
    : undefined;

const audioEmitter =
  NativeAudioCapture != null ? new NativeEventEmitter(NativeAudioCapture) : null;

export async function startAndroidAudioCapture({
  onFrame,
  onError,
}: AudioCaptureHandlers): Promise<() => Promise<void>> {
  if (NativeAudioCapture == null || audioEmitter == null) {
    onError('Android audio capture module is unavailable');
    return async () => {};
  }

  const subscriptions: EmitterSubscription[] = [
    audioEmitter.addListener('RelaxRoomAudioFrame', (event: AudioFrameEvent) => {
      if (event.audio) {
        onFrame(event.audio);
      }
    }),
    audioEmitter.addListener('RelaxRoomAudioError', (event: AudioErrorEvent) => {
      onError(event.message || 'Android audio capture failed');
    }),
  ];

  try {
    await NativeAudioCapture.start(16000, 60);
  } catch (error) {
    subscriptions.forEach(subscription => subscription.remove());
    onError(error instanceof Error ? error.message : 'Android audio capture failed');
    return async () => {};
  }

  return async () => {
    subscriptions.forEach(subscription => subscription.remove());
    try {
      await NativeAudioCapture.stop();
    } catch {
      // Stop is best-effort during call teardown.
    }
  };
}
