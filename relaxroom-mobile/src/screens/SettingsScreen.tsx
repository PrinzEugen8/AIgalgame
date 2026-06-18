import React, {useCallback, useState} from 'react';
import {Pressable, StyleSheet, Text, TextInput, View} from 'react-native';
import {checkHealth} from '../api/events';
import {ApiError, getSession} from '../api/client';
import {configureUnity} from '../bridge/unityBridge';
import {saveBackendUrl} from '../storage/sessionStorage';
import {colors} from '../theme/colors';
import {ArrowLeftIcon} from '../components/icons';
import {useSafeAreaInsets} from 'react-native-safe-area-context';

type Props = {
  onClose: () => void;
};

function formatHealthError(error: unknown, url: string) {
  if (error instanceof ApiError) {
    if (error.status === 0) {
      return error.message;
    }

    const body = error.message ? `：${error.message.slice(0, 180)}` : '';
    return `HTTP ${error.status}${body}`;
  }

  return error instanceof Error ? error.message : `无法连接 ${url}`;
}

export function SettingsScreen({onClose}: Props) {
  const insets = useSafeAreaInsets();
  const session = getSession();
  const [backendBaseUrl, setBackendBaseUrl] = useState(session.backendBaseUrl);
  const [status, setStatus] = useState('');

  const saveCurrentBackendUrl = useCallback(async () => {
    const trimmed = backendBaseUrl.trim();
    if (!trimmed) {
      setStatus('后端地址不能为空');
      return undefined;
    }

    const next = await saveBackendUrl(trimmed);
    setBackendBaseUrl(next.backendBaseUrl);
    configureUnity(next);
    return next;
  }, [backendBaseUrl]);

  const handleSave = useCallback(async () => {
    try {
      const next = await saveCurrentBackendUrl();
      if (next) {
        setStatus('已保存并同步到 Unity');
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : '保存失败');
    }
  }, [saveCurrentBackendUrl]);

  const handleHealthCheck = useCallback(async () => {
    let url = backendBaseUrl.trim();
    try {
      const next = await saveCurrentBackendUrl();
      if (!next) {
        return;
      }
      url = next.backendBaseUrl;

      await checkHealth();
      setStatus('后端连接正常');
    } catch (error) {
      setStatus(formatHealthError(error, url));
    }
  }, [backendBaseUrl, saveCurrentBackendUrl]);

  return (
    <View style={[styles.container, {paddingTop: insets.top + 12}]}>
      <View style={styles.header}>
        <Pressable onPress={onClose} style={styles.backButton}>
          <ArrowLeftIcon />
          <Text style={styles.back}>返回</Text>
        </Pressable>
        <Text style={styles.title}>设置</Text>
        <View style={styles.headerSpacer} />
      </View>

      <Text style={styles.label}>后端地址</Text>
      <TextInput
        style={styles.input}
        value={backendBaseUrl}
        onChangeText={setBackendBaseUrl}
        autoCapitalize="none"
        placeholder="http://10.0.2.2:8899"
        placeholderTextColor={colors.textPlaceholder}
      />

      <View style={styles.row}>
        <Pressable
          style={styles.button}
          onPress={() => {
            void handleSave();
          }}>
          <Text style={styles.buttonText}>保存</Text>
        </Pressable>

        <Pressable
          style={styles.button}
          onPress={() => {
            void handleHealthCheck();
          }}>
          <Text style={styles.buttonText}>健康检查</Text>
        </Pressable>
      </View>

      {status ? <Text style={styles.status}>{status}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.momentsBg,
    paddingHorizontal: 16,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 24,
  },
  backButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    minWidth: 72,
  },
  back: {
    color: colors.accentSoft,
    fontSize: 15,
  },
  title: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: '700',
  },
  headerSpacer: {
    width: 72,
  },
  label: {
    color: colors.textSecondary,
    marginBottom: 8,
  },
  input: {
    minHeight: 48,
    borderRadius: 12,
    paddingHorizontal: 14,
    color: colors.textPrimary,
    backgroundColor: colors.inputBg,
    borderWidth: 1,
    borderColor: colors.panelBorder,
  },
  row: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 20,
  },
  button: {
    flex: 1,
    backgroundColor: colors.accent,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 48,
  },
  buttonText: {
    color: colors.textPrimary,
    fontWeight: '700',
  },
  status: {
    marginTop: 16,
    color: colors.textSecondary,
  },
});
