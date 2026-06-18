import React from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import {colors} from '../theme/colors';
import {SendIcon} from './icons';
import {VoiceHoldButton} from './VoiceHoldButton';

type Props = {
  draft: string;
  sending: boolean;
  voiceMode: boolean;
  onDraftChange: (text: string) => void;
  onSend: (text: string) => void;
  onVoiceError?: (message: string) => void;
  onInputFocus?: () => void;
};

export function ChatInputRow({
  draft,
  sending,
  voiceMode,
  onDraftChange,
  onSend,
  onVoiceError,
  onInputFocus,
}: Props) {
  const submit = () => {
    const text = draft.trim();
    if (!text || sending) {
      return;
    }
    onSend(text);
  };

  return (
    <View style={styles.row}>
      {voiceMode ? (
        <VoiceHoldButton
          disabled={sending}
          onSend={onSend}
          onError={onVoiceError}
        />
      ) : (
        <TextInput
          style={styles.input}
          placeholder="说点什么..."
          placeholderTextColor={colors.textPlaceholder}
          value={draft}
          editable={!sending}
          onChangeText={onDraftChange}
          onSubmitEditing={submit}
          onFocus={onInputFocus}
        />
      )}

      {!voiceMode ? (
        <Pressable
          style={[styles.sendButton, (sending || !draft.trim()) && styles.sendDisabled]}
          disabled={sending || draft.trim().length === 0}
          onPress={submit}>
          {sending ? (
            <ActivityIndicator color={colors.textPrimary} size="small" />
          ) : (
            <SendIcon />
          )}
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'center',
  },
  input: {
    flex: 1,
    minHeight: 44,
    borderRadius: 22,
    paddingHorizontal: 16,
    color: colors.textPrimary,
    backgroundColor: colors.inputBg,
    borderWidth: 1,
    borderColor: colors.panelBorder,
  },
  sendButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.sendButton,
    borderWidth: 1,
    borderColor: colors.sendButtonBorder,
  },
  sendDisabled: {
    opacity: 0.5,
  },
});
