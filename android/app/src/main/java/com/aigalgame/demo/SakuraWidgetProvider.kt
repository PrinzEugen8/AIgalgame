package com.aigalgame.demo

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.view.View
import android.widget.RemoteViews

class SakuraWidgetProvider : AppWidgetProvider() {
    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        appWidgetIds.forEach { id ->
            updateWidget(context, appWidgetManager, id, "小樱", "想聊天", "今天也想听你说说话。", 0, "")
        }
    }

    companion object {
        fun updateAll(context: Context, name: String, status: String, bubble: String, unreadCount: Int, proactiveEventId: String = "") {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, SakuraWidgetProvider::class.java))
            ids.forEach { updateWidget(context, manager, it, name, status, bubble, unreadCount, proactiveEventId) }
        }

        private fun updateWidget(
            context: Context,
            manager: AppWidgetManager,
            id: Int,
            name: String,
            status: String,
            bubble: String,
            unreadCount: Int,
            proactiveEventId: String
        ) {
            val views = RemoteViews(context.packageName, R.layout.widget_sakura)
            views.setTextViewText(R.id.widget_name, name)
            views.setTextViewText(R.id.widget_status, status)
            views.setTextViewText(R.id.widget_bubble, bubble)
            views.setViewVisibility(R.id.widget_badge, if (unreadCount > 0) View.VISIBLE else View.GONE)
            views.setTextViewText(R.id.widget_badge, unreadCount.coerceAtMost(99).toString())
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
    }
}
