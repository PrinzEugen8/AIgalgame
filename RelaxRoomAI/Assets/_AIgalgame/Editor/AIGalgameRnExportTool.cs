#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
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
        private const string ArtifactReportRelativePath = "../relaxroom-mobile/unity/build-artifacts.json";
        private const string Scene01Path = "Assets/_AIgalgame/Scenes/Scene_01.unity";

        private const string SherpaRuntimeAarRelativePath =
            "../relaxroom-mobile/android/app/libs/sherpa-onnx-static-link-onnxruntime-1.13.2.aar";

        private const string SherpaAssetsAarRelativePath =
            "../relaxroom-mobile/android/app/libs/aigalgame-sherpa-asr-assets.aar";

        private const string RnDefineSymbol = "RELAXROOM_RN";

        [MenuItem("Tools/AIgalgame/RN/Export Android Library (OptimizeSpeed)")]
        public static void ExportAndroidLibraryOptimizeSpeed()
        {
            ExportAndroidLibrary(Il2CppCodeGeneration.OptimizeSpeed);
        }

        [MenuItem("Tools/AIgalgame/RN/Export Android Library (OptimizeSize A/B)")]
        public static void ExportAndroidLibraryOptimizeSize()
        {
            ExportAndroidLibrary(Il2CppCodeGeneration.OptimizeSize);
        }

        [MenuItem("Tools/AIgalgame/RN/Export Android Library")]
        public static void ExportAndroidLibraryMenu()
        {
            ExportAndroidLibraryOptimizeSpeed();
        }

        private static void ExportAndroidLibrary(Il2CppCodeGeneration il2CppCodeGeneration)
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
            var previousIl2CppCodeGeneration = PlayerSettings.GetIl2CppCodeGeneration(NamedBuildTarget.Android);
            try
            {
                restoreActions.Add(ApplyRnBuildSettings());
                restoreActions.Add(AIGalgameBurstExportSettings.DisableBurstAotForExport(BuildTarget.Android));

                EditorUserBuildSettings.exportAsGoogleAndroidProject = true;
                EditorUserBuildSettings.androidBuildSystem = AndroidBuildSystem.Gradle;
                EditorUserBuildSettings.buildAppBundle = false;
                EditorUserBuildSettings.development = false;
                PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
                PlayerSettings.Android.fullscreenMode = FullScreenMode.Windowed;
                PlayerSettings.mipStripping = false;
                PlayerSettings.SetManagedStrippingLevel(NamedBuildTarget.Android, ManagedStrippingLevel.High);
                PlayerSettings.SetIl2CppCodeGeneration(NamedBuildTarget.Android, il2CppCodeGeneration);

                var options = new BuildPlayerOptions
                {
                    scenes = GetRnScenePaths(),
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
                PatchUnityLibraryGradle(exportPath);
                WriteExportArtifactReport(projectRoot, exportPath, il2CppCodeGeneration, report);
                Debug.Log($"RelaxRoom Android library exported to: {exportPath}");
            }
            finally
            {
                PlayerSettings.SetIl2CppCodeGeneration(NamedBuildTarget.Android, previousIl2CppCodeGeneration);
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
            var previousShowSplashScreen = PlayerSettings.SplashScreen.show;
            var previousInputHandler = GetActiveInputHandler();
            var previousDefines = PlayerSettings.GetScriptingDefineSymbols(NamedBuildTarget.Android);

            PlayerSettings.SplashScreen.show = false;
            PlayerSettings.SplashScreen.animationMode = PlayerSettings.SplashScreen.AnimationMode.Static;
            PlayerSettings.SplashScreen.showUnityLogo = false;
            SetActiveInputHandler(0);

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
                PlayerSettings.SplashScreen.show = previousShowSplashScreen;
                PlayerSettings.SplashScreen.animationMode = previousSplashAnimation;
                PlayerSettings.SplashScreen.showUnityLogo = previousShowUnityLogo;
                SetActiveInputHandler(previousInputHandler);
                PlayerSettings.SetScriptingDefineSymbols(NamedBuildTarget.Android, previousDefines);
            };
        }

        private static SerializedObject GetProjectSettingsSerializedObject()
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset");
            return assets.Length > 0 ? new SerializedObject(assets[0]) : null;
        }

        private static int GetActiveInputHandler()
        {
            var settings = GetProjectSettingsSerializedObject();
            if (settings == null)
            {
                return 0;
            }

            return settings.FindProperty("activeInputHandler")?.intValue ?? 0;
        }

        private static void SetActiveInputHandler(int value)
        {
            var settings = GetProjectSettingsSerializedObject();
            if (settings == null)
            {
                return;
            }

            var property = settings.FindProperty("activeInputHandler");
            if (property == null)
            {
                return;
            }

            property.intValue = value;
            settings.ApplyModifiedPropertiesWithoutUndo();
        }

        private static string[] GetRnScenePaths()
        {
            if (!File.Exists(Scene01Path))
            {
                throw new BuildFailedException($"RelaxRoom RN export requires Scene_01 at: {Scene01Path}");
            }

            var rnScenes = new[] { Scene01Path };
            ValidateRnSceneList(rnScenes);
            return rnScenes;
        }

        private static void ValidateRnSceneList(IReadOnlyList<string> scenes)

        {

            if (scenes.Count != 1 || scenes[0] != Scene01Path)

            {

                throw new BuildFailedException(
                    $"RelaxRoom RN export must use exactly {Scene01Path}. Actual: {string.Join(", ", scenes)}");

            }

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
                "android:extractNativeLibs=\"true\"",
                "android:extractNativeLibs=\"false\"");
            manifest = manifest.Replace(
                "android:name=\"unity.splash-enable\" android:value=\"True\"",
                "android:name=\"unity.splash-enable\" android:value=\"False\"");
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

        private static void PatchUnityLibraryGradle(string exportPath)
        {
            var gradlePath = Path.Combine(exportPath, "unityLibrary", "build.gradle");
            if (!File.Exists(gradlePath))
            {
                Debug.LogWarning($"Gradle file not found at {gradlePath}.");
                return;
            }

            var gradle = File.ReadAllText(gradlePath);
            gradle = gradle.Replace("useLegacyPackaging true", "useLegacyPackaging false");
            gradle = Regex.Replace(gradle, @"(?m)^(\s*)minSdk\s+\d+\s*$", "$1minSdk 24");
            gradle = Regex.Replace(gradle, @"(?m)^(\s*)minSdkVersion\s+\d+\s*$", "$1minSdkVersion 24");
            gradle = Regex.Replace(
                gradle,
                @"^\s*implementation\(name: 'sherpa-onnx-static-link-onnxruntime-[^']+', ext:'aar'\)\s*\r?\n",
                string.Empty,
                RegexOptions.Multiline);
            File.WriteAllText(gradlePath, gradle);
        }

        private static void WriteExportArtifactReport(
            string projectRoot,
            string exportPath,
            Il2CppCodeGeneration il2CppCodeGeneration,
            BuildReport report)
        {
            var sceneList = GetRnScenePaths();

            var artifacts = CollectArtifactSizes(projectRoot, exportPath);
            var totalBytes = artifacts.Sum(entry => entry.bytes);

            foreach (var artifact in artifacts)
            {
                Debug.Log($"[RelaxRoomExport] {artifact.name}={artifact.sizeMb:F2} MB");
            }

            Debug.Log($"[RelaxRoomExport] scenes={string.Join(",", sceneList)}");

            Debug.Log($"[RelaxRoomExport] total_tracked={totalBytes / (1024f * 1024f):F2} MB");

            var reportPath = Path.GetFullPath(Path.Combine(projectRoot, ArtifactReportRelativePath));
            Directory.CreateDirectory(Path.GetDirectoryName(reportPath)!);

            var builder = new StringBuilder();
            builder.Append("{\n");
            builder.Append("  \"generated_at_utc\": \"").Append(System.DateTime.UtcNow.ToString("o")).Append("\",\n");
            builder.Append("  \"il2cpp_code_generation\": \"").Append(il2CppCodeGeneration).Append("\",\n");
            builder.Append("  \"build_result\": \"").Append(report.summary.result).Append("\",\n");
            builder.Append("  \"scene_list\": [");
            for (var i = 0; i < sceneList.Length; i++)
            {
                if (i > 0)
                {
                    builder.Append(", ");
                }

                builder.Append('"').Append(EscapeJson(sceneList[i])).Append('"');
            }

            builder.Append("],\n");

            builder.Append("  \"total_size_mb\": ").Append((totalBytes / (1024f * 1024f)).ToString("F2")).Append(",\n");
            builder.Append("  \"artifacts\": [\n");
            for (var i = 0; i < artifacts.Count; i++)
            {
                var artifact = artifacts[i];
                builder.Append("    {\"name\":\"").Append(EscapeJson(artifact.name)).Append("\",\"path\":\"");
                builder.Append(EscapeJson(artifact.relativePath)).Append("\",\"size_bytes\":");
                builder.Append(artifact.bytes).Append(",\"size_mb\":");
                builder.Append(artifact.sizeMb.ToString("F2")).Append('}');
                if (i < artifacts.Count - 1)
                {
                    builder.Append(',');
                }

                builder.Append('\n');
            }

            builder.Append("  ]\n}\n");
            File.WriteAllText(reportPath, builder.ToString(), Encoding.UTF8);
            Debug.Log($"[RelaxRoomExport] artifact report written to {reportPath}");
        }

        private static List<(string name, string relativePath, long bytes, float sizeMb)> CollectArtifactSizes(
            string projectRoot,
            string exportPath)
        {
            var tracked = new (string name, string relativePath)[]
            {
                ("libil2cpp.so", Path.Combine("unityLibrary", "src", "main", "jniLibs", "arm64-v8a", "libil2cpp.so")),
                ("libunity.so", Path.Combine("unityLibrary", "src", "main", "jniLibs", "arm64-v8a", "libunity.so")),
                ("libmain.so", Path.Combine("unityLibrary", "src", "main", "jniLibs", "arm64-v8a", "libmain.so")),
                ("global-metadata.dat", Path.Combine("unityLibrary", "src", "main", "assets", "bin", "Data", "Managed", "Metadata", "global-metadata.dat")),
                ("data.unity3d", Path.Combine("unityLibrary", "src", "main", "assets", "bin", "Data", "data.unity3d")),
            };

            var results = new List<(string name, string relativePath, long bytes, float sizeMb)>();
            foreach (var entry in tracked)
            {
                var fullPath = Path.Combine(exportPath, entry.relativePath);
                if (!File.Exists(fullPath))
                {
                    continue;
                }

                var bytes = new FileInfo(fullPath).Length;
                results.Add((entry.name, entry.relativePath.Replace('\\', '/'), bytes, bytes / (1024f * 1024f)));
            }

            AddArtifactIfExists(projectRoot, results, "sherpa-runtime-aar", SherpaRuntimeAarRelativePath);
            AddArtifactIfExists(projectRoot, results, "sherpa-assets-aar", SherpaAssetsAarRelativePath);

            return results;
        }

        private static void AddArtifactIfExists(
            string projectRoot,
            List<(string name, string relativePath, long bytes, float sizeMb)> results,
            string name,
            string projectRelativePath)

        {

            var fullPath = Path.GetFullPath(Path.Combine(projectRoot, projectRelativePath));

            if (!File.Exists(fullPath))

            {

                return;

            }

            var bytes = new FileInfo(fullPath).Length;

            results.Add((name, projectRelativePath.Replace('\\', '/'), bytes, bytes / (1024f * 1024f)));

        }



        private static string EscapeJson(string value)
        {
            return value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"");
        }
    }
}
#endif
