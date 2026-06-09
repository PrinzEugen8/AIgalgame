package com.aigalgame.demo

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.flow.first
import org.json.JSONObject

class NotificationWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val baseUrl = applicationContext.settingsDataStore.data.first()[androidx.datastore.preferences.core.stringPreferencesKey("server_address")].orEmpty()
        if (baseUrl.isBlank()) return Result.success()
        return try {
            val api = ApiClient(baseUrl)
            val state = api.widgetState()
            SakuraWidgetProvider.updateAll(
                applicationContext,
                state.optString("character_name", "小樱"),
                state.optString("status", "想聊天"),
                state.optString("bubble", "今天也想听你说说话。")
            )
            if (state.optInt("unread_count", 0) > 0) {
                notify(state)
            }
            Result.success()
        } catch (_: Exception) {
            Result.retry()
        }
    }

    private fun notify(state: JSONObject) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel("sakura", "小樱主动消息", NotificationManager.IMPORTANCE_DEFAULT)
            applicationContext.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return
        }
        val intent = PendingIntent.getActivity(
            applicationContext,
            31,
            Intent(applicationContext, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(applicationContext, "sakura")
            .setSmallIcon(R.drawable.ic_stat_sakura)
            .setContentTitle("小樱")
            .setContentText(state.optString("bubble", "有新的消息想告诉你。"))
            .setContentIntent(intent)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(applicationContext).notify(1001, notification)
    }
}
