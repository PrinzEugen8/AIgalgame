using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

public static class ApartmentMaterialConverter
{
    private const string ApartmentKitFolder = "Assets/Brick Project Studio";

    [MenuItem("Tools/AIgalgame/Apartment Kit/1. Report Material Shaders")]
    public static void ReportMaterialShaders()
    {
        var materials = FindApartmentMaterials();
        var shaderCounts = new SortedDictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var targets = new List<string>();

        foreach (var material in materials)
        {
            var shaderName = material.shader != null ? material.shader.name : "<missing shader>";
            shaderCounts.TryGetValue(shaderName, out var count);
            shaderCounts[shaderName] = count + 1;

            if (NeedsConversion(material))
            {
                targets.Add($"{AssetDatabase.GetAssetPath(material)} -> {shaderName}");
            }
        }

        var builder = new StringBuilder();
        builder.AppendLine("Apartment Kit material shader report");
        builder.AppendLine($"Generated: {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
        builder.AppendLine($"Folder: {ApartmentKitFolder}");
        builder.AppendLine($"Material count: {materials.Count}");
        builder.AppendLine();
        builder.AppendLine("Shader counts:");
        foreach (var pair in shaderCounts)
        {
            builder.AppendLine($"- {pair.Key}: {pair.Value}");
        }

        builder.AppendLine();
        builder.AppendLine($"Materials that should be converted for URP: {targets.Count}");
        foreach (var line in targets)
        {
            builder.AppendLine($"- {line}");
        }

        var reportPath = "Assets/_AIgalgame/ApartmentMaterialShaderReport.txt";
        File.WriteAllText(reportPath, builder.ToString(), Encoding.UTF8);
        AssetDatabase.ImportAsset(reportPath);

        Debug.Log(builder.ToString());
        EditorUtility.DisplayDialog(
            "Apartment Material Report",
            $"Found {materials.Count} materials.\n{targets.Count} materials should be converted for URP.\n\nReport saved to:\n{reportPath}",
            "OK");
    }

    [MenuItem("Tools/AIgalgame/Apartment Kit/2. Convert Brick Project Studio Materials To URP Lit")]
    public static void ConvertApartmentMaterialsToUrpLit()
    {
        ConvertApartmentMaterialsToUrpLitInternal(true);
    }

    public static void ConvertApartmentMaterialsToUrpLitBatch()
    {
        ConvertApartmentMaterialsToUrpLitInternal(false);
    }

    private static void ConvertApartmentMaterialsToUrpLitInternal(bool showDialogs)
    {
        var materials = FindApartmentMaterials();
        var targets = new List<Material>();

        foreach (var material in materials)
        {
            if (NeedsConversion(material))
            {
                targets.Add(material);
            }
        }

        if (targets.Count == 0)
        {
            if (showDialogs)
            {
                EditorUtility.DisplayDialog("Apartment Material Converter", "No Built-in/Legacy materials were found under Brick Project Studio.", "OK");
            }

            Debug.Log("Apartment Material Converter: no materials need conversion.");
            return;
        }

        if (showDialogs)
        {
            var confirmed = EditorUtility.DisplayDialog(
                "Convert Apartment Materials",
                $"This will convert {targets.Count} materials under:\n{ApartmentKitFolder}\n\nA backup copy of every .mat file will be written to the project root before changes are saved.",
                "Convert",
                "Cancel");

            if (!confirmed)
            {
                return;
            }
        }

        var backupRoot = BackupMaterialFiles(targets);
        var converted = 0;

        try
        {
            AssetDatabase.StartAssetEditing();
            foreach (var material in targets)
            {
                ConvertMaterialToUrpLit(material);
                EditorUtility.SetDirty(material);
                converted++;
            }
        }
        finally
        {
            AssetDatabase.StopAssetEditing();
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
        }

        var message = $"Converted {converted} materials to URP Lit. Backup: {backupRoot}";
        Debug.Log($"Apartment Material Converter: {message}");
        if (showDialogs)
        {
            EditorUtility.DisplayDialog("Apartment Material Converter", message, "OK");
        }
    }

    private static List<Material> FindApartmentMaterials()
    {
        var materials = new List<Material>();
        var guids = AssetDatabase.FindAssets("t:Material", new[] { ApartmentKitFolder });
        foreach (var guid in guids)
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            var material = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (material != null)
            {
                materials.Add(material);
            }
        }

        return materials;
    }

    private static bool NeedsConversion(Material material)
    {
        if (material == null || material.shader == null)
        {
            return true;
        }

        var shaderName = material.shader.name;
        if (shaderName.StartsWith("Universal Render Pipeline/", StringComparison.Ordinal)
            || shaderName.StartsWith("Hidden/Universal Render Pipeline/", StringComparison.Ordinal)
            || shaderName.StartsWith("AIgalgame/", StringComparison.Ordinal)
            || shaderName.StartsWith("VRM10/", StringComparison.Ordinal))
        {
            return false;
        }

        return true;
    }

