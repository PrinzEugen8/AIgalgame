import React, {useCallback, useEffect, useRef, useState} from 'react';
import {ActivityIndicator, Modal, Pressable, StyleSheet, Text, View} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import UnityView from '@azesmway/react-native-unity';
import {HomeTopHud} from '../components/HomeTopHud';
import {InteractionPanel} from '../components/InteractionPanel';
import {MomentsScreen} from './MomentsScreen';
import {SettingsScreen} from './SettingsScreen';
import {getSession} from '../api/client';
import {postUserMessage} from '../api/events';
import {
  fetchHomeBootstrap,
  fetchMoments,
  postIdleAction,
} from '../api/relaxroom';
import {
  beginReply,
  bindUnityRef,
  configureUnity,
  endReply,
  handleUnityMessage,
  playIdleAction,
  playLinesSequentially,
  playPhoneNotification,
  subscribeUnityEvents,
} from '../bridge/unityBridge';
import {loadSession} from '../storage/sessionStorage';
import {useInteractionPanelHeight} from '../theme/layout';
import {colors} from '../theme/colors';
import {getStartupSummaryText, logStartupStage} from '../bridge/startupDiagnostics';
import type {
  ChatMessage,
  HomeBootstrap,
  LazyReply,
  MomentsResponse,
} from '../types/dialogue';

const IDLE_AFTER_MS = 55_000;
const IDLE_RETRY_MS = 25_000;
const UNITY_LOAD_TIMEOUT_MS = 90_000;

