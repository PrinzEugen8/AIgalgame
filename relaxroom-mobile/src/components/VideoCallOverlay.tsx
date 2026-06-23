import React from 'react';
import {Pressable, StyleSheet, Text, View} from 'react-native';
import {colors} from '../theme/colors';
import type {RealtimeCallSnapshot} from '../realtime/realtimeCallClient';
import {CameraIcon, CameraOffIcon, MicIcon, MicOffIcon, PhoneOffIcon} from './icons';

type Props = {
  snapshot: RealtimeCallSnapshot;
  bottomOffset: number;
  onToggleMute: () => void;
  onToggleCamera: () => void;
  onEnd: () => void;
};

function formatElapsed(seconds: number): string {
  const safeSeconds = Math.max(0, seconds);
  const minutes = Math.floor(safeSeconds / 60);
  const rest = safeSeconds % 60;
  return `${minutes}:${rest.toString().padStart(2, '0')}`;
}

export function VideoCallOverlay({
  snapshot,
  bottomOffset,
  onToggleMute,
  onToggleCamera,
  onEnd,
}: Props) {
  const live = snapshot.phase !== 'idle';
  if (!live) {
    return null;
  }

  const isSpeaking = snapshot.phase === 'ai_speaking';

  return (
    <View
      style={[styles.container, {bottom: bottomOffset}]}
      pointerEvents="box-none">
      <View style={styles.hud} pointerEvents="auto">
        <View style={styles.statusRow}>
          <View style={[styles.liveDot, isSpeaking && styles.liveDotSpeaking]} />
          <View style={styles.statusTextBlock}>
            <Text style={styles.title} numberOfLines={1}>
              {snapshot.statusText || 'Video call'}
            </Text>
            <Text style={styles.meta} numberOfLines={1}>
              {formatElapsed(snapshot.elapsedSeconds)} | {snapshot.model} | {snapshot.voice}
            </Text>
          </View>
        </View>

        <View style={styles.controls}>
          <Pressable style={styles.controlButton} onPress={onToggleMute}>
            {snapshot.muted ? <MicOffIcon /> : <MicIcon />}
          </Pressable>
          <Pressable style={styles.controlButton} onPress={onToggleCamera}>
            {snapshot.cameraEnabled ? <CameraIcon /> : <CameraOffIcon />}
          </Pressable>
          <Pressable style={[styles.controlButton, styles.endButton]} onPress={onEnd}>
            <PhoneOffIcon />
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    left: 12,
    right: 12,
    zIndex: 35,
    elevation: 35,
  },
  hud: {
    minHeight: 68,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.glassCardBorder,
    backgroundColor: 'rgba(8, 10, 16, 0.74)',
    paddingHorizontal: 12,
    paddingVertical: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  statusRow: {
    flex: 1,
    minWidth: 0,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
  },
  liveDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#58d68d',
  },
  liveDotSpeaking: {
    backgroundColor: '#9ec7ff',
  },
  statusTextBlock: {
    flex: 1,
    minWidth: 0,
  },
  title: {
    color: colors.textPrimary,
    fontSize: 13,
    fontWeight: '700',
  },
  meta: {
    marginTop: 3,
    color: colors.textMuted,
    fontSize: 11,
  },
  controls: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  controlButton: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.panelBorder,
    backgroundColor: colors.iconButtonBg,
  },
  endButton: {
    backgroundColor: 'rgba(225, 64, 64, 0.78)',
    borderColor: 'rgba(255, 145, 145, 0.45)',
  },
});