    private static Shader FindUrpLitShader()
    {
        var shader = Shader.Find("Universal Render Pipeline/Lit");
        if (shader != null)
        {
            return shader;
        }

        shader = Shader.Find("Universal Render Pipeline/Simple Lit");
        if (shader != null)
        {
            return shader;
        }

        throw new InvalidOperationException("Could not find Universal Render Pipeline/Lit or Universal Render Pipeline/Simple Lit.");
    }

    private static string BackupMaterialFiles(IReadOnlyList<Material> materials)
    {
        var projectRoot = Directory.GetCurrentDirectory();
        var backupRoot = Path.Combine(projectRoot, "MaterialBackups", $"ApartmentKit_{DateTime.Now:yyyyMMdd_HHmmss}");
        Directory.CreateDirectory(backupRoot);

        foreach (var material in materials)
        {
            var assetPath = AssetDatabase.GetAssetPath(material);
            var sourcePath = Path.Combine(projectRoot, assetPath);
            if (!File.Exists(sourcePath))
            {
                continue;
            }

            var destinationPath = Path.Combine(backupRoot, assetPath.Replace('/', Path.DirectorySeparatorChar));
            var destinationDirectory = Path.GetDirectoryName(destinationPath);
            if (!string.IsNullOrEmpty(destinationDirectory))
            {
                Directory.CreateDirectory(destinationDirectory);
            }

            File.Copy(sourcePath, destinationPath, true);
        }

        return backupRoot;
    }

    private static void ConvertMaterialToUrpLit(Material material)
    {
        var mainTexture = GetTexture(material, "_MainTex") ?? GetTexture(material, "_BaseMap");
        var mainTextureScale = GetTexture(material, "_MainTex") != null ? GetTextureScale(material, "_MainTex") : GetTextureScale(material, "_BaseMap");
        var mainTextureOffset = GetTexture(material, "_MainTex") != null ? GetTextureOffset(material, "_MainTex") : GetTextureOffset(material, "_BaseMap");
        var normalTexture = GetTexture(material, "_BumpMap") ?? GetTexture(material, "_NormalMap");
        var normalTextureScale = GetTexture(material, "_BumpMap") != null ? GetTextureScale(material, "_BumpMap") : GetTextureScale(material, "_NormalMap");
        var normalTextureOffset = GetTexture(material, "_BumpMap") != null ? GetTextureOffset(material, "_BumpMap") : GetTextureOffset(material, "_NormalMap");
        var metallicGlossTexture = GetTexture(material, "_MetallicGlossMap");
        var metallicGlossTextureScale = GetTextureScale(material, "_MetallicGlossMap");
        var metallicGlossTextureOffset = GetTextureOffset(material, "_MetallicGlossMap");
        var occlusionTexture = GetTexture(material, "_OcclusionMap");
        var occlusionTextureScale = GetTextureScale(material, "_OcclusionMap");
        var occlusionTextureOffset = GetTextureOffset(material, "_OcclusionMap");
        var emissionTexture = GetTexture(material, "_EmissionMap");
        var emissionTextureScale = GetTextureScale(material, "_EmissionMap");
        var emissionTextureOffset = GetTextureOffset(material, "_EmissionMap");
        var color = GetColor(material, "_Color", GetColor(material, "_BaseColor", Color.white));
        var emissionColor = GetColor(material, "_EmissionColor", GetColor(material, "_EmissionCol", Color.black));
        var metallic = GetFloat(material, "_Metallic", 0f);
        var smoothness = GetFloat(material, "_Glossiness", GetFloat(material, "_Smoothness", 0.5f));
        var smoothnessScale = GetFloat(material, "_GlossMapScale", 1f);
        var bumpScale = GetFloat(material, "_BumpScale", 1f);
        var occlusionStrength = GetFloat(material, "_OcclusionStrength", 1f);
        var cutoff = GetFloat(material, "_Cutoff", 0.5f);
        var standardMode = Mathf.RoundToInt(GetFloat(material, "_Mode", color.a < 0.99f ? 2f : 0f));

        material.shader = FindUrpLitShader();

        SetFloatIfExists(material, "_WorkflowMode", 1f);
        SetTextureWithScaleOffset(material, "_BaseMap", mainTexture, mainTextureScale, mainTextureOffset);
        SetColorIfExists(material, "_BaseColor", color);
        SetTextureWithScaleOffset(material, "_BumpMap", normalTexture, normalTextureScale, normalTextureOffset);
        SetTextureWithScaleOffset(material, "_MetallicGlossMap", metallicGlossTexture, metallicGlossTextureScale, metallicGlossTextureOffset);
        SetTextureWithScaleOffset(material, "_OcclusionMap", occlusionTexture, occlusionTextureScale, occlusionTextureOffset);
        SetTextureWithScaleOffset(material, "_EmissionMap", emissionTexture, emissionTextureScale, emissionTextureOffset);
        SetColorIfExists(material, "_EmissionColor", emissionColor);
        SetFloatIfExists(material, "_Metallic", metallic);
        SetFloatIfExists(material, "_Smoothness", Mathf.Clamp01(smoothness * smoothnessScale));
        SetFloatIfExists(material, "_BumpScale", bumpScale);
        SetFloatIfExists(material, "_OcclusionStrength", occlusionStrength);
        SetFloatIfExists(material, "_Cutoff", cutoff);
        SetFloatIfExists(material, "_SpecularHighlights", 1f);
        SetFloatIfExists(material, "_EnvironmentReflections", 1f);

        ConfigureBlendMode(material, standardMode, color.a);
        ConfigureKeyword(material, "_NORMALMAP", normalTexture != null);
        ConfigureKeyword(material, "_METALLICSPECGLOSSMAP", metallicGlossTexture != null);
        ConfigureKeyword(material, "_OCCLUSIONMAP", occlusionTexture != null);
        ConfigureKeyword(material, "_EMISSION", emissionTexture != null || emissionColor.maxColorComponent > 0.001f);
    }

