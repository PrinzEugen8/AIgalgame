package com.aigalgame.demo

import android.content.Context
import android.provider.Settings
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.json.JSONObject

internal fun androidDeviceId(context: Context): String {
    return Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID) ?: "android"
}

internal fun registerPushToken(context: Context, token: String) {
    if (token.isBlank()) return
    runBlocking {
        val prefs = context.settingsDataStore.data.first()
        val baseUrl = prefs[stringPreferencesKey("server_address")].orEmpty()
        val notificationsEnabled = prefs[booleanPreferencesKey("notifications_enabled")] ?: true
        if (baseUrl.isBlank() || !notificationsEnabled) return@runBlocking
        try {
            ApiClient(baseUrl).registerDevice(
                deviceId = androidDeviceId(context),
                pushToken = token,
                notificationsEnabled = notificationsEnabled
            )
        } catch (_: Exception) {
        }
    }
}

class SakuraFirebaseMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        registerPushToken(applicationContext, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        val eventId = data["proactive_event_id"].orEmpty()
        if (eventId.isBlank()) return
        val title = data["title"] ?: message.notification?.title ?: "Sakura wants to talk"
        val text = data["text"] ?: message.notification?.body ?: "There is a new message for you."
        val state = JSONObject()
            .put("proactive_event_id", eventId)
            .put("title", title)
            .put("text", text)
            .put("source_type", data["source_type"].orEmpty())
        SakuraWidgetProvider.updateAll(
            applicationContext,
            "Sakura",
            "Has a message",
            text,
            1,
            eventId,
            null
        )
        showProactiveNotification(applicationContext, state)
    }
}
