# RelaxRoomAI Backend Contract

This document describes the Unity-facing contract used by `RelaxRoomAI`.

Base URL defaults to `http://127.0.0.1:8899`. Unity sends `user_id`, `character_id`, and `session_id` as query params or JSON fields.

## GET `/api/relaxroom/home`

Purpose: bootstrap the home/chat UI.

Query:

```text
user_id=demo_user
character_id=atri
session_id=relaxroom_unity
```

Response:

```json
{
  "relationship_start_date": "2026-01-11",
  "daily_tip": "今日提示：今天也要好好吃饭哦～",
  "quick_replies": [],
  "companion_name": "亚托莉",
  "user_name": "你",
  "chat_state_label": "夜雨空间 · 和亚托莉聊天中",
  "session_id": "relaxroom_unity",
  "server_time": "2026-06-18T00:00:00+00:00",
  "unread_moment_interactions": 0
}
```

Rules:

- Backend owns `relationship_start_date`; Unity computes the displayed relationship day locally.
- Backend owns `daily_tip`.
- `quick_replies` is reserved for backend-authored bootstrap suggestions and defaults to `[]`.
- LLM-generated lazy replies should come from `/api/events` payload `normal_replies` / `key_replies`; Unity renders them as horizontal chips without a visible scrollbar.

## GET `/api/relaxroom/moments`

Purpose: load the Friend Circle UI.

Response:

```json
{
  "profile": {
    "name": "夜雨空间",
    "status_suffix": "今天也是好好地过",
    "update_note": "亚托莉更新了 3 条动态",
    "avatar_url": "local://RelaxRoomUI/avatar_xiaobai"
  },
  "posts": [
    {
      "id": "seed_moment_atri_walk",
      "username": "亚托莉",
      "created_at": "2026-06-18T00:00:00+00:00",
      "time": "",
      "visibility_label": "仅好友可见",
      "type": "image",
      "text": "朋友圈正文",
      "avatar_url": "local://RelaxRoomUI/avatar_xiaobai",
      "images": ["local://RelaxRoomUI/moment_curry_01"],
      "video_thumbnail": "",
      "video_title": "",
      "video_duration": "",
      "likes": ["你", "同桌同学"],
      "comments": [
        {"user": "同桌同学", "text": "评论内容", "created_at": "2026-06-18T00:00:00+00:00"}
      ]
    }
  ],
  "session_id": "relaxroom_unity",
  "server_time": "2026-06-18T00:00:00+00:00"
}
```

Rules:

- Backend sends ISO timestamps in `created_at`.
- Unity computes relative display text such as `刚刚`, `20 分钟前`, `1 小时前`, and `昨天`.
- `time` is legacy fallback only. Do not send pre-rendered relative time from backend.
- `local://RelaxRoomUI/...` is a temporary mock asset scheme for Unity `Resources/RelaxRoomUI`. Production media can use absolute HTTP URLs or relative backend URLs such as `/media/{asset_id}`.

## POST `/api/events`

Purpose: send chat text to the dialogue pipeline.

Unity sends local ASR results through this same endpoint after recognition.

Response payload can include lazy replies:

```json
{
  "event_type": "dialogue",
  "payload": {
    "lines": [{"line_id": "line_x", "text": "先吃饭吧。"}],
    "normal_replies": [{"reply_id": "reply_a", "text": "那我去食堂。", "type": "normal"}],
    "key_replies": [{"reply_id": "reply_b", "text": "我想和你一起去。", "type": "key"}]
  }
}
```

Unity posts a clicked normal lazy reply as `event_type=user_message` with payload `{ "text": "...", "reply_id": "..." }`. It posts a clicked key reply as `event_type=option_selected` with payload `{ "reply_text": "...", "reply_id": "..." }`.

## POST `/api/relaxroom/moments/test`

Purpose: create or replace a Friend Circle test post that Unity can immediately read from `GET /api/relaxroom/moments`.

Body:

```json
{
  "user_id": "demo_user",
  "character_id": "atri",
  "moment_id": "manual_test_001",
  "username": "亚托莉",
  "text": "测试一下朋友圈 UI。",
  "created_at": "2026-06-18T09:30:00+00:00",
  "visibility_label": "测试可见",
  "type": "image",
  "images": ["https://example.com/demo.png", "local://RelaxRoomUI/moment_room_lamp"],
  "likes": ["你", "同桌同学"],
  "comments": [
    {"user": "你", "text": "可以看到评论。", "created_at": "2026-06-18T09:31:00+00:00"}
  ]
}
```

Rules:

- `moment_id` is optional. Passing the same `moment_id` replaces the post and clears old likes/comments for repeat testing.
- At least one of `text`, `images`, or `video_thumbnail` is required.
- `likes` can be an array or comma/newline-separated string.
- `comments` can be an array of objects or comma/newline-separated text.

## Voice / ASR

Voice recognition is local to Unity:

- Android uses the bundled sherpa ONNX path.
- Windows standalone uses the local sherpa ONNX runtime when available.
- Backend receives only the final recognized text via `/api/events`.

## POST `/api/relaxroom/idle`

Purpose: request an idle motion/dialogue action when Unity detects inactivity.

Body fields include:

```json
{
  "user_id": "demo_user",
  "character_id": "atri",
  "session_id": "relaxroom_unity",
  "idle_seconds": 180,
  "local_time": "2026-06-18T20:00:00+08:00",
  "scene": "Scene_01",
  "allow_llm": true
}
```

## Other Existing Endpoints

- `GET /api/health`: backend health check.
- `POST /api/live2d/touch`: Live2D touch reaction.
- `POST /api/live2d/touch/refresh`: refresh touch pools.
- `GET /media/{asset_id}`: production media file fetch.