    [InitializeOnLoad]
    private static class OneShotConversionRunner
    {
        private const string MarkerAssetPath = "Assets/_AIgalgame/Editor/RunApartmentMaterialConversion.flag";
        private const string LockFilePath = "Library/ApartmentMaterialConversion.lock";
        private const string SessionKey = "AIgalgame.ApartmentMaterialConverter.OneShot";

        static OneShotConversionRunner()
        {
            if (!File.Exists(MarkerAssetPath) || SessionState.GetBool(SessionKey, false))
            {
                return;
            }

            SessionState.SetBool(SessionKey, true);
            EditorApplication.delayCall += RunIfMarked;
        }

        private static void RunIfMarked()
        {
            if (!File.Exists(MarkerAssetPath))
            {
                return;
            }

            FileStream lockStream = null;
            try
            {
                lockStream = new FileStream(LockFilePath, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                ConvertApartmentMaterialsToUrpLitBatch();
                DeleteMarker();
                Debug.Log("Apartment Material Converter: one-shot URP material conversion completed.");
            }
            catch (IOException)
            {
                Debug.Log("Apartment Material Converter: another editor instance is already running the one-shot conversion.");
            }
            catch (Exception error)
            {
                Debug.LogError($"Apartment Material Converter: one-shot conversion failed: {error}");
                SessionState.SetBool(SessionKey, false);
            }
            finally
            {
                if (lockStream != null)
                {
                    lockStream.Dispose();
                    if (File.Exists(LockFilePath))
                    {
                        File.Delete(LockFilePath);
                    }
                }
            }
        }

        private static void DeleteMarker()
        {
            if (AssetDatabase.DeleteAsset(MarkerAssetPath))
            {
                AssetDatabase.Refresh();
                return;
            }

            File.Delete(MarkerAssetPath);
            var metaPath = MarkerAssetPath + ".meta";
            if (File.Exists(metaPath))
            {
                File.Delete(metaPath);
            }

            AssetDatabase.Refresh();
        }
    }

    [InitializeOnLoad]
    private static class OneShotImportRefreshRunner
    {
        private const string MarkerAssetPath = "Assets/_AIgalgame/Editor/RunMaterialImportRefresh.flag";
        private const string SessionKey = "AIgalgame.MaterialImportRefresh.OneShot";

        static OneShotImportRefreshRunner()
        {
            if (!File.Exists(MarkerAssetPath) || SessionState.GetBool(SessionKey, false))
            {
                return;
            }

            SessionState.SetBool(SessionKey, true);
            EditorApplication.delayCall += RunIfMarked;
        }

        private static void RunIfMarked()
        {
            if (!File.Exists(MarkerAssetPath))
            {
                return;
            }

            try
            {
                var options = ImportAssetOptions.ForceUpdate
                    | ImportAssetOptions.ForceSynchronousImport
                    | ImportAssetOptions.ImportRecursive;

                AssetDatabase.ImportAsset("Assets/Brick Project Studio", options);
                AssetDatabase.ImportAsset("Assets/Item", options);
                AssetDatabase.ImportAsset("Assets/Character/keyi", options);
                AssetDatabase.ImportAsset("Assets/Character/wogua", options);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                DeleteMarker();
                Debug.Log("Apartment Material Converter: forced material import refresh completed.");
            }
            catch (Exception error)
            {
                Debug.LogError($"Apartment Material Converter: forced material import refresh failed: {error}");
                SessionState.SetBool(SessionKey, false);
            }
        }

