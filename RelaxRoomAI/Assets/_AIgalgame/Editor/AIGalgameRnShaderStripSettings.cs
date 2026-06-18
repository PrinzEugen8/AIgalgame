#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering.Universal;

namespace AIgalgame.EditorTools
{
    public static class AIGalgameRnShaderStripSettings
    {
        private const string UrpAssetPath = "Assets/Settings/RelaxRoom Mobile URP Asset.asset";
        private const string UrpGlobalSettingsPath = "Assets/UniversalRenderPipelineGlobalSettings.asset";

        [MenuItem("Tools/AIgalgame/RN/Apply Mobile Shader Stripping")]
        public static void ApplyMobileShaderStripping()
        {
            ApplyToAssets(saveAssets: true, logResult: true);
        }

        public static void ApplyToAssets(bool saveAssets, bool logResult)
        {
            var urpAsset = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(UrpAssetPath);
            if (urpAsset != null)
            {
                var serialized = new SerializedObject(urpAsset);
                SetBool(serialized, "m_PrefilterDebugKeywords", true);
                SetBool(serialized, "m_PrefilterXRKeywords", true);
                SetBool(serialized, "m_PrefilterHDROutput", true);
                SetBool(serialized, "m_PrefilterSoftShadowsQualityHigh", false);
                SetBool(serialized, "m_PrefilterSoftShadowsQualityMedium", true);
                SetBool(serialized, "m_PrefilterSoftShadowsQualityLow", true);
                serialized.ApplyModifiedPropertiesWithoutUndo();
                EditorUtility.SetDirty(urpAsset);
            }

            var globalSettings = AssetDatabase.LoadMainAssetAtPath(UrpGlobalSettingsPath);
            if (globalSettings != null)
            {
                var serialized = new SerializedObject(globalSettings);
                SetBool(serialized, "m_StripUnusedPostProcessingVariants", true);
                SetBool(serialized, "m_StripUnusedVariants", true);
                SetBool(serialized, "m_StripDebugVariants", true);
                SetBool(serialized, "supportRuntimeDebugDisplay", false);
                serialized.ApplyModifiedPropertiesWithoutUndo();
                EditorUtility.SetDirty(globalSettings);
            }

            var graphicsSettings = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/GraphicsSettings.asset");
            if (graphicsSettings.Length > 0)
            {
                var serialized = new SerializedObject(graphicsSettings[0]);
                SetBool(serialized, "m_LightmapStripping", true);
                SetBool(serialized, "m_FogStripping", true);
                SetBool(serialized, "m_InstancingStripping", true);
                serialized.ApplyModifiedPropertiesWithoutUndo();
            }

            if (saveAssets)
            {
                AssetDatabase.SaveAssets();
            }

            if (logResult)
            {
                Debug.Log("RelaxRoom mobile shader stripping settings applied for RN export.");
            }
        }

        private static void SetBool(SerializedObject serialized, string propertyName, bool value)
        {
            var property = serialized.FindProperty(propertyName);
            if (property != null)
            {
                property.boolValue = value;
            }
        }
    }
}
#endif
