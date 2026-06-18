import React, {useEffect, useImperativeHandle, useRef, forwardRef} from 'react';
import {FlatList, StyleSheet, Text, View} from 'react-native';
import {colors} from '../theme/colors';
import type {ChatMessage} from '../types/dialogue';

export type ChatMessageListHandle = {
  scrollToEnd: () => void;
};

type Props = {
  messages: ChatMessage[];
};

export const ChatMessageList = forwardRef<ChatMessageListHandle, Props>(
  function ChatMessageList({messages}, ref) {
    const listRef = useRef<FlatList<ChatMessage>>(null);

    const scrollToEnd = () => {
      listRef.current?.scrollToEnd({animated: true});
    };

    useImperativeHandle(ref, () => ({scrollToEnd}), []);

    useEffect(() => {
      if (messages.length === 0) {
        return;
      }
      requestAnimationFrame(scrollToEnd);
    }, [messages]);

    return (
      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={item => item.id}
        style={styles.list}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        onContentSizeChange={scrollToEnd}
        renderItem={({item}) => (
          <View
            style={[
              styles.bubble,
              item.role === 'user' ? styles.userBubble : styles.companionBubble,
              item.role === 'system' && styles.systemBubble,
            ]}>
            <Text style={styles.text}>{item.text}</Text>
          </View>
        )}
      />
    );
  },
);

const styles = StyleSheet.create({
  list: {
    flex: 1,
  },
  content: {
    gap: 6,
    paddingVertical: 4,
    flexGrow: 1,
    justifyContent: 'flex-end',
  },
  bubble: {
    borderRadius: 14,
    paddingHorizontal: 12,
    paddingVertical: 8,
    maxWidth: '88%',
  },
  userBubble: {
    alignSelf: 'flex-end',
    backgroundColor: colors.userBubble,
  },
  companionBubble: {
    alignSelf: 'flex-start',
    backgroundColor: colors.companionBubble,
  },
  systemBubble: {
    alignSelf: 'center',
    backgroundColor: colors.systemBubble,
  },
  text: {
    color: colors.textPrimary,
    fontSize: 14,
    lineHeight: 20,
  },
});
