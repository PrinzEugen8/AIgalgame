#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AIgalgame.EditorTools
{
    public static class AIGalgameRnExportTool
    {
        private const string ExportRelativePath = "../relaxroom-mobile/unity/builds/android";
        private const string RnDefineSymbol = "RELAXROOM_RN";
        private static readonly string[] SherpaPluginPaths =
        {
            "Assets/Plugins/Android/sherpa-onnx-static-link-onnxruntime-1.13.2.aar",
            "Assets/Plugins/Android/sherpa-onnx-static-link-onnxruntime-1.13.2.aar.meta",
        };

        [MenuItem("Tools/AIgalgame/RN/Export Android Library")]
        public static void ExportAndroidLibrary()
        {
            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName;
            if (string.IsNullOrWhiteSpace(projectRoot))
            {
                Debug.LogError("Unable to resolve Unity project root.");
                return;
            }

            var exportPath = Path.GetFullPath(Path.Combine(projectRoot, ExportRelativePath));
            if (Directory.Exists(exportPath))
            {
                Directory.Delete(exportPath, true);
            }

            Directory.CreateDirectory(exportPath);

            var restoreActions = new List<System.Action>();
            try
            {
                restoreActions.Add(ApplyRnBuildSettings());
                restoreActions.Add(StripRnSceneObjects());
                restoreActions.Add(DisableSherpaPluginsForExport());
                restoreActions.Add(AIGalgameBurstExportSettings.DisableBurstAotForExport(BuildTarget.Android));

                EditorUserBuildSettings.exportAsGoogleAndroidProject = true;
                EditorUserBuildSettings.androidBuildSystem = AndroidBuildSystem.Gradle;
                EditorUserBuildSettings.buildAppBundle = false;
                PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
                PlayerSettings.Android.fullscreenMode = FullScreenMode.Windowed;
                PlayerSettings.mipStripping = false;
                PlayerSettings.SetManagedStrippingLevel(NamedBuildTarget.Android, ManagedStrippingLevel.Low);

                var options = new BuildPlayerOptions
                {
                    scenes = GetEnabledScenePaths(),
                    locationPathName = exportPath,
                    target = BuildTarget.Android,
                    options = BuildOptions.CompressWithLz4,
                    extraScriptingDefines = new[] { RnDefineSymbol },
                };

                var report = BuildPipeline.BuildPlayer(options);
                if (report.summary.result != BuildResult.Succeeded)
                {
                    Debug.LogError($"RelaxRoom RN export failed: {report.summary.result}");
                    return;
                }

                PatchUnityLibraryManifest(exportPath);
                PatchUnityLibraryBootConfig(exportPath);
                Debug.Log($"RelaxRoom Android library exported to: {exportPath}");
            }
            finally
            {
                for (var i = restoreActions.Count - 1; i >= 0; i--)
                {
                    restoreActions[i]?.Invoke();
                }
            }
        }

        private static System.Action ApplyRnBuildSettings()
        {
            var previousSplashAnimation = PlayerSettings.SplashScreen.animationMode;
            var previousShowUnityLogo = PlayerSettings.SplashScreen.showUnityLogo;
            var previousDefines = PlayerSettings.GetScriptingDefineSymbols(NamedBuildTarget.Android);

            PlayerSettings.SplashScreen.animationMode = PlayerSettings.SplashScreen.AnimationMode.Static;
            PlayerSettings.SplashScreen.showUnityLogo = false;

            if (!previousDefines.Contains(RnDefineSymbol))
            {
                PlayerSettings.SetScriptingDefineSymbols(
                    NamedBuildTarget.Android,
                    string.IsNullOrWhiteSpace(previousDefines)
                        ? RnDefineSymbol
                        : previousDefines + ";" + RnDefineSymbol);
            }

            return () =>
            {
                PlayerSettings.SplashScreen.animationMode = previousSplashAnimation;
                PlayerSettings.SplashScreen.showUnityLogo = previousShowUnityLogo;
                PlayerSettings.SetScriptingDefineSymbols(NamedBuildTarget.Android, previousDefines);
            };
        }

        private static System.Action StripRnSceneObjects()
        {
            var scenePath = "Assets/Brick Project Studio/Apartment Kit/Scenes/Scene_01.unity";
            if (!File.Exists(scenePath))
            {
                return () => { };
            }

            var scene = UnityEditor.SceneManagement.EditorSceneManager.OpenScene(scenePath);
            var rootStates = new Dictionary<string, bool>();
            foreach (var rootName in new[] { "AIgalgame_RelaxRoomSceneUI", "EventSystem" })
            {
                var root = GameObject.Find(rootName);
                if (root != null)
                {
                    rootStates[rootName] = root.activeSelf;
                    root.SetActive(false);
                }
            }

            UnityEditor.SceneManagement.EditorSceneManager.SaveScene(scene);

            return () =>
            {
                var restoreScene = UnityEditor.SceneManagement.EditorSceneManager.OpenScene(scenePath);
                foreach (var rootState in rootStates)
                {
                    var root = GameObject.Find(rootState.Key);
                    if (root != null)
                    {
                        root.SetActive(rootState.Value);
                    }
                }

                UnityEditor.SceneManagement.EditorSceneManager.SaveScene(restoreScene);
            };
        }

        private static System.Action DisableSherpaPluginsForExport()
        {
            var disabledPaths = new List<string>();
            foreach (var assetPath in SherpaPluginPaths)
            {
                if (!File.Exists(assetPath))
                {
                    continue;
                }

                var importer = AssetImporter.GetAtPath(assetPath);
                if (importer is PluginImporter pluginImporter)
                {
                    pluginImporter.SetCompatibleWithPlatform(BuildTarget.Android, false);
                    pluginImporter.SaveAndReimport();
                    disabledPaths.Add(assetPath);
                }
            }

            return () =>
            {
                foreach (var assetPath in disabledPaths)
                {
                    var importer = AssetImporter.GetAtPath(assetPath);
                    if (importer is PluginImporter pluginImporter)
                    {
                        pluginImporter.SetCompatibleWithPlatform(BuildTarget.Android, true);
                        pluginImporter.SaveAndReimport();
                    }
                }
            };
        }

        private static string[] GetEnabledScenePaths()
        {
            var scenes = EditorBuildSettings.scenes;
            var enabled = new List<string>();
            foreach (var scene in scenes)
            {
                if (scene.enabled)
                {
                    enabled.Add(scene.path);
                }
            }

            if (enabled.Count == 0)
            {
                enabled.Add("Assets/Brick Project Studio/Apartment Kit/Scenes/Scene_01.unity");
            }

            return enabled.ToArray();
        }

        private static void PatchUnityLibraryManifest(string exportPath)
        {
            var manifestPath = Path.Combine(exportPath, "unityLibrary", "src", "main", "AndroidManifest.xml");
            if (!File.Exists(manifestPath))
            {
                Debug.LogWarning($"Manifest not found at {manifestPath}. Apply react-native-unity manifest patch manually.");
                return;
            }

            var manifest = File.ReadAllText(manifestPath);
            manifest = Regex.Replace(
                manifest,
                @"<intent-filter>[\s\S]*?android\.intent\.category\.LAUNCHER[\s\S]*?</intent-filter>",
                string.Empty);
            manifest = manifest.Replace(
                "android:name=\"unity.launch-fullscreen\" android:value=\"True\"",
                "android:name=\"unity.launch-fullscreen\" android:value=\"False\"");
            manifest = manifest.Replace(
                "android:hardwareAccelerated=\"false\"",
                "android:hardwareAccelerated=\"true\"");
            File.WriteAllText(manifestPath, manifest);
        }

        private static void PatchUnityLibraryBootConfig(string exportPath)
        {
            var bootConfigPath = Path.Combine(exportPath, "unityLibrary", "src", "main", "assets", "bin", "Data", "boot.config");
            if (!File.Exists(bootConfigPath))
            {
                Debug.LogWarning($"boot.config not found at {bootConfigPath}.");
                return;
            }

            var bootConfig = File.ReadAllText(bootConfigPath);
            bootConfig = bootConfig.Replace("androidStartInFullscreen=1", "androidStartInFullscreen=0");
            File.WriteAllText(bootConfigPath, bootConfig);
        }
    }
}
#endif
