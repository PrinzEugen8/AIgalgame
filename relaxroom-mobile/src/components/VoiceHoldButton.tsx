import React, {useRef} from 'react';
import {PermissionsAndroid, Platform, Pressable, StyleSheet, Text} from 'react-native';
import {
  startLocalAsr,
  stopLocalAsr,
  waitForLocalAsrResult,
} from '../bridge/unityBridge';
import {colors} from '../theme/colors';

type Props = {
  disabled?: boolean;
  onSend: (text: string) => void;
  onError?: (message: string) => void;
};

const MIN_HOLD_MS = 350;

async function ensureMicPermission(): Promise<boolean> {
  if (Platform.OS !== 'android') {
    return true;
  }
  const granted = await PermissionsAndroid.request(
    PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
    {
      title: '麦克风权限',
      message: '语音消息需要访问麦克风',
      buttonPositive: '允许',
      buttonNegative: '拒绝',
    },
  );
  return granted === PermissionsAndroid.RESULTS.GRANTED;
}

export function VoiceHoldButton({disabled, onSend, onError}: Props) {
  const pressingRef = useRef(false);
  const pressStartedAtRef = useRef(0);
  const requestIdRef = useRef('');
  const [pressing, setPressing] = React.useState(false);
  const [recognizing, setRecognizing] = React.useState(false);

  const handlePressIn = async () => {
    if (disabled || recognizing || pressingRef.current) {
      return;
    }

    const allowed = await ensureMicPermission();
    if (!allowed) {
      onError?.('未获得麦克风权限');
      return;
    }

    pressingRef.current = true;
    pressStartedAtRef.current = Date.now();
    requestIdRef.current = startLocalAsr();
    setPressing(true);
  };

  const handlePressOut = async () => {
    if (!pressingRef.current) {
      setPressing(false);
      return;
    }

    const heldMs = Date.now() - pressStartedAtRef.current;
    const requestId = requestIdRef.current;
    pressingRef.current = false;
    setPressing(false);

    if (heldMs < MIN_HOLD_MS) {
      stopLocalAsr(requestId);
      onError?.('按住时间太短，请按住说话后再松开');
      return;
    }

    stopLocalAsr(requestId);
    setRecognizing(true);
    try {
      const result = await waitForLocalAsrResult(requestId);
      if (result.ok && result.text) {
        onSend(result.text);
        return;
      }
      onError?.(result.error ?? '未识别到语音内容');
    } finally {
      setRecognizing(false);
    }
  };

  const label = recognizing
    ? '正在识别...'
    : pressing
      ? '松开 发送'
      : '按住 说话';

  return (
    <Pressable
      style={[
        styles.button,
        pressing && styles.buttonActive,
        (disabled || recognizing) && styles.disabled,
      ]}
      disabled={disabled || recognizing}
      onPressIn={() => {
        void handlePressIn();
      }}
      onPressOut={() => {
        void handlePressOut();
      }}
      onTouchCancel={() => {
        void handlePressOut();
      }}>
      <Text style={styles.text}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    flex: 1,
    minHeight: 44,
    borderRadius: 22,
    backgroundColor: colors.inputBg,
    borderWidth: 1,
    borderColor: colors.panelBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonActive: {
    backgroundColor: colors.iconActive,
    borderColor: colors.sendButtonBorder,
  },
  disabled: {
    opacity: 0.5,
  },
  text: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: '600',
  },
});
