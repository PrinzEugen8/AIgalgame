import React from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type NativeSyntheticEvent,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import {
  PhoneCameraPreview,
  type PhoneCameraErrorEvent,
  type PhoneCameraFacing,
  type PhoneCameraFrameEvent,
} from '../components/PhoneCameraPreview';
import {
  MicIcon,
  MicOffIcon,
  PhoneOffIcon,
  SwitchCameraIcon,
} from '../components/icons';
import type {RealtimeCallSnapshot} from '../realtime/realtimeCallClient';
import {colors} from '../theme/colors';

export type VideoCallPrimaryPane = 'remote' | 'local';

type Props = {
  snapshot: RealtimeCallSnapshot;
  companionName?: string;
  primaryPane: VideoCallPrimaryPane;
  cameraFacing: PhoneCameraFacing;
  onSwapPanes: () => void;
  onToggleMute: () => void;
  onToggleCameraFacing: () => void;
  onEnd: () => void;
  onFrame: (event: NativeSyntheticEvent<PhoneCameraFrameEvent>) => void;
  onCameraError: (event: NativeSyntheticEvent<PhoneCameraErrorEvent>) => void;
};

function formatElapsed(seconds: number): string {
  const safeSeconds = Math.max(0, seconds);
  const minutes = Math.floor(safeSeconds / 60);
  const rest = safeSeconds % 60;
  return `${minutes}:${rest.toString().padStart(2, '0')}`;
}

function statusText(snapshot: RealtimeCallSnapshot): string {
  if (snapshot.phase === 'connecting') {
    return 'Connecting';
  }
  if (snapshot.phase === 'ai_speaking') {
    return 'Companion speaking';
  }
  if (snapshot.phase === 'user_speaking') {
    return 'You are speaking';
  }
  if (snapshot.phase === 'error') {
    return snapshot.statusText || 'Call error';
  }
  return snapshot.statusText || 'Video call live';
}

export function VideoCallScreen({
  snapshot,
  companionName = 'Companion',
  primaryPane,
  cameraFacing,
  onSwapPanes,
  onToggleMute,
  onToggleCameraFacing,
  onEnd,
  onFrame,
  onCameraError,
}: Props) {
  const localIsPrimary = primaryPane === 'local';
  const cameraActive = snapshot.phase !== 'idle' && snapshot.phase !== 'ending';

  return (
    <View
      style={[
        styles.root,
        localIsPrimary ? styles.localPrimaryRoot : styles.remotePrimaryRoot,
      ]}
      pointerEvents="box-none">
      {localIsPrimary ? (
        <LocalCameraPane
          active={cameraActive}
          enabled={snapshot.cameraEnabled}
          cameraFacing={cameraFacing}
          style={styles.fullCamera}
          onFrame={onFrame}
          onCameraError={onCameraError}
        />
      ) : null}

      <SafeAreaView edges={['top']} style={styles.topBar} pointerEvents="auto">
        <View style={styles.callInfo}>
          <View style={styles.liveDot} />
          <View style={styles.callTextBlock}>
            <Text style={styles.name} numberOfLines={1}>
              {companionName}
            </Text>
            <Text style={styles.status} numberOfLines={1}>
              {statusText(snapshot)} | {formatElapsed(snapshot.elapsedSeconds)}
            </Text>
          </View>
        </View>
      </SafeAreaView>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Swap video panes"
        style={styles.pip}
        onPress={onSwapPanes}>
        {localIsPrimary ? (
          <View style={styles.remotePipHitLayer} />
        ) : (
          <LocalCameraPane
            active={cameraActive}
            enabled={snapshot.cameraEnabled}
            cameraFacing={cameraFacing}
            style={styles.pipCamera}
            onFrame={onFrame}
            onCameraError={onCameraError}
          />
        )}
        <View style={styles.pipBadge}>
          <Text style={styles.pipBadgeText}>
            {localIsPrimary ? 'Her' : 'You'}
          </Text>
        </View>
      </Pressable>

      <SafeAreaView
        edges={['bottom']}
        style={styles.bottomBar}
        pointerEvents="box-none">
        <View style={styles.controls} pointerEvents="auto">
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Toggle microphone"
            style={[styles.controlButton, snapshot.muted && styles.controlButtonActive]}
            onPress={onToggleMute}>
            {snapshot.muted ? <MicOffIcon /> : <MicIcon />}
          </Pressable>

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="End video call"
            style={[styles.controlButton, styles.endButton]}
            onPress={onEnd}>
            <PhoneOffIcon />
          </Pressable>

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Switch phone camera"
            style={styles.controlButton}
            onPress={onToggleCameraFacing}>
            <SwitchCameraIcon />
          </Pressable>
        </View>
      </SafeAreaView>
    </View>
  );
}

