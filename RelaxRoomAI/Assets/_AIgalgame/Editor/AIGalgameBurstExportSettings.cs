#if UNITY_EDITOR
using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace AIgalgame.EditorTools
{
    internal static class AIGalgameBurstExportSettings
    {
        public static Action DisableBurstAotForExport(BuildTarget target)
        {
            try
            {
                var burstEditorAssembly = AppDomain.CurrentDomain.GetAssemblies()
                    .FirstOrDefault(assembly => assembly.GetName().Name == "Unity.Burst.Editor");
                if (burstEditorAssembly == null)
                {
                    Debug.LogWarning("Unity.Burst.Editor assembly not found. Relying on BurstAotSettings JSON only.");
                    return () => { };
                }

                var settingsType = burstEditorAssembly.GetType("Unity.Burst.Editor.BurstPlatformAotSettings");
                if (settingsType == null)
                {
                    return () => { };
                }

                var getOrCreate = settingsType.GetMethod(
                    "GetOrCreateSettings",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                if (getOrCreate == null)
                {
                    return () => { };
                }

                var settings = getOrCreate.Invoke(null, new object[] { target });
                if (settings == null)
                {
                    return () => { };
                }

                var enableField = settingsType.GetField(
                    "EnableBurstCompilation",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (enableField == null)
                {
                    return () => { };
                }

                var previous = (bool)enableField.GetValue(settings);
                if (previous)
                {
                    enableField.SetValue(settings, false);

                    var saveMethod = settingsType.GetMethod(
                        "Save",
                        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                    saveMethod?.Invoke(settings, null);
                    AssetDatabase.SaveAssets();
                    Debug.Log($"Burst AOT disabled for {target} during RelaxRoom RN export.");
                }

                return () => { };
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"Unable to toggle Burst AOT settings via editor API: {exception.Message}");
                return () => { };
            }
        }
    }
}
#endif