        private static void DeleteMarker()
        {
            if (AssetDatabase.DeleteAsset(MarkerAssetPath))
            {
                AssetDatabase.Refresh();
                return;
            }

            File.Delete(MarkerAssetPath);
            var metaPath = MarkerAssetPath + ".meta";
            if (File.Exists(metaPath))
            {
                File.Delete(metaPath);
            }

            AssetDatabase.Refresh();
        }
    }

    private static Texture GetTexture(Material material, string property)
    {
        return material.HasProperty(property) ? material.GetTexture(property) : null;
    }

    private static Vector2 GetTextureScale(Material material, string property)
    {
        return material.HasProperty(property) ? material.GetTextureScale(property) : Vector2.one;
    }

    private static Vector2 GetTextureOffset(Material material, string property)
    {
        return material.HasProperty(property) ? material.GetTextureOffset(property) : Vector2.zero;
    }

    private static Color GetColor(Material material, string property, Color fallback)
    {
        return material.HasProperty(property) ? material.GetColor(property) : fallback;
    }

    private static float GetFloat(Material material, string property, float fallback)
    {
        return material.HasProperty(property) ? material.GetFloat(property) : fallback;
    }

    private static void SetTextureWithScaleOffset(Material material, string property, Texture texture, Vector2 scale, Vector2 offset)
    {
        if (!material.HasProperty(property))
        {
            return;
        }

        material.SetTexture(property, texture);
        material.SetTextureScale(property, scale);
        material.SetTextureOffset(property, offset);
    }

    private static void SetColorIfExists(Material material, string property, Color value)
    {
        if (material.HasProperty(property))
        {
            material.SetColor(property, value);
        }
    }

    private static void SetFloatIfExists(Material material, string property, float value)
    {
        if (material.HasProperty(property))
        {
            material.SetFloat(property, value);
        }
    }

    private static void ConfigureBlendMode(Material material, int standardMode, float alpha)
    {
        var isCutout = standardMode == 1;
        var isFade = standardMode == 2;
        var isTransparent = standardMode == 3 || isFade || alpha < 0.99f;

        if (isTransparent)
        {
            SetFloatIfExists(material, "_Surface", 1f);
            SetFloatIfExists(material, "_AlphaClip", 0f);
            SetFloatIfExists(material, "_ZWrite", 0f);
            material.SetOverrideTag("RenderType", "Transparent");
            material.renderQueue = (int)RenderQueue.Transparent;
            ConfigureKeyword(material, "_SURFACE_TYPE_TRANSPARENT", true);
            ConfigureKeyword(material, "_ALPHATEST_ON", false);

            if (standardMode == 3)
            {
                SetFloatIfExists(material, "_Blend", 1f);
                SetFloatIfExists(material, "_SrcBlend", (float)BlendMode.One);
                SetFloatIfExists(material, "_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
                ConfigureKeyword(material, "_ALPHAPREMULTIPLY_ON", true);
            }
            else
            {
                SetFloatIfExists(material, "_Blend", 0f);
                SetFloatIfExists(material, "_SrcBlend", (float)BlendMode.SrcAlpha);
                SetFloatIfExists(material, "_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
                ConfigureKeyword(material, "_ALPHAPREMULTIPLY_ON", false);
            }

            return;
        }

        SetFloatIfExists(material, "_Surface", 0f);
        SetFloatIfExists(material, "_Blend", 0f);
        SetFloatIfExists(material, "_SrcBlend", (float)BlendMode.One);
        SetFloatIfExists(material, "_DstBlend", (float)BlendMode.Zero);
        SetFloatIfExists(material, "_ZWrite", 1f);
        material.renderQueue = isCutout ? (int)RenderQueue.AlphaTest : -1;
        material.SetOverrideTag("RenderType", isCutout ? "TransparentCutout" : "Opaque");
        ConfigureKeyword(material, "_SURFACE_TYPE_TRANSPARENT", false);
        ConfigureKeyword(material, "_ALPHAPREMULTIPLY_ON", false);
        ConfigureKeyword(material, "_ALPHATEST_ON", isCutout);
        SetFloatIfExists(material, "_AlphaClip", isCutout ? 1f : 0f);
    }

    private static void ConfigureKeyword(Material material, string keyword, bool enabled)
    {
        if (enabled)
        {
            material.EnableKeyword(keyword);
        }
        else
        {
            material.DisableKeyword(keyword);
        }
    }
}