function LocalCameraPane({
  active,
  enabled,
  cameraFacing,
  style,
  onFrame,
  onCameraError,
}: {
  active: boolean;
  enabled: boolean;
  cameraFacing: PhoneCameraFacing;
  style: StyleProp<ViewStyle>;
  onFrame: (event: NativeSyntheticEvent<PhoneCameraFrameEvent>) => void;
  onCameraError: (event: NativeSyntheticEvent<PhoneCameraErrorEvent>) => void;
}) {
  return (
    <View style={[styles.localPane, style]}>
      {enabled && active ? (
        <PhoneCameraPreview
          style={styles.localImage}
          active={active}
          cameraEnabled={enabled}
          cameraFacing={cameraFacing}
          frameIntervalMs={320}
          frameJpegQuality={68}
          frameMaxLongEdge={640}
          onFrame={onFrame}
          onCameraError={onCameraError}
        />
      ) : (
        <View style={styles.cameraPlaceholder}>
          <Text style={styles.cameraPlaceholderText}>Camera off</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 40,
    elevation: 40,
  },
  remotePrimaryRoot: {
    backgroundColor: 'transparent',
  },
  localPrimaryRoot: {
    backgroundColor: '#000',
  },
  fullCamera: {
    ...StyleSheet.absoluteFillObject,
  },
  topBar: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    paddingHorizontal: 16,
    paddingBottom: 12,
    backgroundColor: 'rgba(0,0,0,0.28)',
    zIndex: 65,
    elevation: 65,
  },
  callInfo: {
    minHeight: 54,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  liveDot: {
    width: 9,
    height: 9,
    borderRadius: 4.5,
    backgroundColor: '#58d68d',
  },
  callTextBlock: {
    flex: 1,
    minWidth: 0,
  },
  name: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: '700',
  },
  status: {
    marginTop: 3,
    color: colors.textSecondary,
    fontSize: 12,
  },
  pip: {
    position: 'absolute',
    top: 92,
    right: 14,
    width: 116,
    height: 164,
    borderRadius: 10,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.28)',
    backgroundColor: 'rgba(0,0,0,0.28)',
    zIndex: 70,
    elevation: 70,
  },
  pipCamera: {
    ...StyleSheet.absoluteFillObject,
  },
  remotePipHitLayer: {
    flex: 1,
    backgroundColor: 'transparent',
  },
  pipBadge: {
    position: 'absolute',
    left: 6,
    bottom: 6,
    minWidth: 26,
    height: 20,
    borderRadius: 10,
    paddingHorizontal: 7,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(0,0,0,0.58)',
  },
  pipBadgeText: {
    color: colors.textPrimary,
    fontSize: 11,
    fontWeight: '700',
  },
  localPane: {
    overflow: 'hidden',
    backgroundColor: '#05070a',
  },
  localImage: {
    width: '100%',
    height: '100%',
  },
  cameraPlaceholder: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#05070a',
  },
  cameraPlaceholderText: {
    color: colors.textMuted,
    fontSize: 12,
    fontWeight: '700',
  },
  bottomBar: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: 20,
    paddingTop: 18,
    backgroundColor: 'rgba(0,0,0,0.28)',
    zIndex: 65,
    elevation: 65,
  },
  controls: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 26,
    paddingBottom: 18,
  },
  controlButton: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.14)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.16)',
  },
  controlButtonActive: {
    backgroundColor: 'rgba(255,255,255,0.24)',
  },
  endButton: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: 'rgba(224, 57, 57, 0.9)',
    borderColor: 'rgba(255,255,255,0.22)',
  },
});
