import React from 'react';
import {
  Platform,
  requireNativeComponent,
  StyleSheet,
  Text,
  type NativeSyntheticEvent,
  type StyleProp,
  type ViewProps,
  type ViewStyle,
  View,
} from 'react-native';
import {colors} from '../theme/colors';

export type PhoneCameraFacing = 'front' | 'back';

export type PhoneCameraFrameEvent = {
  image: string;
  facing: PhoneCameraFacing;
  width: number;
  height: number;
  rotationDegrees: number;
};

export type PhoneCameraErrorEvent = {
  message: string;
  facing?: PhoneCameraFacing;
};

type NativeProps = ViewProps & {
  style?: StyleProp<ViewStyle>;
  active: boolean;
  cameraEnabled: boolean;
  cameraFacing: PhoneCameraFacing;
  frameIntervalMs: number;
  frameJpegQuality: number;
  frameMaxLongEdge: number;
  onFrame?: (event: NativeSyntheticEvent<PhoneCameraFrameEvent>) => void;
  onCameraError?: (event: NativeSyntheticEvent<PhoneCameraErrorEvent>) => void;
};

const NativePhoneCameraPreview =
  Platform.OS === 'android'
    ? requireNativeComponent<NativeProps>('RelaxRoomCameraPreview')
    : null;

type Props = ViewProps & {
  style?: StyleProp<ViewStyle>;
  active: boolean;
  cameraEnabled?: boolean;
  cameraFacing: PhoneCameraFacing;
  frameIntervalMs?: number;
  frameJpegQuality?: number;
  frameMaxLongEdge?: number;
  onFrame?: (event: NativeSyntheticEvent<PhoneCameraFrameEvent>) => void;
  onCameraError?: (event: NativeSyntheticEvent<PhoneCameraErrorEvent>) => void;
};

export function PhoneCameraPreview({
  style,
  active,
  cameraEnabled = true,
  cameraFacing,
  frameIntervalMs = 0,
  frameJpegQuality = 72,
  frameMaxLongEdge = 640,
  onFrame,
  onCameraError,
}: Props) {
  if (NativePhoneCameraPreview == null) {
    return (
      <View pointerEvents="none" style={[styles.fallback, style]}>
        <Text style={styles.fallbackText}>Camera preview is Android only</Text>
      </View>
    );
  }

  return (
    <NativePhoneCameraPreview
      pointerEvents="none"
      style={style}
      active={active}
      cameraEnabled={cameraEnabled}
      cameraFacing={cameraFacing}
      frameIntervalMs={frameIntervalMs}
      frameJpegQuality={frameJpegQuality}
      frameMaxLongEdge={frameMaxLongEdge}
      onFrame={onFrame}
      onCameraError={onCameraError}
    />
  );
}

const styles = StyleSheet.create({
  fallback: {
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#0b0d12',
  },
  fallbackText: {
    color: colors.textMuted,
    fontSize: 12,
  },
});