export function RelaxRoomScreen() {
  const unityRef = useRef<UnityView>(null);
  const lastInteractionRef = useRef(Date.now());
  const idleInFlightRef = useRef(false);
  const unityReadyRef = useRef(false);
  const unityConfiguredRef = useRef(false);
  const {maxHeight, bottomInset} = useInteractionPanelHeight();

  const [unityReady, setUnityReady] = useState(false);
  const [, setEnvironmentReady] = useState(false);
  const [home, setHome] = useState<HomeBootstrap>();
  const [moments, setMoments] = useState<MomentsResponse>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [statusText, setStatusText] = useState('');
  const [sending, setSending] = useState(false);
  const [draft, setDraft] = useState('');
  const [normalReplies, setNormalReplies] = useState<LazyReply[]>([]);
  const [keyReplies, setKeyReplies] = useState<LazyReply[]>([]);
  const [showMoments, setShowMoments] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [sessionReady, setSessionReady] = useState(false);
  const [momentsLoading, setMomentsLoading] = useState(false);
  const [momentsError, setMomentsError] = useState('');
  const [loadTimedOut, setLoadTimedOut] = useState(false);
  const [devStartupSummary, setDevStartupSummary] = useState<string | null>(null);

  const pushMessage = useCallback((role: ChatMessage['role'], text: string) => {
    setMessages(current => [
      ...current,
      {id: `${Date.now()}_${current.length}`, role, text, createdAt: Date.now()},
    ]);
  }, []);

  const refreshHome = useCallback(async () => {
    try {
      const data = await fetchHomeBootstrap();
      setHome(data);
      setNormalReplies(data.quick_replies ?? []);
    } catch (error) {
      setStatusText(
        error instanceof Error ? error.message : '首页数据加载失败',
      );
    }
  }, []);

  const refreshMoments = useCallback(async () => {
    setMomentsLoading(true);
    setMomentsError('');
    try {
      const data = await fetchMoments();
      setMoments(data);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : '朋友圈加载失败';
      setMomentsError(message);
      setStatusText(message);
    } finally {
      setMomentsLoading(false);
    }
  }, []);

  const markInteraction = useCallback(() => {
    lastInteractionRef.current = Date.now();
  }, []);

  const sendMessage = useCallback(
    async (text: string, replyId = '', keyReply = false) => {
      if (!text.trim() || sending) {
        return;
      }

      markInteraction();
      setSending(true);
      setStatusText('发送中...');
      setNormalReplies([]);
      setKeyReplies([]);
      setDraft('');
      pushMessage('user', text);

      try {
        beginReply();
        const response = await postUserMessage(text, replyId, keyReply);
        if (response.event_type === 'error') {
          endReply();
          pushMessage('system', response.payload?.message ?? '后端返回错误');
          setStatusText('后端返回错误');
          return;
        }

        const lines = response.payload?.lines ?? [];
        if (lines.length === 0) {
          endReply();
          setStatusText('她暂时没有回复');
          return;
        }

        for (const line of lines) {
          if (line.text) {
            pushMessage('companion', line.text);
          }
        }

        await playLinesSequentially(lines);
        setNormalReplies(response.payload?.normal_replies ?? []);
        setKeyReplies(response.payload?.key_replies ?? []);
        setStatusText('');
      } catch (error) {
        endReply();
        pushMessage(
          'system',
          error instanceof Error ? error.message : '发送失败',
        );
        setStatusText('发送失败');
      } finally {
        setSending(false);
      }
    },
    [markInteraction, pushMessage, sending],
  );

  useEffect(() => {
    logStartupStage('unity_view_mount');
  }, []);

  useEffect(() => {
    if (unityReady) {
      return;
    }

    const timer = setTimeout(() => {
      setLoadTimedOut(true);
      logStartupStage('unity_load_timeout');
    }, UNITY_LOAD_TIMEOUT_MS);

    return () => clearTimeout(timer);
  }, [unityReady]);

  useEffect(() => {
    let cancelled = false;
    void loadSession().then(() => {
      if (!cancelled) {
        logStartupStage('session_ready');
        setSessionReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!sessionReady) {
      return;
    }
    bindUnityRef(unityRef);
    refreshHome();
    refreshMoments();

    const unsubscribe = subscribeUnityEvents(event => {
      if (event.evt === 'room_ready' || event.evt === 'ready') {
        logStartupStage('unity_ready', event.state);
        setEnvironmentReady(true);

        if (!unityReadyRef.current) {
          unityReadyRef.current = true;
          setUnityReady(true);
          setLoadTimedOut(false);
          setStatusText('');
        }

        if (!unityConfiguredRef.current) {
          unityConfiguredRef.current = true;
          configureUnity(getSession());
        }
        return;
      }

      if (event.evt === 'stable_frame') {
        logStartupStage('stable_frame', event.state);
        return;
      }

      if (event.evt === 'environment_ready') {
        setEnvironmentReady(true);
        setStatusText('');
        return;
      }

      if (event.evt === 'touch.started') {
        markInteraction();
        return;
      }

      if (event.evt === 'touch.finished') {
        if (event.text) {
          pushMessage('companion', event.text);
        }
        return;
      }

      if (event.evt === 'error') {
        setStatusText(event.text ?? 'Unity bridge error');
      }
    });

    return unsubscribe;
  }, [markInteraction, pushMessage, refreshHome, refreshMoments, sessionReady]);

  useEffect(() => {
    const timer = setInterval(async () => {
      if (!unityReady || sending || idleInFlightRef.current || showMoments || showSettings) {
        return;
      }

      const idleMs = Date.now() - lastInteractionRef.current;
      if (idleMs < IDLE_AFTER_MS) {
        return;
      }

      idleInFlightRef.current = true;
      try {
        const response = await postIdleAction(Math.floor(idleMs / 1000));
        const motion = response.payload?.motion;
        if (motion) {
          playIdleAction(
            motion,
            response.payload?.action_duration_seconds ?? undefined,
          );
        }

        for (const line of response.payload?.lines ?? []) {
          if (line.text) {
            pushMessage('companion', line.text);
          }
        }

        markInteraction();
      } catch {
        lastInteractionRef.current = Date.now() - IDLE_AFTER_MS + IDLE_RETRY_MS;
      } finally {
        idleInFlightRef.current = false;
      }
    }, 5000);

    return () => clearInterval(timer);
  }, [
    markInteraction,
    pushMessage,
    sending,
    showMoments,
    showSettings,
    unityReady,
  ]);

  useEffect(() => {
    if (showMoments || showSettings) {
      unityRef.current?.pauseUnity?.(true);
      return;
    }

    if (unityReady) {
      unityRef.current?.resumeUnity?.();
    }
  }, [showMoments, showSettings, unityReady]);

  return (
    <View style={styles.root}>
      <UnityView
        ref={unityRef}
        style={[StyleSheet.absoluteFillObject, {opacity: unityReady ? 1 : 0}]}
        androidKeepPlayerMounted={true}
        fullScreen={true}
        onUnityMessage={event => handleUnityMessage(event.nativeEvent.message)}
      />

      <View style={styles.overlay} pointerEvents="box-none">
        <SafeAreaView
          edges={['top']}
          style={styles.topHudLayer}
          pointerEvents="box-none">
          <HomeTopHud
            home={home}
            onOpenMoments={() => {
              markInteraction();
              setShowMoments(true);
              void refreshMoments();
            }}
            onOpenSettings={() => {
              markInteraction();
              setShowSettings(true);
            }}
          />
        </SafeAreaView>

        <InteractionPanel
          maxHeight={maxHeight}
          bottomInset={bottomInset}
          messages={messages}
          statusText={statusText}
          sending={sending}
          normalReplies={normalReplies}
          keyReplies={keyReplies}
          draft={draft}
          onDraftChange={setDraft}
          onSend={text => {
            void sendMessage(text);
          }}
          onQuickReplySelect={text => {
            markInteraction();
            setDraft(text);
          }}
          onPhone={() => {
            markInteraction();
            playPhoneNotification();
          }}
        />
      </View>

      <Modal visible={showMoments} animationType="slide">
        <MomentsScreen
          data={moments}
          home={home}
          loading={momentsLoading}
          errorText={momentsError}
          onRefresh={() => {
            void refreshMoments();
          }}
          onClose={() => {
            markInteraction();
            setShowMoments(false);
          }}
        />
      </Modal>

      <Modal visible={showSettings} animationType="slide">
        <SettingsScreen
          onClose={() => {
            markInteraction();
            setShowSettings(false);
            void refreshHome();
          }}
        />
      </Modal>

      {!unityReady ? (
        <Pressable
          style={styles.loadingOverlay}
          pointerEvents="box-none"
          onLongPress={() => {
            if (__DEV__) {
              setDevStartupSummary(getStartupSummaryText());
            }
          }}>
          <ActivityIndicator color={colors.textPrimary} size="large" />
          <Text style={styles.loadingTitle}>正在唤醒房间</Text>
          <Text style={styles.loadingText}>
            {loadTimedOut
              ? 'Unity 房间加载失败，请重启 App'
              : '首次加载可能需要 30 到 60 秒'}
          </Text>
          {__DEV__ && devStartupSummary ? (
            <Text style={styles.devSummaryText}>{devStartupSummary}</Text>
          ) : null}
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background,
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 10,
    elevation: 10,
  },
  topHudLayer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 20,
    elevation: 20,
  },
  loadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 100,
    elevation: 100,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
    paddingHorizontal: 24,
  },
  loadingTitle: {
    marginTop: 14,
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: '700',
  },
  loadingText: {
    marginTop: 6,
    color: colors.textSecondary,
    fontSize: 12,
  },
  devSummaryText: {
    marginTop: 16,
    color: colors.textSecondary,
    fontSize: 10,
    fontFamily: 'monospace',
    alignSelf: 'stretch',
  },
});
