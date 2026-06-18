import React, {useMemo, useState} from 'react';
import {
  Image,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type ImageSourcePropType,
} from 'react-native';
import {formatRelativeTime, postMomentComment, postMomentLike} from '../api/relaxroom';
import {getSession, resolveMediaUrl} from '../api/client';
import {colors} from '../theme/colors';
import {ImageLightbox} from './ImageLightbox';
import {PlayIcon} from './icons';
import type {MomentPost} from '../types/dialogue';
import {resolveLocalAsset} from '../utils/media';

type Props = {
  post: MomentPost;
  commentDraft: string;
  onCommentDraftChange: (value: string) => void;
  onRefresh: () => void;
};

export function MomentCard({
  post,
  commentDraft,
  onCommentDraftChange,
  onRefresh,
}: Props) {
  const session = getSession();
  const [previewSource, setPreviewSource] = useState<ImageSourcePropType | null>(
    null,
  );

  const avatar =
    resolveLocalAsset(post.avatar_url) ??
    (resolveMediaUrl(session.backendBaseUrl, post.avatar_url)
      ? {uri: resolveMediaUrl(session.backendBaseUrl, post.avatar_url)}
      : undefined);

  const images = useMemo(
    () =>
      (post.images ?? [])
        .map(imageUrl => {
          return (
            resolveLocalAsset(imageUrl) ??
            (resolveMediaUrl(session.backendBaseUrl, imageUrl)
              ? {uri: resolveMediaUrl(session.backendBaseUrl, imageUrl)}
              : undefined)
          );
        })
        .filter((source): source is ImageSourcePropType => source != null),
    [post.images, session.backendBaseUrl],
  );

  const videoThumb =
    resolveLocalAsset(post.video_thumbnail) ??
    (resolveMediaUrl(session.backendBaseUrl, post.video_thumbnail)
      ? {uri: resolveMediaUrl(session.backendBaseUrl, post.video_thumbnail)}
      : undefined);

  const isVideo = post.type === 'video' || Boolean(post.video_thumbnail);

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        {avatar ? <Image source={avatar} style={styles.avatar} /> : null}
        <View style={styles.headerText}>
          <Text style={styles.username}>{post.username}</Text>
          <Text style={styles.time}>
            {formatRelativeTime(post.created_at) || post.time}
          </Text>
        </View>
      </View>

      {post.text ? <Text style={styles.postText}>{post.text}</Text> : null}

      {images.length > 0 ? (
        <View style={styles.imageRow}>
          {images.map((source, index) => (
            <Pressable
              key={`${post.id}_img_${index}`}
              onPress={() => setPreviewSource(source)}>
              <Image source={source} style={styles.postImage} />
            </Pressable>
          ))}
        </View>
      ) : null}

      {isVideo && videoThumb ? (
        <Pressable
          style={styles.videoCard}
          onPress={() => setPreviewSource(videoThumb)}>
          <Image source={videoThumb} style={styles.videoThumb} />
          <View style={styles.videoOverlay} pointerEvents="none">
            <PlayIcon />
          </View>
          {post.video_duration ? (
            <Text style={styles.videoDuration}>{post.video_duration}</Text>
          ) : null}
        </Pressable>
      ) : null}

      {post.video_title ? (
        <Text style={styles.videoTitle}>{post.video_title}</Text>
      ) : null}

      <Text style={styles.likes}>
        {(post.likes ?? []).length > 0
          ? `♥ ${post.likes?.join('、')} 点赞了`
          : '暂无点赞'}
      </Text>

      {(post.comments ?? []).map(comment => (
        <Text key={`${comment.user}-${comment.text}`} style={styles.comment}>
          <Text style={styles.commentUser}>{comment.user}: </Text>
          {comment.text}
        </Text>
      ))}

      <View style={styles.actions}>
        <Pressable
          style={styles.actionButton}
          onPress={async () => {
            await postMomentLike(post.id);
            onRefresh();
          }}>
          <Text style={styles.actionText}>点赞</Text>
        </Pressable>
      </View>

      <View style={styles.commentRow}>
        <TextInput
          style={styles.commentInput}
          placeholder="写评论..."
          placeholderTextColor={colors.textPlaceholder}
          value={commentDraft}
          onChangeText={onCommentDraftChange}
        />
        <Pressable
          style={styles.actionButton}
          onPress={async () => {
            const content = commentDraft.trim();
            if (!content) {
              return;
            }
            await postMomentComment(post.id, content);
            onCommentDraftChange('');
            onRefresh();
          }}>
          <Text style={styles.actionText}>发送</Text>
        </Pressable>
      </View>

      <ImageLightbox
        visible={previewSource != null}
        source={previewSource ?? undefined}
        onClose={() => setPreviewSource(null)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.glassCard,
    borderRadius: 16,
    padding: 14,
    gap: 8,
    borderWidth: 1,
    borderColor: colors.glassCardBorder,
  },
  cardHeader: {
    flexDirection: 'row',
    gap: 10,
    alignItems: 'center',
  },
  headerText: {
    flex: 1,
  },
  avatar: {
    width: 42,
    height: 42,
    borderRadius: 8,
  },
  username: {
    color: colors.textPrimary,
    fontWeight: '700',
  },
  time: {
    color: colors.textMuted,
    fontSize: 12,
  },
  postText: {
    color: colors.textPrimary,
    lineHeight: 20,
  },
  imageRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  postImage: {
    width: 96,
    height: 96,
    borderRadius: 8,
  },
  videoCard: {
    width: '100%',
    height: 160,
    borderRadius: 10,
    overflow: 'hidden',
    backgroundColor: colors.inputBg,
  },
  videoThumb: {
    width: '100%',
    height: '100%',
  },
  videoOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.overlay,
  },
  videoDuration: {
    position: 'absolute',
    right: 8,
    bottom: 8,
    color: colors.textPrimary,
    fontSize: 12,
    backgroundColor: 'rgba(0,0,0,0.55)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  videoTitle: {
    color: colors.textSecondary,
    fontSize: 12,
  },
  likes: {
    color: colors.likeAccent,
    fontSize: 12,
  },
  comment: {
    color: 'rgba(255,255,255,0.82)',
    fontSize: 13,
  },
  commentUser: {
    color: colors.likeAccent,
  },
  actions: {
    flexDirection: 'row',
    gap: 8,
  },
  actionButton: {
    backgroundColor: colors.iconButtonBg,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  actionText: {
    color: colors.textPrimary,
  },
  commentRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'center',
  },
  commentInput: {
    flex: 1,
    minHeight: 40,
    borderRadius: 10,
    paddingHorizontal: 12,
    color: colors.textPrimary,
    backgroundColor: colors.inputBg,
    borderWidth: 1,
    borderColor: colors.panelBorder,
  },
});
