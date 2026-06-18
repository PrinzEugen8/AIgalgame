using System;
using AIgalgame.Motion;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AIgalgame.EditorTools
{
    [CustomEditor(typeof(AIGalgameRelaxRoomPhoneAttachmentController))]
    public sealed class AIGalgameRelaxRoomPhoneAttachmentControllerEditor : Editor
    {
        private const string LiveTunePrefsKey = "AIgalgame.RelaxRoom.PhoneGrip.LiveTune";

        private SerializedProperty animatorProperty;
        private SerializedProperty phonePrefabProperty;
        private SerializedProperty phoneInstanceProperty;
        private SerializedProperty previewPhoneGripProperty;
        private bool showPreview = true;

        [Serializable]
        private sealed class LiveTuneSnapshot
        {
            public string targetGlobalId;
            public int gripIndex;
            public Vector3 localPosition;
            public Vector3 localEulerAngles;
            public Vector3 localScale;
        }

        [Serializable]
        private sealed class LiveTuneSnapshotList
        {
            public LiveTuneSnapshot[] snapshots = Array.Empty<LiveTuneSnapshot>();
        }

        [InitializeOnLoadMethod]
        private static void RegisterPlayModeRestore()
        {
            EditorApplication.playModeStateChanged -= ApplyLiveTuneSnapshotsAfterPlayMode;
            EditorApplication.playModeStateChanged += ApplyLiveTuneSnapshotsAfterPlayMode;
        }

        private void OnEnable()
        {
            animatorProperty = serializedObject.FindProperty("animator");
            phonePrefabProperty = serializedObject.FindProperty("phonePrefab");
            phoneInstanceProperty = serializedObject.FindProperty("phoneInstance");
            previewPhoneGripProperty = serializedObject.FindProperty("previewPhoneGrip");
            SceneView.duringSceneGui += DrawScenePhoneHandles;
        }

        private void OnDisable()
        {
            SceneView.duringSceneGui -= DrawScenePhoneHandles;
        }

        public override void OnInspectorGUI()
        {
            serializedObject.Update();
            DrawDefaultInspector();

            EditorGUILayout.Space(10f);
            EditorGUILayout.LabelField("Phone Prop On Character", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox("The phone is a real child object on the character. No runtime creation. Select Preview Phone Grip, then use the Scene view Move/Rotate/Scale tools while the real action is playing.", MessageType.Info);

            using (new EditorGUILayout.HorizontalScope())
            {
                using (new EditorGUI.DisabledScope(Application.isPlaying))
                {
                    if (GUILayout.Button("Create/Assign Phone Child"))
                    {
                        CreateOrAssignPhoneChild();
                        SceneView.RepaintAll();
                    }
                }

                if (GUILayout.Button("Frame Phone"))
                {
                    FramePhoneObject();
                }
            }

            using (new EditorGUILayout.HorizontalScope())
            {
                var showLabel = Application.isPlaying
                    ? (showPreview ? "Refresh Live Phone" : "Show Live Phone")
                    : (showPreview ? "Refresh Phone" : "Show Phone");
                if (GUILayout.Button(showLabel))
                {
                    showPreview = true;
                    ShowRuntimePhoneIfNeeded();
                    UpdatePhoneObject();
                    SceneView.RepaintAll();
                }

                if (GUILayout.Button(Application.isPlaying ? "Hide Live Phone" : "Hide Phone"))
                {
                    showPreview = false;
                    HideRuntimePhoneIfNeeded();
                    PlacePhoneOnTableForEditing();
                    SceneView.RepaintAll();
                }
            }

            serializedObject.ApplyModifiedProperties();
        }

        private void DrawScenePhoneHandles(SceneView sceneView)
        {
            if (!showPreview || target == null || Selection.activeObject != target)
            {
                return;
            }

            serializedObject.Update();
            var preset = GetSelectedPresetProperty();
            if (preset == null)
            {
                return;
            }

            var parent = GetPhoneParent(preset);
            if (parent == null)
            {
                return;
            }

            var localPosition = preset.FindPropertyRelative("localPosition");
            var localEulerAngles = preset.FindPropertyRelative("localEulerAngles");
            var localScale = preset.FindPropertyRelative("localScale");
            if (localPosition == null || localEulerAngles == null || localScale == null)
            {
                return;
            }

            var worldPosition = parent.TransformPoint(localPosition.vector3Value);
            var worldRotation = parent.rotation * Quaternion.Euler(localEulerAngles.vector3Value);
            var scale = localScale.vector3Value;
            var handleSize = HandleUtility.GetHandleSize(worldPosition);

            ShowRuntimePhoneIfNeeded();
            UpdatePhoneObject(parent, localPosition.vector3Value, localEulerAngles.vector3Value, scale);

            using (new Handles.DrawingScope(Color.cyan))
            {
                Handles.Label(worldPosition + Vector3.up * handleSize * 0.16f, $"Phone Grip: {GetSelectedGripLabel()}");

                EditorGUI.BeginChangeCheck();
                switch (Tools.current)
                {
                    case Tool.Rotate:
                        worldRotation = Handles.RotationHandle(worldRotation, worldPosition);
                        break;
                    case Tool.Scale:
                    case Tool.Rect:
                        scale = Handles.ScaleHandle(scale, worldPosition, worldRotation, handleSize);
                        break;
                    case Tool.Move:
                    case Tool.Transform:
                    default:
                        worldPosition = Handles.PositionHandle(worldPosition, worldRotation);
                        break;
                }

                if (EditorGUI.EndChangeCheck())
                {
                    Undo.RecordObject(target, "Adjust RelaxRoom Phone Grip");
                    localPosition.vector3Value = parent.InverseTransformPoint(worldPosition);
                    localEulerAngles.vector3Value = NormalizeEuler((Quaternion.Inverse(parent.rotation) * worldRotation).eulerAngles);
                    localScale.vector3Value = SanitizeScale(scale);
                    serializedObject.ApplyModifiedProperties();
                    EditorUtility.SetDirty(target);
                    SaveLiveTuneSnapshotIfNeeded(localPosition.vector3Value, localEulerAngles.vector3Value, localScale.vector3Value);
                    UpdatePhoneObject(parent, localPosition.vector3Value, localEulerAngles.vector3Value, localScale.vector3Value);
                }
            }
        }

        private SerializedProperty GetSelectedPresetProperty()
        {
            return serializedObject.FindProperty(GetPresetPropertyName(previewPhoneGripProperty.enumValueIndex));
        }

        private static string GetPresetPropertyName(int gripIndex)
        {
            return gripIndex switch
            {
                1 => "replyTextingPhoneAttachment",
                2 => "replyWaitingPhoneAttachment",
                3 => "replyDoubleTypingPhoneAttachment",
                4 => "idleTabletPhoneAttachment",
                5 => "idleTextingPhoneAttachment",
                _ => "defaultPhoneAttachment",
            };
        }

        private string GetSelectedGripLabel()
        {
            if (previewPhoneGripProperty == null ||
                previewPhoneGripProperty.enumDisplayNames == null ||
                previewPhoneGripProperty.enumValueIndex < 0 ||
                previewPhoneGripProperty.enumValueIndex >= previewPhoneGripProperty.enumDisplayNames.Length)
            {
                return "Default";
            }

            return previewPhoneGripProperty.enumDisplayNames[previewPhoneGripProperty.enumValueIndex];
        }

        private Transform GetPhoneParent(SerializedProperty preset)
        {
            var phoneController = target as AIGalgameRelaxRoomPhoneAttachmentController;
            if (phoneController == null)
            {
                return null;
            }

            var animator = animatorProperty.objectReferenceValue as Animator;
            if (animator == null)
            {
                animator = phoneController.GetComponent<Animator>() ??
                    phoneController.GetComponentInChildren<Animator>() ??
                    phoneController.GetComponentInParent<Animator>();
            }

            if (animator != null && animator.avatar != null && animator.avatar.isHuman)
            {
                var handBone = GetPresetHandBone(preset);
                var bone = animator.GetBoneTransform(handBone);
                if (bone != null)
                {
                    var socketName = handBone == HumanBodyBones.LeftHand
                        ? "LeftHand_Socket"
                        : "RightHand_Socket";
                    var socket = FindChildRecursive(bone, socketName);
                    return socket != null ? socket : bone;
                }
            }

            return phoneController.transform;
        }

        private static Transform FindChildRecursive(Transform parent, string childName)
        {
            if (parent == null || string.IsNullOrWhiteSpace(childName))
            {
                return null;
            }

            for (var i = 0; i < parent.childCount; i++)
            {
                var child = parent.GetChild(i);
                if (child != null && string.Equals(child.name, childName, StringComparison.Ordinal))
                {
                    return child;
                }

                var nested = FindChildRecursive(child, childName);
                if (nested != null)
                {
                    return nested;
                }
            }

            return null;
        }

        private static HumanBodyBones GetPresetHandBone(SerializedProperty preset)
        {
            var handBone = preset.FindPropertyRelative("handBone");
            if (handBone == null || handBone.enumNames == null || handBone.enumNames.Length == 0)
            {
                return HumanBodyBones.RightHand;
            }

            var index = Mathf.Clamp(handBone.enumValueIndex, 0, handBone.enumNames.Length - 1);
            return Enum.TryParse(handBone.enumNames[index], out HumanBodyBones bone)
                ? bone
                : HumanBodyBones.RightHand;
        }

        private void UpdatePhoneObject()
        {
            serializedObject.Update();
            var preset = GetSelectedPresetProperty();
            var parent = preset != null ? GetPhoneParent(preset) : null;
            if (preset == null || parent == null)
            {
                return;
            }

            UpdatePhoneObject(
                parent,
                preset.FindPropertyRelative("localPosition").vector3Value,
                preset.FindPropertyRelative("localEulerAngles").vector3Value,
                preset.FindPropertyRelative("localScale").vector3Value);
        }

        private void UpdatePhoneObject(Transform parent, Vector3 localPosition, Vector3 localEulerAngles, Vector3 localScale)
        {
            var phone = GetAssignedPhoneObject();
            if (phone == null)
            {
                return;
            }

            phone.SetActive(true);
            phone.transform.SetParent(parent, false);
            if (IsHandSocket(parent))
            {
                phone.transform.localPosition = Vector3.zero;
                phone.transform.localRotation = Quaternion.identity;
                phone.transform.localScale = Vector3.one;
                return;
            }

            phone.transform.localPosition = localPosition;
            phone.transform.localRotation = Quaternion.Euler(localEulerAngles);
            phone.transform.localScale = SanitizeScale(localScale);
        }

        private static bool IsHandSocket(Transform target)
        {
            return target != null &&
                (string.Equals(target.name, "RightHand_Socket", StringComparison.Ordinal) ||
                    string.Equals(target.name, "LeftHand_Socket", StringComparison.Ordinal));
        }

        private GameObject GetAssignedPhoneObject()
        {
            var assigned = phoneInstanceProperty.objectReferenceValue as Transform;
            return assigned != null ? assigned.gameObject : null;
        }

        private void ShowRuntimePhoneIfNeeded()
        {
            if (!Application.isPlaying || target is not AIGalgameRelaxRoomPhoneAttachmentController phoneController)
            {
                return;
            }

            phoneController.EditorShowPhoneGrip(previewPhoneGripProperty.enumValueIndex);
        }

        private void HideRuntimePhoneIfNeeded()
        {
            if (!Application.isPlaying || target is not AIGalgameRelaxRoomPhoneAttachmentController phoneController)
            {
                return;
            }

            phoneController.EditorHidePhoneGrip();
        }

        private void PlacePhoneOnTableForEditing()
        {
            if (target is not AIGalgameRelaxRoomPhoneAttachmentController phoneController)
            {
                return;
            }

            phoneController.PlaceOnTable(true);
            var phone = GetAssignedPhoneObject();
            if (phone != null)
            {
                phone.SetActive(true);
                EditorUtility.SetDirty(phone);
                EditorSceneManager.MarkSceneDirty(phone.scene);
            }

            EditorUtility.SetDirty(phoneController);
        }

        private void CreateOrAssignPhoneChild()
        {
            if (Application.isPlaying)
            {
                return;
            }

            serializedObject.Update();
            var prefab = phonePrefabProperty.objectReferenceValue as GameObject;
            if (prefab == null)
            {
                EditorUtility.DisplayDialog("RelaxRoom Phone", "Phone Prefab is empty. Assign Assets/Item/手机.fbx first.", "OK");
                return;
            }

            var preset = GetSelectedPresetProperty();
            var parent = preset != null ? GetPhoneParent(preset) : null;
            if (parent == null)
            {
                EditorUtility.DisplayDialog("RelaxRoom Phone", "Could not find the target hand bone or character transform.", "OK");
                return;
            }

            var existing = phoneInstanceProperty.objectReferenceValue as Transform;
            GameObject phoneObject;
            if (existing != null)
            {
                phoneObject = existing.gameObject;
                Undo.SetTransformParent(existing, parent, "Attach RelaxRoom Phone Child");
            }
            else
            {
                phoneObject = PrefabUtility.InstantiatePrefab(prefab, parent) as GameObject;
                if (phoneObject == null)
                {
                    phoneObject = Instantiate(prefab, parent, false);
                }

                phoneObject.name = "RelaxRoom Phone Prop";
                Undo.RegisterCreatedObjectUndo(phoneObject, "Create RelaxRoom Phone Child");
                phoneInstanceProperty.objectReferenceValue = phoneObject.transform;
            }

            phoneObject.SetActive(true);
            serializedObject.ApplyModifiedProperties();
            PrefabUtility.RecordPrefabInstancePropertyModifications(target);
            EditorUtility.SetDirty(target);
            UpdatePhoneObject();
            EditorSceneManager.MarkSceneDirty(phoneObject.scene);
        }

        private void FramePhoneObject()
        {
            UpdatePhoneObject();
            var phone = GetAssignedPhoneObject();
            if (phone == null || SceneView.lastActiveSceneView == null)
            {
                return;
            }

            var bounds = new Bounds(phone.transform.position, Vector3.one * 0.12f);
            var renderers = phone.GetComponentsInChildren<Renderer>();
            foreach (var renderer in renderers)
            {
                bounds.Encapsulate(renderer.bounds);
            }

            SceneView.lastActiveSceneView.Frame(bounds, false);
        }

        private static Vector3 NormalizeEuler(Vector3 euler)
        {
            return new Vector3(NormalizeAngle(euler.x), NormalizeAngle(euler.y), NormalizeAngle(euler.z));
        }

        private static float NormalizeAngle(float angle)
        {
            angle %= 360f;
            if (angle > 180f)
            {
                angle -= 360f;
            }

            if (angle < -180f)
            {
                angle += 360f;
            }

            return angle;
        }

        private static Vector3 SanitizeScale(Vector3 scale)
        {
            if (scale == Vector3.zero)
            {
                return Vector3.one;
            }

            return scale;
        }

        private void SaveLiveTuneSnapshotIfNeeded(Vector3 localPosition, Vector3 localEulerAngles, Vector3 localScale)
        {
            if (!Application.isPlaying || target == null)
            {
                return;
            }

            var snapshot = new LiveTuneSnapshot
            {
                targetGlobalId = GlobalObjectId.GetGlobalObjectIdSlow(target).ToString(),
                gripIndex = previewPhoneGripProperty.enumValueIndex,
                localPosition = localPosition,
                localEulerAngles = localEulerAngles,
                localScale = localScale,
            };

            var list = LoadLiveTuneSnapshots();
            var replaced = false;
            for (var i = 0; i < list.snapshots.Length; i++)
            {
                var existing = list.snapshots[i];
                if (existing.targetGlobalId == snapshot.targetGlobalId &&
                    existing.gripIndex == snapshot.gripIndex)
                {
                    list.snapshots[i] = snapshot;
                    replaced = true;
                    break;
                }
            }

            if (!replaced)
            {
                Array.Resize(ref list.snapshots, list.snapshots.Length + 1);
                list.snapshots[^1] = snapshot;
            }

            EditorPrefs.SetString(LiveTunePrefsKey, JsonUtility.ToJson(list));
        }

        private static LiveTuneSnapshotList LoadLiveTuneSnapshots()
        {
            var payload = EditorPrefs.GetString(LiveTunePrefsKey, string.Empty);
            if (string.IsNullOrWhiteSpace(payload))
            {
                return new LiveTuneSnapshotList();
            }

            try
            {
                return JsonUtility.FromJson<LiveTuneSnapshotList>(payload) ?? new LiveTuneSnapshotList();
            }
            catch
            {
                return new LiveTuneSnapshotList();
            }
        }

        private static void ApplyLiveTuneSnapshotsAfterPlayMode(PlayModeStateChange state)
        {
            if (state != PlayModeStateChange.EnteredEditMode)
            {
                return;
            }

            var list = LoadLiveTuneSnapshots();
            if (list.snapshots == null || list.snapshots.Length == 0)
            {
                return;
            }

            var phoneControllers = Resources.FindObjectsOfTypeAll<AIGalgameRelaxRoomPhoneAttachmentController>();
            foreach (var snapshot in list.snapshots)
            {
                foreach (var phoneController in phoneControllers)
                {
                    if (phoneController == null ||
                        string.IsNullOrEmpty(snapshot.targetGlobalId) ||
                        GlobalObjectId.GetGlobalObjectIdSlow(phoneController).ToString() != snapshot.targetGlobalId)
                    {
                        continue;
                    }

                    ApplySnapshot(phoneController, snapshot);
                    break;
                }
            }

            EditorPrefs.DeleteKey(LiveTunePrefsKey);
        }

        private static void ApplySnapshot(AIGalgameRelaxRoomPhoneAttachmentController phoneController, LiveTuneSnapshot snapshot)
        {
            var serializedController = new SerializedObject(phoneController);
            var preset = serializedController.FindProperty(GetPresetPropertyName(snapshot.gripIndex));
            if (preset == null)
            {
                return;
            }

            preset.FindPropertyRelative("localPosition").vector3Value = snapshot.localPosition;
            preset.FindPropertyRelative("localEulerAngles").vector3Value = snapshot.localEulerAngles;
            preset.FindPropertyRelative("localScale").vector3Value = SanitizeScale(snapshot.localScale);
            serializedController.ApplyModifiedPropertiesWithoutUndo();
            EditorUtility.SetDirty(phoneController);
            PrefabUtility.RecordPrefabInstancePropertyModifications(phoneController);
            if (phoneController.gameObject.scene.IsValid())
            {
                EditorSceneManager.MarkSceneDirty(phoneController.gameObject.scene);
            }
        }
    }
}
