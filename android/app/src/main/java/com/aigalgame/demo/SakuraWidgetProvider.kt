package com.aigalgame.demo

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

class SakuraWidgetProvider : AppWidgetProvider() {
    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        appWidgetIds.forEach { id ->
            updateWidget(context, appWidgetManager, id, "小樱", "想聊天", "今天也想听你说说话。")
        }
    }

    companion object {
        fun updateAll(context: Context, name: String, status: String, bubble: String) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, SakuraWidgetProvider::class.java))
            ids.forEach { updateWidget(context, manager, it, name, status, bubble) }
        }

        private fun updateWidget(
            context: Context,
            manager: AppWidgetManager,
            id: Int,
            name: String,
            status: String,
            bubble: String
        ) {
            val views = RemoteViews(context.packageName, R.layout.widget_sakura)
            views.setTextViewText(R.id.widget_name, name)
            views.setTextViewText(R.id.widget_status, status)
            views.setTextViewText(R.id.widget_bubble, bubble)
            val pending = PendingIntent.getActivity(
                context,
                11,
                Intent(context, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.widget_root, pending)
            manager.updateAppWidget(id, views)
        }
    }
}
