#if UNITY_EDITOR
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace AIgalgame.EditorTools
{
    public static class AIGalgameRemoveLegacySceneUiTool
    {
        private const string Scene01Path = "Assets/Brick Project Studio/Apartment Kit/Scenes/Scene_01.unity";

        private static readonly string[] LegacyUiRootNames =
        {
            "AIgalgame_RelaxRoomSceneUI",
            "EventSystem",
            "AIgalgame_MotionTestUI",
            "AIgalgame_MotionTestCanvas",
            "AIgalgame_ChatdollDemoCanvas",
        };

        [MenuItem("Tools/AIgalgame/RN/Remove Legacy Scene UI From Scene_01")]
        public static void RemoveLegacySceneUiMenu()
        {
            if (!RemoveLegacySceneUiFromScene01(saveScene: true))
            {
                Debug.LogWarning("No legacy Unity UI roots found in Scene_01.");
            }
        }

        public static bool RemoveLegacySceneUiFromScene01(bool saveScene)
        {
            if (!System.IO.File.Exists(Scene01Path))
            {
                return false;
            }

            var scene = EditorSceneManager.OpenScene(Scene01Path, OpenSceneMode.Single);
            var targets = CollectLegacyUiRoots(scene);
            if (targets.Count == 0)
            {
                return false;
            }

            foreach (var target in targets)
            {
                if (target == null)
                {
                    continue;
                }

                var name = target.name;
                Object.DestroyImmediate(target);
                Debug.Log($"Removed legacy Unity UI root from Scene_01: {name}");
            }

            if (saveScene)
            {
                EditorSceneManager.MarkSceneDirty(scene);
                EditorSceneManager.SaveScene(scene);
            }

            return true;
        }

        private static List<GameObject> CollectLegacyUiRoots(Scene scene)
        {
            var results = new List<GameObject>();
            var seen = new HashSet<int>();

            foreach (var rootName in LegacyUiRootNames)
            {
                var match = FindSceneObjectByName(scene, rootName);
                if (match == null || !seen.Add(match.GetInstanceID()))
                {
                    continue;
                }

                results.Add(match);
            }

            return results;
        }

        private static GameObject FindSceneObjectByName(Scene scene, string objectName)
        {
            if (!scene.IsValid())
            {
                return null;
            }

            foreach (var root in scene.GetRootGameObjects())
            {
                if (root.name == objectName)
                {
                    return root;
                }

                var nested = FindChildByName(root.transform, objectName);
                if (nested != null)
                {
                    return nested;
                }
            }

            return null;
        }

        private static GameObject FindChildByName(Transform parent, string objectName)
        {
            for (var i = 0; i < parent.childCount; i++)
            {
                var child = parent.GetChild(i);
                if (child.name == objectName)
                {
                    return child.gameObject;
                }

                var nested = FindChildByName(child, objectName);
                if (nested != null)
                {
                    return nested;
                }
            }

            return null;
        }
    }
}
#endif
