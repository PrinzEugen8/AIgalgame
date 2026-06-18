import React from 'react';
import {Image, Pressable, StyleSheet, Text, View} from 'react-native';
import {colors} from '../theme/colors';

type Props = {
  uri: string;
  onRemove: () => void;
};

export function ImagePreviewChip({uri, onRemove}: Props) {
  return (
    <View style={styles.container}>
      <Image source={{uri}} style={styles.image} />
      <Pressable style={styles.remove} onPress={onRemove}>
        <Text style={styles.removeText}>×</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignSelf: 'flex-start',
    marginBottom: 6,
  },
  image: {
    width: 72,
    height: 72,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.panelBorder,
  },
  remove: {
    position: 'absolute',
    top: -6,
    right: -6,
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: 'rgba(0,0,0,0.7)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  removeText: {
    color: colors.textPrimary,
    fontSize: 16,
    lineHeight: 18,
    fontWeight: '700',
  },
});
