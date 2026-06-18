import AsyncStorage from '@react-native-async-storage/async-storage';
import {DEFAULT_SESSION, setSession} from '../api/client';
import type {SessionConfig} from '../types/dialogue';

const BACKEND_URL_KEY = 'relaxroom.backendBaseUrl';

export async function loadSession(): Promise<void> {
  try {
    const backendBaseUrl = await AsyncStorage.getItem(BACKEND_URL_KEY);
    if (backendBaseUrl?.trim()) {
      setSession({backendBaseUrl: backendBaseUrl.trim()});
    }
  } catch {
    setSession(DEFAULT_SESSION);
  }
}

export async function saveBackendUrl(backendBaseUrl: string): Promise<SessionConfig> {
  const trimmed = backendBaseUrl.trim();
  await AsyncStorage.setItem(BACKEND_URL_KEY, trimmed);
  return setSession({backendBaseUrl: trimmed});
}
