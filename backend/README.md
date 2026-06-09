# Backend

## Run

```powershell
cd E:\AIgalgame\backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8899 --reload
```

Open `http://<PC-LAN-IP>:8899/admin` on the PC to configure real services and copy the phone connection address.

## Debugging

The backend writes console logs and a rotating file log at `backend/data/logs/backend.log` by default. `backend/data/` is ignored by git, so local logs, SQLite state, generated media, and secrets stay out of commits.

Useful environment variables:

```powershell
$env:AIGALGAME_LOG_LEVEL = "DEBUG"
$env:AIGALGAME_PORT = "8899"
.\run.ps1
```

For attachable breakpoints, enable `debugpy` before starting the service and attach your IDE to `127.0.0.1:5678`:

```powershell
$env:AIGALGAME_DEBUGPY = "1"
$env:AIGALGAME_DEBUGPY_WAIT = "1"
.\run.ps1
```

The admin page also includes debug actions wired to `/api/debug/run-daily-cycle`, `/api/debug/generate-proactive`, and `/api/debug/advance-time`.

## Provider Notes

All secrets are filled in on `/admin` and saved to `backend/data/secrets.local.json`, which is ignored by git.

Provider tests intentionally fail if a real key, model, or provider capability is missing. The demo does not fabricate news, image, or TTS success.

### Example Provider Metadata

LLM,火山 Ark:

```json
{
  "kind": "llm",
  "provider": "volc_ark",
  "base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "model": "your-model-id",
  "metadata": {"timeout": 30}
}
```

TTS,火山:

```json
{
  "kind": "tts",
  "provider": "volcengine",
  "base_url": "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
  "model": "S_xxxxx",
  "metadata": {
    "appid": "your-app-id",
    "app_key": "your-app-key-or-app-id",
    "voice_type": "S_xxxxx",
    "resource_id": "seed-tts-2.0",
    "format": "mp3",
    "sample_rate": 24000
  }
}
```

Search, model-native:

```json
{
  "kind": "search",
  "provider": "llm_web_search",
  "metadata": {
    "supports_web_search": true,
    "extra_body": {}
  }
}
```

Image, OpenAI-compatible:

```json
{
  "kind": "image",
  "provider": "openai_image",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-image-1",
  "metadata": {"size": "1024x1024"}
}
```
