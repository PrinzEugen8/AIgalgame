import React, {useCallback, useEffect, useRef, useState} from 'react';
import {
  ActivityIndicator,
  Modal,
  PermissionsAndroid,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import UnityView from '@azesmway/react-native-unity';
import {HomeTopHud} from '../components/HomeTopHud';
import {InteractionPanel} from '../components/InteractionPanel';
import {ChevronLeftIcon, ChevronRightIcon} from '../components/icons';
import {MomentsScreen} from './MomentsScreen';
import {SettingsScreen} from './SettingsScreen';
import {
  VideoCallScreen,
  type VideoCallPrimaryPane,
} from './VideoCallScreen';
import type {PhoneCameraFacing} from '../components/PhoneCameraPreview';
import {getSession} from '../api/client';
import {postSleepingUserMessage, postUserMessage} from '../api/events';
import {
  fetchHomeBootstrap,
  fetchMoments,
  postIdleAction,
} from '../api/relaxroom';
import {
  beginReply,
  bindUnityRef,
  beginVideoCall,
  configureUnity,
  enterSleepMode,
  endReply,
  endVideoCall,
  exitSleepMode,
  handleUnityMessage,
  notifyWhileSleeping,
  playIdleAction,
  playLinesSequentially,
  playPhoneNotification,
  setVideoCallAiSpeaking,
  setVideoCallExpression,
  setVideoCallUserSpeaking,
  subscribeUnityEvents,
  switchCameraNext,
  switchCameraPrevious,
} from '../bridge/unityBridge';
import {
  RealtimeCallController,
  type RealtimeCallSnapshot,
} from '../realtime/realtimeCallClient';
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
const INITIAL_CALL_SNAPSHOT: RealtimeCallSnapshot = {
  phase: 'idle',
  statusText: '',
  elapsedSeconds: 0,
  muted: false,
  cameraEnabled: true,
  configured: false,
  model: 'qwen3.5-omni-flash-realtime-2026-03-15',
  voice: 'Momo',
  providerId: '',
};

async function requestVideoCallPermissions(): Promise<boolean> {
  if (Platform.OS !== 'android') {
    return true;
  }

  const result = await PermissionsAndroid.requestMultiple([
    PermissionsAndroid.PERMISSIONS.CAMERA,
    PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
  ]);

  return (
    result[PermissionsAndroid.PERMISSIONS.CAMERA] ===
      PermissionsAndroid.RESULTS.GRANTED &&
    result[PermissionsAndroid.PERMISSIONS.RECORD_AUDIO] ===
      PermissionsAndroid.RESULTS.GRANTED
  );
}

export function RelaxRoomScreen() {
  const unityRef = useRef<UnityView>(null);
  const callControllerRef = useRef<RealtimeCallController | null>(null);
  const lastCallImageSentAtRef = useRef(0);
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
  const [isSleeping, setIsSleeping] = useState(false);
  const [sessionReady, setSessionReady] = useState(false);
  const [momentsLoading, setMomentsLoading] = useState(false);
  const [momentsError, setMomentsError] = useState('');
  const [loadTimedOut, setLoadTimedOut] = useState(false);
  const [devStartupSummary, setDevStartupSummary] = useState<string | null>(null);
  const [callSnapshot, setCallSnapshot] =
    useState<RealtimeCallSnapshot>(INITIAL_CALL_SNAPSHOT);
  const [callPrimaryPane, setCallPrimaryPane] =
    useState<VideoCallPrimaryPane>('remote');
  const [phoneCameraFacing, setPhoneCameraFacing] =
    useState<PhoneCameraFacing>('front');

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

  const resolveCallController = useCallback(() => {
    if (callControllerRef.current != null) {
      return callControllerRef.current;
    }

    callControllerRef.current = new RealtimeCallController({
      onSnapshot: setCallSnapshot,
      onUnityEvent: event => {
        switch (event) {
          case 'begin':
            beginVideoCall();
            setVideoCallExpression('shy', 0.55);
            break;
          case 'end':
            endVideoCall();
            break;
          case 'ai_speaking_start':
            setVideoCallAiSpeaking(true);
            break;
          case 'ai_speaking_stop':
            setVideoCallAiSpeaking(false);
            break;
          case 'user_speaking_start':
            setVideoCallUserSpeaking(true);
            break;
          case 'user_speaking_stop':
            setVideoCallUserSpeaking(false);
            break;
        }
      },
    });
    return callControllerRef.current;
  }, []);

  const handleVideoCallPress = useCallback(async () => {
    markInteraction();
    if (isSleeping) {
      notifyWhileSleeping();
      return;
    }

    const controller = resolveCallController();
    if (callSnapshot.phase === 'idle' || callSnapshot.phase === 'error') {
      const granted = await requestVideoCallPermissions();
      if (!granted) {
        setStatusText('Camera and microphone permission are required');
        return;
      }

      setCallPrimaryPane('remote');
      setPhoneCameraFacing('front');
      lastCallImageSentAtRef.current = 0;
      void controller.start();
      return;
    }
    controller.end();
  }, [callSnapshot.phase, isSleeping, markInteraction, resolveCallController]);

  const setSleepModeForTest = useCallback(
    (sleeping: boolean) => {
      markInteraction();
      setShowSettings(false);
      setIsSleeping(sleeping);
      setTimeout(() => {
        if (sleeping) {
          enterSleepMode();
          return;
        }

        exitSleepMode();
      }, 80);
    },
    [markInteraction],
  );

  const handlePreviousCamera = useCallback(() => {
    markInteraction();
    switchCameraPrevious();
  }, [markInteraction]);

  const handleNextCamera = useCallback(() => {
    markInteraction();
    switchCameraNext();
  }, [markInteraction]);

  useEffect(() => {
    return () => {
      callControllerRef.current?.dispose();
      callControllerRef.current = null;
    };
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
        if (isSleeping) {
          notifyWhileSleeping();
          const response = await postSleepingUserMessage(text);
          if (response.event_type === 'error') {
            pushMessage('system', response.payload?.message ?? 'Backend error');
            setStatusText('Backend error');
            return;
          }

          setStatusText('');
          return;
        }

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
        if (!isSleeping) {
          endReply();
        }
        pushMessage(
          'system',
          error instanceof Error ? error.message : '发送失败',
        );
        setStatusText('发送失败');
      } finally {
        setSending(false);
      }
    },
    [isSleeping, markInteraction, pushMessage, sending],
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

      if (event.evt === 'sleep.changed') {
        setIsSleeping(event.state === 'sleeping');
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
      if (
        !unityReady ||
        sending ||
        idleInFlightRef.current ||
        showMoments ||
        showSettings ||
        isSleeping ||
        callSnapshot.phase !== 'idle'
      ) {
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
    isSleeping,
    callSnapshot.phase,
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

  const callActive = callSnapshot.phase !== 'idle';
  const unityInCallPip = callActive && callPrimaryPane === 'local';
  const unityViewStyle = StyleSheet.flatten([
    styles.unityView,
    unityReady ? styles.unityVisible : styles.unityHidden,
    unityInCallPip ? styles.unityPip : null,
  ]);

  return (
    <View style={styles.root}>
      <View
        pointerEvents={unityInCallPip ? 'none' : 'auto'}
        style={unityViewStyle}>
        <UnityView
          ref={unityRef}
          style={styles.unityEmbedded}
          androidKeepPlayerMounted={true}
          fullScreen={!unityInCallPip}
          onUnityMessage={event => handleUnityMessage(event.nativeEvent.message)}
        />
      </View>

      <View style={styles.overlay} pointerEvents="box-none">
        {!callActive ? (
          <>
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

            <View style={styles.cameraControls} pointerEvents="box-none">
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Previous Unity camera"
                style={[styles.cameraButton, styles.cameraButtonLeft]}
                onPress={handlePreviousCamera}>
                <ChevronLeftIcon />
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Next Unity camera"
                style={[styles.cameraButton, styles.cameraButtonRight]}
                onPress={handleNextCamera}>
                <ChevronRightIcon />
              </Pressable>
            </View>

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
                if (isSleeping) {
                  notifyWhileSleeping();
                  return;
                }

                playPhoneNotification();
              }}
              videoCallActive={false}
              onVideoCall={() => {
                void handleVideoCallPress();
              }}
            />
          </>
        ) : (
          <VideoCallScreen
            snapshot={callSnapshot}
            companionName={home?.companion_name}
            primaryPane={callPrimaryPane}
            cameraFacing={phoneCameraFacing}
            onSwapPanes={() => {
              markInteraction();
              setCallPrimaryPane(current =>
                current === 'remote' ? 'local' : 'remote',
              );
            }}
            onToggleMute={() => {
              markInteraction();
              resolveCallController().toggleMute();
            }}
            onToggleCameraFacing={() => {
              markInteraction();
              setPhoneCameraFacing(current =>
                current === 'front' ? 'back' : 'front',
              );
            }}
            onEnd={() => {
              markInteraction();
              resolveCallController().end();
            }}
            onFrame={event => {
              const image = event.nativeEvent.image;
              if (!image) {
                return;
              }

              const now = Date.now();
              if (now - lastCallImageSentAtRef.current >= 1200) {
                lastCallImageSentAtRef.current = now;
                resolveCallController().sendImageJpegBase64(image);
              }
            }}
            onCameraError={event => {
              setStatusText(event.nativeEvent.message);
            }}
          />
        )}
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
          isSleeping={isSleeping}
          onEnterSleep={() => setSleepModeForTest(true)}
          onExitSleep={() => setSleepModeForTest(false)}
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
  unityView: {
    ...StyleSheet.absoluteFillObject,
  },
  unityEmbedded: {
    ...StyleSheet.absoluteFillObject,
  },
  unityVisible: {
    opacity: 1,
  },
  unityHidden: {
    opacity: 0,
  },
  unityPip: {
    top: 92,
    left: undefined,
    right: 14,
    bottom: undefined,
    width: 116,
    height: 164,
    borderRadius: 10,
    overflow: 'hidden',
    zIndex: 55,
    elevation: 55,
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
  cameraControls: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 18,
    elevation: 18,
  },
  cameraButton: {
    position: 'absolute',
    top: '42%',
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.panelBg,
    borderWidth: 1,
    borderColor: colors.panelBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cameraButtonLeft: {
    left: 12,
  },
  cameraButtonRight: {
    right: 12,
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
