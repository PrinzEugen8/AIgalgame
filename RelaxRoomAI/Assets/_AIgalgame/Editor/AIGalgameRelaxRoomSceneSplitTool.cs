#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UniVRM10;

namespace AIgalgame.EditorTools
{
    public static class AIGalgameRelaxRoomSceneSplitTool
    {
        private const string SourceScenePath = "Assets/Brick Project Studio/Apartment Kit/Scenes/Scene_01.unity";
        private const string BootScenePath = "Assets/_AIgalgame/Scenes/RelaxRoomBoot.unity";
        private const string ApartmentScenePath = "Assets/_AIgalgame/Scenes/Scene_01_Apartment.unity";
        private const string ScenesFolder = "Assets/_AIgalgame/Scenes";

        private static readonly string[] RnUiRootNames =
        {
            "AIgalgame_RelaxRoomSceneUI",
            "EventSystem",
        };

        [MenuItem("Tools/AIgalgame/RN/Split Scenes For Mobile")]
        public static void SplitScenesForMobileMenu()
        {
            Debug.LogWarning(
                "Scene split is deprecated. Mobile builds now use Scene_01 directly. This menu is kept for reference only.");
            EnsureMobileScenes(forceRefresh: true);
        }

        public static void EnsureMobileScenes(bool forceRefresh = false)
        {
            if (!File.Exists(SourceScenePath))
            {
                Debug.LogError($"Source scene not found: {SourceScenePath}");
                return;
            }

            Directory.CreateDirectory(Path.GetFullPath(ScenesFolder));

            if (forceRefresh || !File.Exists(BootScenePath) || !File.Exists(ApartmentScenePath))
            {
                BuildApartmentScene();
                BuildBootScene();
            }

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Debug.Log("RelaxRoom mobile scenes are ready (deprecated split workflow).");
        }

        [MenuItem("Tools/AIgalgame/RN/Bake Apartment Occlusion")]
        public static void BakeApartmentOcclusionMenu()
        {
            BakeApartmentOcclusion();
        }

        public static void BakeApartmentOcclusion()
        {
            EnsureMobileScenes();

            var scene = EditorSceneManager.OpenScene(ApartmentScenePath, OpenSceneMode.Single);
            MarkStaticRootsForOcclusion(scene);
            StaticOcclusionCulling.Compute();

            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene);
            Debug.Log($"Occlusion culling baked for {ApartmentScenePath}");
        }

        private static void BuildApartmentScene()
        {
            var scene = EditorSceneManager.OpenScene(SourceScenePath, OpenSceneMode.Single);
            RemoveRnUiRoots();
            RemoveCharacterRoots();
            RemoveBootOnlyRoots();
            RemoveBaseScenePresentationChildren();
            EditorSceneManager.SaveScene(scene, ApartmentScenePath);
        }

        private static void BuildBootScene()
        {
            var scene = EditorSceneManager.OpenScene(SourceScenePath, OpenSceneMode.Single);
            RemoveRnUiRoots();
            RemoveApartmentOnlyRoots();
            EditorSceneManager.SaveScene(scene, BootScenePath);
        }

        private static void RemoveRnUiRoots()
        {
            foreach (var rootName in RnUiRootNames)
            {
                var root = GameObject.Find(rootName);
                if (root != null)
                {
                    Object.DestroyImmediate(root);
                }
            }

            foreach (var canvas in Object.FindObjectsByType<Canvas>(FindObjectsInactive.Include, FindObjectsSortMode.None))
            {
                Object.DestroyImmediate(canvas.gameObject);
            }
        }

        private static void RemoveCharacterRoots()
        {
            foreach (var vrm in Object.FindObjectsByType<Vrm10Instance>(FindObjectsInactive.Include, FindObjectsSortMode.None))
            {
                Object.DestroyImmediate(vrm.gameObject);
            }
        }

        private static void RemoveBootOnlyRoots()
        {
            var phoneSlot = GameObject.Find("RelaxRoom Phone TableSlot");
            if (phoneSlot != null)
            {
                Object.DestroyImmediate(phoneSlot);
            }
        }

        private static void RemoveApartmentOnlyRoots()
        {
            var ground = GameObject.Find("Ground");
            if (ground != null)
            {
                Object.DestroyImmediate(ground);
            }

            var structure = GameObject.Find("Structure_02");
            if (structure != null)
            {
                Object.DestroyImmediate(structure);
            }

            var baseScene = GameObject.Find("Base Scene");
            if (baseScene != null)
            {
                var groundChild = baseScene.transform.Find("Ground");
                if (groundChild != null)
                {
                    Object.DestroyImmediate(groundChild.gameObject);
                }
            }
        }

        private static void RemoveBaseScenePresentationChildren()
        {
            var baseScene = GameObject.Find("Base Scene");
            if (baseScene == null)
            {
                return;
            }

            var keepNames = new HashSet<string> { "Ground" };
            var children = baseScene.transform.Cast<Transform>().ToList();
            foreach (var child in children)
            {
                if (!keepNames.Contains(child.name))
                {
                    Object.DestroyImmediate(child.gameObject);
                }
            }

            var baseTransform = baseScene.transform;
            var ground = baseTransform.Find("Ground");
            if (ground != null)
            {
                ground.SetParent(null, true);
            }

            Object.DestroyImmediate(baseScene);
        }

        private static void MarkStaticRootsForOcclusion(Scene scene)
        {
            var roots = scene.GetRootGameObjects();
            foreach (var root in roots)
            {
                MarkStaticRecursive(root.transform);
            }
        }

        private static void MarkStaticRecursive(Transform transform)
        {
            var flags = GameObjectUtility.GetStaticEditorFlags(transform.gameObject);
            flags |= StaticEditorFlags.OccluderStatic | StaticEditorFlags.OccludeeStatic;
            GameObjectUtility.SetStaticEditorFlags(transform.gameObject, flags);

            for (var i = 0; i < transform.childCount; i++)
            {
                MarkStaticRecursive(transform.GetChild(i));
            }
        }

        private static void UpdateBuildSettings()
        {
            var scenes = new[]
            {
                new EditorBuildSettingsScene(BootScenePath, false),
                new EditorBuildSettingsScene(ApartmentScenePath, false),
                new EditorBuildSettingsScene(SourceScenePath, true),
            };
            EditorBuildSettings.scenes = scenes;
        }
    }
}
#endif
