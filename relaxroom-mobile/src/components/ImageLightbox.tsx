import React from 'react';
import {
  Image,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
  type ImageSourcePropType,
} from 'react-native';
import {useSafeAreaInsets} from 'react-native-safe-area-context';
import {colors} from '../theme/colors';

type Props = {
  visible: boolean;
  source?: ImageSourcePropType;
  onClose: () => void;
};

export function ImageLightbox({visible, source, onClose}: Props) {
  const insets = useSafeAreaInsets();

  return (
    <Modal
      visible={visible && source != null}
      transparent
      animationType="fade"
      onRequestClose={onClose}>
      <View style={styles.container}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        {source ? (
          <Image source={source} style={styles.image} resizeMode="contain" />
        ) : null}
        <Pressable
          style={[styles.closeButton, {top: insets.top + 12}]}
          onPress={onClose}>
          <Text style={styles.closeText}>关闭</Text>
        </Pressable>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.92)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
  },
  image: {
    width: '100%',
    height: '100%',
  },
  closeButton: {
    position: 'absolute',
    right: 16,
    backgroundColor: 'rgba(255,255,255,0.14)',
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: colors.panelBorder,
  },
  closeText: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: '600',
  },
});
