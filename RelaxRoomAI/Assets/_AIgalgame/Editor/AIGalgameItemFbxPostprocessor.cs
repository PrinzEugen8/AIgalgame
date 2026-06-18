using UnityEditor;

public sealed class AIGalgameItemFbxPostprocessor : AssetPostprocessor
{
    private const string ItemFolder = "assets/item/";

    private void OnPreprocessModel()
    {
        var normalizedPath = assetPath.Replace("\\", "/").ToLowerInvariant();
        if (!normalizedPath.StartsWith(ItemFolder) || !normalizedPath.EndsWith(".fbx"))
        {
            return;
        }

        var importer = assetImporter as ModelImporter;
        if (importer == null)
        {
            return;
        }

        importer.importAnimation = false;
        importer.importCameras = false;
        importer.importLights = false;
        importer.importNormals = ModelImporterNormals.Calculate;
        importer.normalCalculationMode = ModelImporterNormalCalculationMode.AreaAndAngleWeighted;
        importer.normalSmoothingAngle = 60f;
        importer.importTangents = ModelImporterTangents.CalculateMikk;
        importer.weldVertices = true;
        importer.optimizeMeshPolygons = true;
        importer.optimizeMeshVertices = true;
    }

    [MenuItem("AIgalgame/RelaxRoom/Reimport Item FBX Assets")]
    public static void ReimportItemFbxAssets()
    {
        var guids = AssetDatabase.FindAssets("t:Model", new[] { "Assets/Item" });
        foreach (var guid in guids)
        {
            var path = AssetDatabase.GUIDToAssetPath(guid);
            if (path.EndsWith(".fbx", System.StringComparison.OrdinalIgnoreCase))
            {
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            }
        }

        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
    }
}
