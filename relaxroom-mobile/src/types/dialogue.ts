export type DialogueControllerCommand = {
  state?: string;
  face?: string;
  animation?: string;
  focus?: string;
  mouth?: string;
  lipsync?: boolean;
  pause?: number;
  tags?: string[];
};

export type DialogueVisualCue = {
  text?: string;
  face?: string;
  expression?: string;
  focus?: string;
  weight?: number;
};

export type DialogueLine = {
  line_id?: string;
  text?: string;
  emotion?: string;
  pose?: string;
  expression?: string;
  motion?: string;
  tts_audio_url?: string;
  controller?: DialogueControllerCommand;
  visual_cues?: DialogueVisualCue[];
};

export type LazyReply = {
  reply_id?: string;
  text: string;
  type?: string;
};

export type EventPayload = {
  lines?: DialogueLine[];
  normal_replies?: LazyReply[];
  key_replies?: LazyReply[];
  message?: string;
};

export type BackendEventResponse = {
  event_type?: string;
  payload?: EventPayload;
};

export type HomeBootstrap = {
  relationship_start_date?: string;
  daily_tip?: string;
  quick_replies?: LazyReply[];
  companion_name?: string;
  user_name?: string;
  chat_state_label?: string;
  session_id?: string;
  server_time?: string;
  unread_moment_interactions?: number;
};

export type MomentComment = {
  user: string;
  text: string;
  created_at?: string;
};

export type MomentPost = {
  id: string;
  username: string;
  created_at?: string;
  time?: string;
  visibility_label?: string;
  type?: string;
  text?: string;
  avatar_url?: string;
  images?: string[];
  video_thumbnail?: string;
  video_title?: string;
  video_duration?: string;
  likes?: string[];
  comments?: MomentComment[];
};

export type MomentsResponse = {
  profile?: {
    name?: string;
    status_suffix?: string;
    update_note?: string;
    avatar_url?: string;
  };
  posts?: MomentPost[];
  session_id?: string;
  server_time?: string;
};

export type IdleActionResponse = {
  ok?: boolean;
  payload?: {
    motion?: string;
    lines?: DialogueLine[];
    cooldown_seconds?: number;
    action_duration_seconds?: number;
  };
};

export type UnityBridgeEvent = {
  evt: string;
  id?: string;
  hit_area?: string;
  line_id?: string;
  text?: string;
  state?: string;
  face?: string;
  motion?: string;
  lip_sync?: boolean;
  summary?: string;
  source?: string;
  stage?: string;
  elapsed_ms?: number;
  detail?: string;
  stages?: Array<{stage: string; elapsed_ms: number}>;
};

export type SessionConfig = {
  backendBaseUrl: string;
  userId: string;
  characterId: string;
  sessionId: string;
  appearanceId: string;
};

export type RealtimeCallConfig = {
  ok?: boolean;
  configured?: boolean;
  provider_id?: string;
  provider_source?: string;
  model?: string;
  voice?: string;
  reason?: string;
  session?: Record<string, unknown>;
  transport?: {
    preferred?: string;
    websocket_endpoint?: string;
    audio_event?: string;
    image_event?: string;
  };
  audio?: {
    input_format?: string;
    input_sample_rate?: number;
    output_format?: string;
    output_sample_rate?: number;
  };
  vision?: {
    image_input?: boolean;
    active_frame_interval_ms?: number;
    idle_frame_interval_ms?: number;
    max_long_edge?: number;
    jpeg_quality?: number;
  };
};

export type ChatMessage = {
  id: string;
  role: 'user' | 'companion' | 'system';
  text: string;
  createdAt: number;
};
