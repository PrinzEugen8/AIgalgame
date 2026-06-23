import {apiRequest, getSession} from './client';
import type {
  HomeBootstrap,
  IdleActionResponse,
  MomentsResponse,
  RealtimeCallConfig,
} from '../types/dialogue';

function sessionQuery(extra?: Record<string, string | number | boolean | undefined>) {
  const session = getSession();
  return {
    user_id: session.userId,
    character_id: session.characterId,
    session_id: session.sessionId,
    ...extra,
  };
}

export async function fetchHomeBootstrap(): Promise<HomeBootstrap> {
  return apiRequest('/api/relaxroom/home', undefined, sessionQuery());
}

export async function fetchMoments(): Promise<MomentsResponse> {
  return apiRequest('/api/relaxroom/moments', undefined, sessionQuery());
}

export async function fetchRealtimeCallConfig(): Promise<RealtimeCallConfig> {
  return apiRequest('/api/realtime/call/config', undefined, sessionQuery());
}

export async function postMomentLike(momentId: string): Promise<unknown> {
  return apiRequest(
    `/api/moments/${encodeURIComponent(momentId)}/like`,
    {method: 'POST', body: '{}'},
    sessionQuery(),
  );
}

export async function postMomentComment(
  momentId: string,
  content: string,
): Promise<unknown> {
  return apiRequest(
    `/api/moments/${encodeURIComponent(momentId)}/comments`,
    {
      method: 'POST',
      body: JSON.stringify({content}),
    },
    sessionQuery(),
  );
}

export async function postIdleAction(idleSeconds = 0): Promise<IdleActionResponse> {
  const session = getSession();
  return apiRequest('/api/relaxroom/idle', {
    method: 'POST',
    body: JSON.stringify({
      user_id: session.userId,
      character_id: session.characterId,
      session_id: session.sessionId,
      idle_seconds: idleSeconds,
      client_context: {
        source: 'RelaxRoomMobile',
        local_time: new Date().toISOString(),
      },
    }),
  });
}

export function relationshipDay(startDate?: string): number {
  if (!startDate) {
    return 1;
  }
  const start = new Date(startDate);
  const now = new Date();
  const diff = now.getTime() - start.getTime();
  return Math.max(1, Math.floor(diff / (24 * 60 * 60 * 1000)) + 1);
}

export function formatRelativeTime(iso?: string): string {
  if (!iso) {
    return '';
  }
  const created = new Date(iso);
  const diffMs = Date.now() - created.getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) {
    return '刚刚';
  }
  if (minutes < 60) {
    return `${minutes} 分钟前`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时前`;
  }
  const days = Math.floor(hours / 24);
  if (days === 1) {
    return '昨天';
  }
  return `${days} 天前`;
}
