import {NativeModules, Platform} from 'react-native';

type AudioOutputNativeModule = {
  start: (sampleRate: number) => Promise<void>;
  playPcm16Base64: (audio: string) => Promise<void>;
  stop: () => Promise<void>;
};

const NativeAudioOutput =
  Platform.OS === 'android'
    ? (NativeModules.RelaxRoomAudioOutput as AudioOutputNativeModule | undefined)
    : undefined;

export async function startAndroidAudioOutput(sampleRate = 24000): Promise<void> {
  if (NativeAudioOutput == null) {
    return;
  }
  await NativeAudioOutput.start(sampleRate);
}

export function playAndroidPcm16Base64(audio: string): void {
  if (NativeAudioOutput == null || audio.length === 0) {
    return;
  }

  void NativeAudioOutput.playPcm16Base64(audio);
}

export async function stopAndroidAudioOutput(): Promise<void> {
  if (NativeAudioOutput == null) {
    return;
  }

  try {
    await NativeAudioOutput.stop();
  } catch {
    // Stop is best-effort during call teardown.
  }
}
