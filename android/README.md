# Android App

Build:

```powershell
cd E:\AIgalgame\android
.\gradlew.bat :app:assembleDebug
```

APK:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

The app is designed for a real Android device on the same LAN as the backend. Cleartext HTTP is enabled for local development, so `http://192.168.x.x:8899` works. Online service keys are configured only in the backend admin page.

Implemented surfaces:

- Galgame home screen with top status, Sakura character scene, dialogue box, normal/key replies, custom input, and TTS playback when backend provides audio.
- Bottom navigation with only Home, Dress Up, and Settings.
- Dress Up screen with local PNG character standees and scene selection.
- Moments feed with like/comment feedback.
- Calendar panel backed by 15-minute schedule slots.
- Journal/memory view.
- 2x4 home-screen AppWidget and periodic notification/widget polling.
