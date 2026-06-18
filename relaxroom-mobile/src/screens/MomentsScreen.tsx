import React, {useMemo, useState} from 'react';
import {
  ActivityIndicator,
  Image,
  ImageBackground,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import {useSafeAreaInsets} from 'react-native-safe-area-context';
import {relationshipDay} from '../api/relaxroom';
import {getSession, resolveMediaUrl} from '../api/client';
import {MomentCard} from '../components/MomentCard';
import {ArrowLeftIcon, PlusIcon} from '../components/icons';
import {colors} from '../theme/colors';
import type {HomeBootstrap, MomentsResponse} from '../types/dialogue';
import {resolveLocalAsset} from '../utils/media';

type Props = {
  data?: MomentsResponse;
  home?: HomeBootstrap;
  loading?: boolean;
  errorText?: string;
  onRefresh: () => void;
  onClose: () => void;
};

export function MomentsScreen({
  data,
  home,
  loading = false,
  errorText = '',
  onRefresh,
  onClose,
}: Props) {
  const insets = useSafeAreaInsets();
  const [commentDraft, setCommentDraft] = useState<Record<string, string>>({});
  const session = getSession();
  const day = relationshipDay(home?.relationship_start_date);

  const posts = useMemo(() => data?.posts ?? [], [data?.posts]);

  const profileAvatar =
    resolveLocalAsset(data?.profile?.avatar_url) ??
    resolveLocalAsset('local://RelaxRoomUI/avatar_xiaobai') ??
    (resolveMediaUrl(session.backendBaseUrl, data?.profile?.avatar_url)
      ? {uri: resolveMediaUrl(session.backendBaseUrl, data?.profile?.avatar_url)}
      : undefined);

  return (
    <ImageBackground
      source={require('../assets/relaxroom/moment_rain_city.png')}
      style={styles.container}
      imageStyle={styles.backgroundImage}>
      <View style={[styles.overlay, {paddingTop: insets.top + 8}]}>
        <View style={styles.header}>
          <Pressable onPress={onClose} style={styles.headerAction}>
            <ArrowLeftIcon />
            <Text style={styles.back}>返回</Text>
          </Pressable>
          <Text style={styles.title}>朋友圈</Text>
          <Pressable onPress={onRefresh} style={styles.headerActionRight}>
            <PlusIcon />
          </Pressable>
        </View>

        <ScrollView style={styles.scroll} contentContainerStyle={styles.list}>
          <View style={styles.banner}>
            <View style={styles.bannerText}>
              <Text style={styles.bannerTitle}>
                {data?.profile?.name ?? home?.chat_state_label ?? '夜雨空间'}
              </Text>
              <Text style={styles.bannerSubtitle}>
                第 {day} 天 · {data?.profile?.status_suffix ?? '今天也是好好地过'}
              </Text>
              {data?.profile?.update_note ? (
                <Text style={styles.bannerNote}>{data.profile.update_note}</Text>
              ) : null}
            </View>
            {profileAvatar ? (
              <Image source={profileAvatar} style={styles.profileAvatar} />
            ) : null}
          </View>

          <View style={styles.divider} />

          {loading ? (
            <View style={styles.stateBox}>
              <ActivityIndicator color={colors.accentSoft} />
              <Text style={styles.stateText}>加载中…</Text>
            </View>
          ) : null}

          {!loading && errorText ? (
            <View style={styles.stateBox}>
              <Text style={styles.errorText}>{errorText}</Text>
              <Pressable style={styles.retryButton} onPress={onRefresh}>
                <Text style={styles.retryText}>重试</Text>
              </Pressable>
            </View>
          ) : null}

          {!loading && !errorText && posts.length === 0 ? (
            <View style={styles.stateBox}>
              <Text style={styles.stateText}>暂无动态</Text>
            </View>
          ) : null}

          {!loading && !errorText
            ? posts.map(post => (
                <MomentCard
                  key={post.id}
                  post={post}
                  commentDraft={commentDraft[post.id] ?? ''}
                  onCommentDraftChange={value =>
                    setCommentDraft(current => ({...current, [post.id]: value}))
                  }
                  onRefresh={onRefresh}
                />
              ))
            : null}

          {!loading && !errorText && posts.length > 0 ? (
            <Text style={styles.footer}>— 已经是最早的动态了 —</Text>
          ) : null}
        </ScrollView>
      </View>
    </ImageBackground>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.momentsBg,
  },
  backgroundImage: {
    opacity: 0.35,
  },
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(16, 19, 29, 0.82)',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    marginBottom: 8,
  },
  headerAction: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    minWidth: 72,
  },
  headerActionRight: {
    minWidth: 40,
    alignItems: 'flex-end',
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
  scroll: {
    flex: 1,
  },
  list: {
    gap: 14,
    paddingHorizontal: 16,
    paddingBottom: 32,
  },
  banner: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    paddingVertical: 12,
    gap: 12,
  },
  bannerText: {
    flex: 1,
    gap: 4,
  },
  bannerTitle: {
    color: colors.textPrimary,
    fontSize: 22,
    fontWeight: '700',
  },
  bannerSubtitle: {
    color: colors.textSecondary,
    fontSize: 13,
  },
  bannerNote: {
    color: colors.textMuted,
    fontSize: 12,
    marginTop: 2,
  },
  profileAvatar: {
    width: 64,
    height: 64,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: colors.panelBorder,
  },
  divider: {
    height: 1,
    backgroundColor: colors.panelBorder,
    marginBottom: 4,
  },
  stateBox: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    paddingVertical: 24,
  },
  stateText: {
    color: colors.textMuted,
    fontSize: 14,
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 20,
  },
  retryButton: {
    backgroundColor: colors.accent,
    borderRadius: 10,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  retryText: {
    color: colors.textPrimary,
    fontWeight: '700',
  },
  footer: {
    textAlign: 'center',
    color: colors.textMuted,
    fontSize: 12,
    marginTop: 8,
    marginBottom: 16,
  },
});
