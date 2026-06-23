import React from 'react';
import {Pressable, StyleSheet, View} from 'react-native';
import {colors} from '../theme/colors';
import {ImageIcon, MicIcon, PhoneIcon, VideoIcon} from './icons';

type Props = {
  voiceMode: boolean;
  videoCallActive: boolean;
  onToggleVoice: () => void;
  onPickImage: () => void;
  onPhone: () => void;
  onVideoCall: () => void;
};

export function FunctionButtonRow({
  voiceMode,
  videoCallActive,
  onToggleVoice,
  onPickImage,
  onPhone,
  onVideoCall,
}: Props) {
  return (
    <View style={styles.row}>
      <Pressable
        style={[styles.button, voiceMode && styles.buttonActive]}
        onPress={onToggleVoice}>
        <MicIcon color={voiceMode ? colors.textPrimary : colors.textSecondary} />
      </Pressable>
      <Pressable style={styles.button} onPress={onPickImage}>
        <ImageIcon />
      </Pressable>
      <Pressable style={styles.button} onPress={onPhone}>
        <PhoneIcon />
      </Pressable>
      <Pressable
        style={[styles.button, videoCallActive && styles.buttonActive]}
        onPress={onVideoCall}>
        <VideoIcon color={videoCallActive ? colors.textPrimary : colors.textSecondary} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: 16,
    justifyContent: 'center',
    paddingTop: 4,
  },
  button: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.iconButtonBg,
    borderWidth: 1,
    borderColor: colors.panelBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonActive: {
    backgroundColor: colors.iconActive,
    borderColor: colors.sendButtonBorder,
  },
});
