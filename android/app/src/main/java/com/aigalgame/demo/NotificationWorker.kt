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

private const val SakuraNotificationChannelId = "sakura"

internal fun shouldMarkProactiveDelivered(pendingEventId: String, notificationPosted: Boolean): Boolean {
    return pendingEventId.isNotBlank() && notificationPosted
}

internal fun showProactiveNotification(context: Context, state: JSONObject): Boolean {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val channel = NotificationChannel(SakuraNotificationChannelId, "Sakura proactive messages", NotificationManager.IMPORTANCE_DEFAULT)
        channel.lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
    if (!canPostProactiveNotifications(context)) {
        return false
    }
    val eventId = state.optString("proactive_event_id")
    val title = state.optString("title", "Sakura wants to talk")
    val text = state.optString("text", "There is a new message for you.")
    val largeIcon = BitmapFactory.decodeResource(context.resources, R.drawable.chibi_sakura_widget)
    val openIntent = Intent(context, MainActivity::class.java)
        .putExtra("proactive_event_id", eventId)
        .putExtra("proactive_open_type", "notification_opened")
    val intent = PendingIntent.getActivity(
        context,
        31 + eventId.hashCode(),
        openIntent,
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
    val notification = NotificationCompat.Builder(context, SakuraNotificationChannelId)
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
    return try {
        NotificationManagerCompat.from(context).notify(1001 + eventId.hashCode(), notification)
        true
    } catch (_: SecurityException) {
        false
    }
}

internal fun canPostProactiveNotifications(context: Context): Boolean {
    if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
        return false
    }
    val compat = NotificationManagerCompat.from(context)
    if (!compat.areNotificationsEnabled()) {
        return false
    }
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val manager = context.getSystemService(NotificationManager::class.java)
        val channel = manager.getNotificationChannel(SakuraNotificationChannelId)
        if (channel != null && channel.importance == NotificationManager.IMPORTANCE_NONE) {
            return false
        }
    }
    return true
}

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
            val pendingEventId = event.optString("proactive_event_id")
            val widgetEventId = widget.optString("proactive_event_id", pendingEventId)
            val chibiUrl = widget.optString("chibi_url")
            val chibi = if (chibiUrl.isNotBlank()) {
                try {
                    val bytes = HttpDownloader.bytes(api.absoluteUrl(chibiUrl))
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                } catch (_: Exception) {
                    null
                }
            } else {
                null
            }
            SakuraWidgetProvider.updateAll(
                applicationContext,
                widget.optString("character_name", "小樱"),
                widget.optString("status", "想聊天"),
                widget.optString("bubble", "今天也想听你说说话。"),
                widget.optInt("unread_count", 0),
                widgetEventId,
                chibi
            )
            if (pendingEventId.isNotBlank()) {
                val notificationPosted = if (prefs[booleanPreferencesKey("notifications_enabled")] ?: true) {
                    notify(event)
                } else {
                    false
                }
                if (shouldMarkProactiveDelivered(pendingEventId, notificationPosted)) {
                    api.markProactiveDelivered(pendingEventId)
                }
            }
            try {
                api.prepareOpening(widgetEventId)
            } catch (_: Exception) {
            }
            Result.success()
        } catch (_: Exception) {
            Result.retry()
        }
    }

    private fun notify(state: JSONObject): Boolean = showProactiveNotification(applicationContext, state)
}
