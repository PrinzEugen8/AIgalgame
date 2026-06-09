package com.aigalgame.demo

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.flow.first
import org.json.JSONObject

class NotificationWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val prefs = applicationContext.settingsDataStore.data.first()
        val baseUrl = prefs[stringPreferencesKey("server_address")].orEmpty()
        if (baseUrl.isBlank()) return Result.success()
        return try {
            val api = ApiClient(baseUrl)
            val state = api.proactivePending()
            val widget = state.optObject("widget")
            val event = state.optObject("event")
            val eventId = event.optString("proactive_event_id", widget.optString("proactive_event_id"))
            SakuraWidgetProvider.updateAll(
                applicationContext,
                widget.optString("character_name", "小樱"),
                widget.optString("status", "想聊天"),
                widget.optString("bubble", "今天也想听你说说话。"),
                widget.optInt("unread_count", 0),
                eventId
            )
            if (eventId.isNotBlank()) {
                if (prefs[booleanPreferencesKey("notifications_enabled")] ?: true) {
                    notify(event)
                }
                api.markProactiveDelivered(eventId)
            }
            Result.success()
        } catch (_: Exception) {
            Result.retry()
        }
    }

    private fun notify(state: JSONObject) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel("sakura", "小樱主动消息", NotificationManager.IMPORTANCE_DEFAULT)
            channel.lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            applicationContext.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return
        }
        val eventId = state.optString("proactive_event_id")
        val title = state.optString("title", "小樱想和你说话")
        val text = state.optString("text", "有新的消息想告诉你。")
        val largeIcon = BitmapFactory.decodeResource(applicationContext.resources, R.drawable.chibi_sakura_widget)
        val openIntent = Intent(applicationContext, MainActivity::class.java)
            .putExtra("proactive_event_id", eventId)
            .putExtra("proactive_open_type", "notification_opened")
        val intent = PendingIntent.getActivity(
            applicationContext,
            31 + eventId.hashCode(),
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(applicationContext, "sakura")
            .setSmallIcon(R.drawable.ic_stat_sakura)
            .setLargeIcon(largeIcon)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(intent)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(applicationContext).notify(1001, notification)
    }
}
