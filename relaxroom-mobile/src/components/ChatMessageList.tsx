import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
} from 'react';
import {
  FlatList,
  GestureResponderEvent,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import {colors} from '../theme/colors';
import type {ChatMessage} from '../types/dialogue';

const TAP_MOVE_THRESHOLD = 8;

export type ChatMessageListHandle = {
  scrollToEnd: () => void;
};

type Props = {
  messages: ChatMessage[];
  onAdvanceReply?: () => void;
};

export const ChatMessageList = forwardRef<ChatMessageListHandle, Props>(
  function ChatMessageList({messages, onAdvanceReply}, ref) {
    const listRef = useRef<FlatList<ChatMessage>>(null);
    const touchStartRef = useRef<{pageX: number; pageY: number} | null>(null);
    const touchMovedRef = useRef(false);

    const scrollToEnd = useCallback((animated = true) => {
      listRef.current?.scrollToEnd({animated});
    }, []);

    const scrollToEndAfterLayout = useCallback(
      (animated = true) => {
        requestAnimationFrame(() => scrollToEnd(animated));
        setTimeout(() => scrollToEnd(animated), 80);
      },
      [scrollToEnd],
    );

    useImperativeHandle(
      ref,
      () => ({scrollToEnd: () => scrollToEndAfterLayout(true)}),
      [scrollToEndAfterLayout],
    );

    useEffect(() => {
      if (messages.length === 0) {
        return;
      }
      scrollToEndAfterLayout(true);
    }, [messages.length, scrollToEndAfterLayout]);

    const handleTouchStart = useCallback((event: GestureResponderEvent) => {
      const {pageX, pageY} = event.nativeEvent;
      touchStartRef.current = {pageX, pageY};
      touchMovedRef.current = false;
    }, []);

    const handleTouchMove = useCallback((event: GestureResponderEvent) => {
      const start = touchStartRef.current;
      if (!start) {
        return;
      }

      const {pageX, pageY} = event.nativeEvent;
      if (
        Math.abs(pageX - start.pageX) > TAP_MOVE_THRESHOLD ||
        Math.abs(pageY - start.pageY) > TAP_MOVE_THRESHOLD
      ) {
        touchMovedRef.current = true;
      }
    }, []);

    const handleTouchEnd = useCallback(() => {
      if (!touchMovedRef.current) {
        onAdvanceReply?.();
      }
      touchStartRef.current = null;
      touchMovedRef.current = false;
    }, [onAdvanceReply]);

    const handleScrollBeginDrag = useCallback(() => {
      touchMovedRef.current = true;
    }, []);

    return (
      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={item => item.id}
        style={styles.list}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        onContentSizeChange={() => scrollToEndAfterLayout(true)}
        onScrollBeginDrag={handleScrollBeginDrag}
        onTouchEnd={handleTouchEnd}
        onTouchMove={handleTouchMove}
        onTouchStart={handleTouchStart}
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
    paddingTop: 4,
    paddingBottom: 10,
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
