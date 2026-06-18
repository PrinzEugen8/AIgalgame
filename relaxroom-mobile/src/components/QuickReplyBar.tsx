import React from 'react';
import {Pressable, ScrollView, StyleSheet, Text} from 'react-native';
import {colors} from '../theme/colors';
import type {LazyReply} from '../types/dialogue';

type Props = {
  normalReplies: LazyReply[];
  keyReplies: LazyReply[];
  disabled?: boolean;
  onSelect: (text: string) => void;
};

export function QuickReplyBar({
  normalReplies,
  keyReplies,
  disabled,
  onSelect,
}: Props) {
  const replies = [
    ...normalReplies,
    ...keyReplies.map(item => ({...item, type: 'key'})),
  ];
  if (replies.length === 0) {
    return null;
  }

  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.row}>
      {replies.map(reply => {
        const keyReply = reply.type === 'key';
        return (
          <Pressable
            key={`${reply.reply_id ?? reply.text}`}
            disabled={disabled}
            style={[styles.chip, keyReply && styles.keyChip]}
            onPress={() => onSelect(reply.text)}>
            <Text style={styles.chipText}>{reply.text}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  row: {
    gap: 8,
    paddingVertical: 6,
  },
  chip: {
    backgroundColor: colors.chipBg,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: colors.panelBorder,
  },
  keyChip: {
    backgroundColor: colors.keyChipBg,
  },
  chipText: {
    color: colors.textPrimary,
    fontSize: 13,
  },
});
