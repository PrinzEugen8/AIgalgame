using System;
using System.Runtime.InteropServices;
using UnityEngine;

namespace AIgalgame.Motion
{
    public static class ReactNativeMessenger
    {
#if UNITY_IOS && !UNITY_EDITOR
        [DllImport("__Internal")]
        private static extern void sendMessageToMobileApp(string message);
#endif

        public static void Send(string json)
        {
            if (string.IsNullOrEmpty(json))
            {
                return;
            }

#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                using var bridge = new AndroidJavaClass("com.azesmwayreactnativeunity.ReactNativeUnityViewManager");
                bridge.CallStatic("sendMessageToMobileApp", json);
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"ReactNativeMessenger Android send failed: {exception.Message}");
            }
#elif UNITY_IOS && !UNITY_EDITOR
            sendMessageToMobileApp(json);
#else
            Debug.Log($"[ReactNativeMessenger] {json}");
#endif
        }
    }
}
