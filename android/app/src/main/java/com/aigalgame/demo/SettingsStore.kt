package com.aigalgame.demo

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

val Context.settingsDataStore by preferencesDataStore("aigalgame_settings")

class SettingsStore(private val context: Context) {
    private object Keys {
        val ServerAddress = stringPreferencesKey("server_address")
        val SelectedCharacter = stringPreferencesKey("selected_character")
        val SelectedBackground = stringPreferencesKey("selected_background")
        val PreviewEmotion = stringPreferencesKey("preview_emotion")
        val TtsEnabled = booleanPreferencesKey("tts_enabled")
        val NotificationsEnabled = booleanPreferencesKey("notifications_enabled")
        val LastLocationUploadedAt = longPreferencesKey("last_location_uploaded_at")
        val Live2dAppearanceId = stringPreferencesKey("live2d_appearance_id")
        val Live2dConfigVersion = stringPreferencesKey("live2d_config_version")
    }

    val baseUrl: Flow<String> = context.settingsDataStore.data.map { prefs ->
        prefs[Keys.ServerAddress] ?: ""
    }.distinctUntilChanged()

    val uiSettings: Flow<LocalUiSettings> = context.settingsDataStore.data.map { prefs ->
        LocalUiSettings(
            selectedCharacter = prefs[Keys.SelectedCharacter] ?: "neko",
            selectedBackground = prefs[Keys.SelectedBackground] ?: "classroom",
            previewEmotion = prefs[Keys.PreviewEmotion] ?: "calm",
            ttsEnabled = prefs[Keys.TtsEnabled] ?: true,
            notificationsEnabled = prefs[Keys.NotificationsEnabled] ?: true,
            placements = mapOf(
                "neko" to readPlacement(prefs, "neko", OutfitPlacement(scale = 1.10f, offsetY = -10f, bottomInset = 30f)),
                "atri" to readPlacement(prefs, "atri", OutfitPlacement(scale = 1.14f, offsetY = -12f, bottomInset = 34f)),
                "murasame" to readPlacement(prefs, "murasame", OutfitPlacement(scale = 1.08f, offsetY = -6f, bottomInset = 42f))
            )
        )
    }.distinctUntilChanged()

    suspend fun saveBaseUrl(value: String) {
        context.settingsDataStore.edit { prefs ->
            prefs[Keys.ServerAddress] = normalizeBackendUrl(value)
        }
    }

    suspend fun saveSelectedCharacter(value: String) {
        context.settingsDataStore.edit { prefs -> prefs[Keys.SelectedCharacter] = value }
    }

    suspend fun saveSelectedBackground(value: String) {
        context.settingsDataStore.edit { prefs -> prefs[Keys.SelectedBackground] = value }
    }

    suspend fun savePreviewEmotion(value: String) {
        context.settingsDataStore.edit { prefs -> prefs[Keys.PreviewEmotion] = value }
    }

    suspend fun saveTtsEnabled(value: Boolean) {
        context.settingsDataStore.edit { prefs -> prefs[Keys.TtsEnabled] = value }
    }

    suspend fun saveNotificationsEnabled(value: Boolean) {
        context.settingsDataStore.edit { prefs -> prefs[Keys.NotificationsEnabled] = value }
    }

    suspend fun readLastLocationUploadedAt(): Long {
        return context.settingsDataStore.data.first()[Keys.LastLocationUploadedAt] ?: 0L
    }

    suspend fun saveLastLocationUploadedAt(value: Long) {
        context.settingsDataStore.edit { prefs -> prefs[Keys.LastLocationUploadedAt] = value }
    }

    suspend fun readLive2dSyncState(): Live2dSyncState {
        val prefs = context.settingsDataStore.data.first()
        return Live2dSyncState(
            appearanceId = prefs[Keys.Live2dAppearanceId] ?: "",
            configVersion = prefs[Keys.Live2dConfigVersion] ?: "",
        )
    }

    suspend fun saveLive2dSyncState(appearanceId: String, configVersion: String) {
        context.settingsDataStore.edit { prefs ->
            prefs[Keys.Live2dAppearanceId] = appearanceId
            prefs[Keys.Live2dConfigVersion] = configVersion
        }
    }

    suspend fun savePlacement(character: String, placement: OutfitPlacement) {
        val next = placement.coerceForStage()
        context.settingsDataStore.edit { prefs ->
            prefs[placementKey(character, "scale")] = next.scale
            prefs[placementKey(character, "offset_x")] = next.offsetX
            prefs[placementKey(character, "offset_y")] = next.offsetY
            prefs[placementKey(character, "bottom_inset")] = next.bottomInset
        }
    }

    private fun readPlacement(
        prefs: androidx.datastore.preferences.core.Preferences,
        character: String,
        fallback: OutfitPlacement
    ): OutfitPlacement {
        return OutfitPlacement(
            scale = prefs[placementKey(character, "scale")] ?: fallback.scale,
            offsetX = prefs[placementKey(character, "offset_x")] ?: fallback.offsetX,
            offsetY = prefs[placementKey(character, "offset_y")] ?: fallback.offsetY,
            bottomInset = prefs[placementKey(character, "bottom_inset")] ?: fallback.bottomInset
        ).coerceForStage()
    }

    private fun placementKey(character: String, field: String) = floatPreferencesKey("placement_${character}_$field")
}

data class Live2dSyncState(
    val appearanceId: String,
    val configVersion: String,
)

data class LocalUiSettings(
    val selectedCharacter: String,
    val selectedBackground: String,
    val previewEmotion: String,
    val ttsEnabled: Boolean,
    val notificationsEnabled: Boolean,
    val placements: Map<String, OutfitPlacement>
)
