package com.aigalgame.demo

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.view.View
import android.widget.RemoteViews
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager

class SakuraWidgetProvider : AppWidgetProvider() {
    override fun onEnabled(context: Context) {
        refreshNow(context)
    }

    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        appWidgetIds.forEach { id ->
            updateWidget(context, appWidgetManager, id, "小樱", "想聊天", "今天也想听你说说话。", 0, "", null)
        }
        enqueueRefresh(context)
    }

    companion object {
        fun refreshNow(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, SakuraWidgetProvider::class.java))
            ids.forEach { updateWidget(context, manager, it, "小樱", "想聊天", "今天也想听你说说话。", 0, "", null) }
            enqueueRefresh(context)
        }

        fun updateAll(context: Context, name: String, status: String, bubble: String, unreadCount: Int, proactiveEventId: String = "", chibi: Bitmap? = null) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, SakuraWidgetProvider::class.java))
            ids.forEach { updateWidget(context, manager, it, name, status, bubble, unreadCount, proactiveEventId, chibi) }
        }

        fun clearUnread(context: Context) {
            updateAll(context, "小樱", "已读", "我在这里，慢慢说就好。", 0, "")
        }

        private fun updateWidget(
            context: Context,
            manager: AppWidgetManager,
            id: Int,
            name: String,
            status: String,
            bubble: String,
            unreadCount: Int,
            proactiveEventId: String,
            chibi: Bitmap?
        ) {
            val views = RemoteViews(context.packageName, R.layout.widget_sakura)
            views.setTextViewText(R.id.widget_name, name)
            views.setTextViewText(R.id.widget_status, status)
            views.setTextViewText(R.id.widget_bubble, bubble)
            views.setViewVisibility(R.id.widget_badge, if (unreadCount > 0) View.VISIBLE else View.GONE)
            views.setTextViewText(R.id.widget_badge, unreadCount.coerceAtMost(99).toString())
            if (chibi != null) {
                views.setImageViewBitmap(R.id.widget_icon, chibi)
            } else {
                views.setImageViewResource(R.id.widget_icon, R.drawable.chibi_sakura_widget)
            }
            val pending = PendingIntent.getActivity(
                context,
                11,
                Intent(context, MainActivity::class.java)
                    .putExtra("proactive_event_id", proactiveEventId)
                    .putExtra("proactive_open_type", "widget_opened"),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.widget_root, pending)
            manager.updateAppWidget(id, views)
        }

        private fun enqueueRefresh(context: Context) {
            WorkManager.getInstance(context).enqueueUniqueWork(
                "sakura_widget_refresh_once",
                ExistingWorkPolicy.REPLACE,
                OneTimeWorkRequestBuilder<NotificationWorker>().build()
            )
        }
    }
}
