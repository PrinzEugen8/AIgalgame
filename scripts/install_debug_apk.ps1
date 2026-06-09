$ErrorActionPreference = "Stop"
$adb = "C:\Users\05\AppData\Local\Android\Sdk\platform-tools\adb.exe"
$apk = "E:\AIgalgame\android\app\build\outputs\apk\debug\app-debug.apk"
if (!(Test-Path $adb)) {
    throw "adb not found at $adb"
}
if (!(Test-Path $apk)) {
    throw "APK not found. Build first: cd E:\AIgalgame\android; .\gradlew.bat :app:assembleDebug"
}
& $adb devices
& $adb install -r $apk
