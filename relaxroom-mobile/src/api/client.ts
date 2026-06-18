import {Platform} from 'react-native';
import type {SessionConfig} from '../types/dialogue';

export const DEFAULT_SESSION: SessionConfig = {
  backendBaseUrl:
    Platform.OS === 'android'
      ? 'http://10.0.2.2:8899'
      : 'http://127.0.0.1:8899',
  userId: 'demo_user',
  characterId: 'atri',
  sessionId: 'relaxroom_rn',
  appearanceId: 'neko',
};

let activeSession: SessionConfig = {...DEFAULT_SESSION};

export function getSession(): SessionConfig {
  return activeSession;
}

export function setSession(patch: Partial<SessionConfig>): SessionConfig {
  activeSession = {...activeSession, ...patch};
  return activeSession;
}

export function buildQuery(
  params: Record<string, string | number | boolean | undefined>,
): string {
  return Object.entries(params)
    .filter(
      ([, value]) =>
        value !== undefined && value !== null && `${value}`.length > 0,
    )
    .map(
      ([key, value]) =>
        `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`,
    )
    .join('&');
}

function httpErrorMessage(status: number, body: string, path: string): string {
  const trimmed = body.trim();
  if (status === 500) {
    return '后端内部错误，请查看服务端日志';
  }
  if (status === 404) {
    return `接口不存在：${path}`;
  }
  if (status === 502 || status === 503 || status === 504) {
    return '后端服务不可用，请确认服务已启动';
  }
  if (trimmed.toLowerCase() === 'internal server error') {
    return '后端内部错误，请查看服务端日志';
  }
  return trimmed || `请求失败 (${status})`;
}

export function resolveMediaUrl(
  backendBaseUrl: string,
  url?: string,
): string | undefined {
  if (!url) {
    return undefined;
  }
  if (
    url.startsWith('http://') ||
    url.startsWith('https://') ||
    url.startsWith('file://') ||
    url.startsWith('content://')
  ) {
    return url;
  }
  if (url.startsWith('local://')) {
    return undefined;
  }
  return `${backendBaseUrl.replace(/\/$/, '')}${
    url.startsWith('/') ? url : `/${url}`
  }`;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function networkErrorMessage(url: string, error: unknown): string {
  const originalMessage =
    error instanceof Error ? error.message : String(error ?? '');
  const normalized = originalMessage.toLowerCase();
  const isHttps = url.toLowerCase().startsWith('https://');

  if (
    normalized.includes('network request failed') ||
    normalized.includes('failed to fetch')
  ) {
    if (isHttps) {
      return `无法连接 ${url}。可能是证书不受信任；Debug 包已允许系统/用户证书，Release 需要服务端使用有效证书。`;
    }

    return `无法连接 ${url}`;
  }

  if (
    normalized.includes('ssl') ||
    normalized.includes('certificate') ||
    normalized.includes('cert') ||
    normalized.includes('trust')
  ) {
    return `证书不受信任：${url}。Debug 包已放宽证书信任；Release 需要服务端使用有效证书。`;
  }

  return originalMessage ? `无法连接 ${url}：${originalMessage}` : `无法连接 ${url}`;
}

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  query?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const session = getSession();
  const queryString = query ? buildQuery(query) : '';
  const url = `${session.backendBaseUrl.replace(/\/$/, '')}${path}${
    queryString ? `?${queryString}` : ''
  }`;

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    });
  } catch (error) {
    throw new ApiError(networkErrorMessage(url, error), 0);
  }

  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(httpErrorMessage(response.status, text, path), response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
