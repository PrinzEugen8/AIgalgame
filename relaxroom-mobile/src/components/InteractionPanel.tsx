import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Alert, Keyboard, Platform, StyleSheet, Text, View} from 'react-native';
import {launchImageLibrary} from 'react-native-image-picker';
import {ChatInputRow} from './ChatInputRow';
import {ChatMessageList, type ChatMessageListHandle} from './ChatMessageList';
import {FunctionButtonRow} from './FunctionButtonRow';
import {ImagePreviewChip} from './ImagePreviewChip';
import {QuickReplyBar} from './QuickReplyBar';
import {colors} from '../theme/colors';
import type {ChatMessage, LazyReply} from '../types/dialogue';

type Props = {
  maxHeight: number;
  bottomInset: number;
  messages: ChatMessage[];
  sending: boolean;
  statusText?: string;
  normalReplies: LazyReply[];
  keyReplies: LazyReply[];
  draft: string;
  onDraftChange: (text: string) => void;
  onSend: (text: string) => void;
  onQuickReplySelect: (text: string) => void;
  onPhone: () => void;
};

export function InteractionPanel({
  maxHeight,
  bottomInset,
  messages,
  sending,
  statusText,
  normalReplies,
  keyReplies,
  draft,
  onDraftChange,
  onSend,
  onQuickReplySelect,
  onPhone,
}: Props) {
  const [voiceMode, setVoiceMode] = useState(false);
  const [imagePreviewUri, setImagePreviewUri] = useState<string | null>(null);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  const messageListRef = useRef<ChatMessageListHandle>(null);

  useEffect(() => {
    const showEvent =
      Platform.OS === 'ios' ? 'keyboardWillShow' : 'keyboardDidShow';
    const hideEvent =
      Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide';

    const showSub = Keyboard.addListener(showEvent, event => {
      setKeyboardHeight(event.endCoordinates.height);
    });
    const hideSub = Keyboard.addListener(hideEvent, () => {
      setKeyboardHeight(0);
    });

    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, []);

  const handlePickImage = useCallback(async () => {
    const result = await launchImageLibrary({
      mediaType: 'photo',
      selectionLimit: 1,
      quality: 0.8,
    });
    if (result.didCancel || !result.assets?.[0]?.uri) {
      return;
    }
    setImagePreviewUri(result.assets[0].uri);
  }, []);

  const handleVoiceError = useCallback((message: string) => {
    Alert.alert('语音消息', message);
  }, []);

  return (
    <View
      style={[
        styles.container,
        {
          bottom: keyboardHeight,
          height: maxHeight,
          paddingBottom: keyboardHeight > 0 ? 8 : Math.max(bottomInset, 8),
        },
      ]}
      pointerEvents="box-none">
      <View style={styles.panel} pointerEvents="auto">
        {statusText ? (
          <Text style={styles.status} numberOfLines={1}>
            {statusText}
          </Text>
        ) : null}

        <View style={styles.history}>
          <ChatMessageList ref={messageListRef} messages={messages} />
        </View>

        <QuickReplyBar
          normalReplies={normalReplies}
          keyReplies={keyReplies}
          disabled={sending}
          onSelect={onQuickReplySelect}
        />

        {imagePreviewUri ? (
          <ImagePreviewChip
            uri={imagePreviewUri}
            onRemove={() => setImagePreviewUri(null)}
          />
        ) : null}

        <ChatInputRow
          draft={draft}
          sending={sending}
          voiceMode={voiceMode}
          onDraftChange={onDraftChange}
          onSend={onSend}
          onVoiceError={handleVoiceError}
          onInputFocus={() => {
            messageListRef.current?.scrollToEnd();
          }}
        />

        <FunctionButtonRow
          voiceMode={voiceMode}
          onToggleVoice={() => setVoiceMode(current => !current)}
          onPickImage={() => {
            void handlePickImage();
          }}
          onPhone={onPhone}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 20,
    elevation: 20,
  },
  panel: {
    marginHorizontal: 12,
    marginBottom: 8,
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 10,
    borderRadius: 18,
    backgroundColor: colors.panelBg,
    borderWidth: 1,
    borderColor: colors.panelBorder,
    flex: 1,
  },
  status: {
    color: colors.accentSoft,
    fontSize: 11,
    marginBottom: 4,
  },
  history: {
    flex: 1,
    minHeight: 48,
    marginBottom: 4,
  },
});
