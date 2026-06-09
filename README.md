# AI Galgame Android Demo

This workspace contains a local-LAN Android demo and a Python backend for an AI Galgame companion.

- `backend/`: FastAPI service, SQLite state, provider configuration, Galgame pipeline, schedule/moment systems.
- `android/`: Kotlin/Compose Android app for real-device LAN testing.

Secrets must be configured locally through the backend admin page; they are never committed or packaged into the APK.

## Quick Start

1. Start the backend:

   ```powershell
   cd E:\AIgalgame
   .\backend\run.ps1
   ```

2. Find the PC LAN IP:

   ```powershell
   ipconfig
   ```

3. Open the backend admin page on the PC and configure real services:

   ```text
   http://<PC-LAN-IP>:8899/admin
   ```

4. Build the APK:

   ```powershell
   cd E:\AIgalgame\android
   .\gradlew.bat :app:assembleDebug
   ```

5. Install to a connected Android device:

   ```powershell
   cd E:\AIgalgame
   .\scripts\install_debug_apk.ps1
   ```

6. In the app, connect to `http://<PC-LAN-IP>:8899`.

Provider tests must pass in the backend admin page before real LLM/TTS/search/image flows can be accepted. The app no longer contains service keys or provider configuration fields.
