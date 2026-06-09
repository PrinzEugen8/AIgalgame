package com.aigalgame.demo

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

val Context.settingsDataStore by preferencesDataStore("aigalgame_settings")

class SettingsStore(private val context: Context) {
    private object Keys {
        val ServerAddress = stringPreferencesKey("server_address")
    }

    val baseUrl: Flow<String> = context.settingsDataStore.data.map { prefs ->
        prefs[Keys.ServerAddress] ?: ""
    }

    suspend fun saveBaseUrl(value: String) {
        context.settingsDataStore.edit { prefs ->
            prefs[Keys.ServerAddress] = value.trim().trimEnd('/')
        }
    }
}
