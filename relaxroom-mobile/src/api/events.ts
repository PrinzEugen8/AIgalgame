import {apiRequest, getSession} from './client';
import type {BackendEventResponse, LazyReply} from '../types/dialogue';

export async function postUserMessage(
  text: string,
  replyId = '',
  keyReply = false,
): Promise<BackendEventResponse> {
  const session = getSession();
  const eventType = keyReply ? 'option_selected' : 'user_message';
  const payload = keyReply
    ? {reply_text: text, ...(replyId ? {reply_id: replyId} : {})}
    : {text, ...(replyId ? {reply_id: replyId} : {})};

  return apiRequest<BackendEventResponse>('/api/events', {
    method: 'POST',
    body: JSON.stringify({
      event_type: eventType,
      event_id: `rn_${Date.now()}`,
      user_id: session.userId,
      character_id: session.characterId,
      session_id: session.sessionId,
      payload,
      client_context: {
        source: 'RelaxRoomMobile',
        local_time: new Date().toISOString(),
      },
    }),
  });
}

export async function postQuickReply(reply: LazyReply, keyReply: boolean) {
  return postUserMessage(reply.text, reply.reply_id ?? '', keyReply);
}

export async function checkHealth(): Promise<{ok?: boolean}> {
  return apiRequest('/api/health');
}
