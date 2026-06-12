package com.aigalgame.demo

import android.annotation.SuppressLint
import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.time.Instant
import java.time.OffsetDateTime

private const val CommitmentReminderAction = "com.aigalgame.demo.COMMITMENT_REMINDER"
private const val ExtraCommitmentId = "commitment_id"
private const val ExtraProactiveEventId = "proactive_event_id"
private const val ExtraReminderTitle = "title"
private const val ExtraReminderText = "text"

internal fun scheduleCommitmentReminder(context: Context, commitment: JSONObject): Boolean {
    val commitmentId = commitment.optString("commitment_id")
    val proactiveEventId = commitment.optString("proactive_event_id")
    val remindAt = commitment.optString("remind_at")
    val triggerAtMillis = parseReminderMillis(remindAt) ?: return false
    if (commitmentId.isBlank() || triggerAtMillis <= System.currentTimeMillis() - 60_000L) {
        return false
    }

    val title = reminderTitle(commitment)
    val text = reminderText(commitment)
    val intent = Intent(context, CommitmentReminderReceiver::class.java)
        .setAction(CommitmentReminderAction)
        .putExtra(ExtraCommitmentId, commitmentId)
        .putExtra(ExtraProactiveEventId, proactiveEventId)
        .putExtra(ExtraReminderTitle, title)
        .putExtra(ExtraReminderText, text)
    val pendingIntent = PendingIntent.getBroadcast(
        context,
        commitmentId.hashCode() and Int.MAX_VALUE,
        intent,
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
    val alarmManager = context.getSystemService(AlarmManager::class.java) ?: return false
    setReminderAlarm(alarmManager, triggerAtMillis, pendingIntent)
    return true
}

@SuppressLint("ScheduleExactAlarm")
private fun setReminderAlarm(alarmManager: AlarmManager, triggerAtMillis: Long, pendingIntent: PendingIntent) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !alarmManager.canScheduleExactAlarms()) {
        alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, pendingIntent)
        return
    }
    alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, pendingIntent)
}

private fun parseReminderMillis(value: String): Long? {
    return runCatching { OffsetDateTime.parse(value).toInstant().toEpochMilli() }.getOrNull()
        ?: runCatching { Instant.parse(value).toEpochMilli() }.getOrNull()
}

private fun reminderTitle(commitment: JSONObject): String {
    val title = commitment.optString("title").trim()
    return if (title.isBlank() || title == "\u7684\u65f6\u5019") {
        "\u63d0\u9192\u65f6\u95f4\u5230\u4e86"
    } else {
        title
    }
}

private fun reminderText(commitment: JSONObject): String {
    return commitment.optString("description").ifBlank {
        commitment.optString("title").ifBlank { "\u4f60\u4e4b\u524d\u8ba9\u6211\u5230\u70b9\u53eb\u4f60\u3002" }
    }
}

class CommitmentReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != CommitmentReminderAction) return
        val eventId = intent.getStringExtra(ExtraProactiveEventId).orEmpty()
        val title = intent.getStringExtra(ExtraReminderTitle).orEmpty().ifBlank { "\u63d0\u9192\u65f6\u95f4\u5230\u4e86" }
        val text = intent.getStringExtra(ExtraReminderText).orEmpty().ifBlank { "\u4f60\u4e4b\u524d\u8ba9\u6211\u5230\u70b9\u53eb\u4f60\u3002" }
        val state = JSONObject()
            .put("proactive_event_id", eventId)
            .put("title", title)
            .put("text", text)
            .put("source_type", "appointment")
        val posted = showProactiveNotification(context, state)
        SakuraWidgetProvider.updateAll(
            context,
            "Sakura",
            "\u63d0\u9192",
            text,
            if (eventId.isBlank()) 0 else 1,
            eventId,
            null
        )
        if (!posted || eventId.isBlank()) return

        val pendingResult = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                val prefs = context.settingsDataStore.data.first()
                val baseUrl = prefs[stringPreferencesKey("server_address")].orEmpty()
                if (baseUrl.isNotBlank()) {
                    ApiClient(baseUrl).markProactiveDelivered(eventId)
                }
            } catch (_: Exception) {
            } finally {
                pendingResult.finish()
            }
        }
    }
}
