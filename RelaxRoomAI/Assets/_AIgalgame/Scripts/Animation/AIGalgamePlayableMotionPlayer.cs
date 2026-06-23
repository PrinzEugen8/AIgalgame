using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

#if UNITY_EDITOR
using UnityEditor;
#endif

namespace AIgalgame.Motion
{
    [Serializable]
    public sealed class MotionClipEntry
    {
        public string displayName;
        public AnimationClip clip;
    }

    [Serializable]
    public sealed class MotionAdjustmentData
    {
        public string clipKey;
        public string displayName;
        public Vector3 rootPositionOffset;
        public Vector3 rootRotationOffset;
        public float rootScale = 1f;
        public float playbackSpeed = 1f;
        public float blendSeconds = 0.35f;
        public float leftHandIkWeight;
        public float rightHandIkWeight;
        public float leftFootIkWeight;
        public float rightFootIkWeight;
        public float lookAtWeight;
        public bool hasLeftHandTarget;
        public bool hasRightHandTarget;
        public bool hasLeftFootTarget;
        public bool hasRightFootTarget;
        public bool hasLookAtTarget;
        public bool hasLeftHandPole;
        public bool hasRightHandPole;
        public bool hasLeftFootPole;
        public bool hasRightFootPole;
        public Vector3 leftHandTargetLocalPosition;
        public Vector3 rightHandTargetLocalPosition;
        public Vector3 leftFootTargetLocalPosition;
        public Vector3 rightFootTargetLocalPosition;
        public Vector3 lookAtTargetLocalPosition;
        public Vector3 leftHandTargetLocalEuler;
        public Vector3 rightHandTargetLocalEuler;
        public Vector3 leftFootTargetLocalEuler;
        public Vector3 rightFootTargetLocalEuler;
        public Vector3 leftHandPoleLocalPosition;
        public Vector3 rightHandPoleLocalPosition;
        public Vector3 leftFootPoleLocalPosition;
        public Vector3 rightFootPoleLocalPosition;

        public static MotionAdjustmentData CreateDefault(string key, string name, float blend)
        {
            return new MotionAdjustmentData
            {
                clipKey = key,
                displayName = name,
                rootPositionOffset = Vector3.zero,
                rootRotationOffset = Vector3.zero,
                rootScale = 1f,
                playbackSpeed = 1f,
                blendSeconds = Mathf.Clamp(blend, 0f, 2f)
            };
        }

        public MotionAdjustmentData Clone()
        {
            return new MotionAdjustmentData
            {
                clipKey = clipKey,
                displayName = displayName,
                rootPositionOffset = rootPositionOffset,
                rootRotationOffset = rootRotationOffset,
                rootScale = rootScale,
                playbackSpeed = playbackSpeed,
                blendSeconds = blendSeconds,
                leftHandIkWeight = leftHandIkWeight,
                rightHandIkWeight = rightHandIkWeight,
                leftFootIkWeight = leftFootIkWeight,
                rightFootIkWeight = rightFootIkWeight,
                lookAtWeight = lookAtWeight,
                hasLeftHandTarget = hasLeftHandTarget,
                hasRightHandTarget = hasRightHandTarget,
                hasLeftFootTarget = hasLeftFootTarget,
                hasRightFootTarget = hasRightFootTarget,
                hasLookAtTarget = hasLookAtTarget,
                hasLeftHandPole = hasLeftHandPole,
                hasRightHandPole = hasRightHandPole,
                hasLeftFootPole = hasLeftFootPole,
                hasRightFootPole = hasRightFootPole,
                leftHandTargetLocalPosition = leftHandTargetLocalPosition,
                rightHandTargetLocalPosition = rightHandTargetLocalPosition,
                leftFootTargetLocalPosition = leftFootTargetLocalPosition,
                rightFootTargetLocalPosition = rightFootTargetLocalPosition,
                lookAtTargetLocalPosition = lookAtTargetLocalPosition,
                leftHandTargetLocalEuler = leftHandTargetLocalEuler,
                rightHandTargetLocalEuler = rightHandTargetLocalEuler,
                leftFootTargetLocalEuler = leftFootTargetLocalEuler,
                rightFootTargetLocalEuler = rightFootTargetLocalEuler,
                leftHandPoleLocalPosition = leftHandPoleLocalPosition,
                rightHandPoleLocalPosition = rightHandPoleLocalPosition,
                leftFootPoleLocalPosition = leftFootPoleLocalPosition,
                rightFootPoleLocalPosition = rightFootPoleLocalPosition
            };
        }

        public void Sanitize(float fallbackBlend)
        {
            rootScale = Mathf.Clamp(rootScale <= 0f ? 1f : rootScale, 0.5f, 1.5f);
            playbackSpeed = Mathf.Clamp(playbackSpeed <= 0f ? 1f : playbackSpeed, 0.1f, 3f);
            blendSeconds = Mathf.Clamp(blendSeconds < 0f ? fallbackBlend : blendSeconds, 0f, 2f);
            leftHandIkWeight = Mathf.Clamp01(leftHandIkWeight);
            rightHandIkWeight = Mathf.Clamp01(rightHandIkWeight);
            leftFootIkWeight = Mathf.Clamp01(leftFootIkWeight);
            rightFootIkWeight = Mathf.Clamp01(rightFootIkWeight);
            lookAtWeight = Mathf.Clamp01(lookAtWeight);
        }
    }

    public enum MotionIkTarget
    {
        LeftHand,
        RightHand,
        LeftFoot,
        RightFoot,
        LookAt
    }

    public enum MotionIkHandleKind
    {
        Effector,
        Pole
    }

    [Serializable]
    internal sealed class MotionAdjustmentDatabase
    {
        public List<MotionAdjustmentData> adjustments = new();
    }

    public static class AIGalgameMotionAdjustmentStore
    {
        private const string EditorAssetPath = "Assets/_AIgalgame/Animation/MotionCalibration.json";

        public static MotionAdjustmentData LoadOrDefault(string clipKey, string displayName, float fallbackBlend)
        {
            var database = LoadDatabase();
            var found = database.adjustments.FirstOrDefault(entry => entry.clipKey == clipKey);
            if (found == null)
            {
                return MotionAdjustmentData.CreateDefault(clipKey, displayName, fallbackBlend);
            }

            var copy = found.Clone();
            copy.displayName = string.IsNullOrWhiteSpace(copy.displayName) ? displayName : copy.displayName;
            copy.Sanitize(fallbackBlend);
            return copy;
        }

        public static void Save(MotionAdjustmentData adjustment)
        {
            if (adjustment == null || string.IsNullOrWhiteSpace(adjustment.clipKey))
            {
                return;
            }

            adjustment.Sanitize(0.35f);

            var database = LoadDatabase();
            var existingIndex = database.adjustments.FindIndex(entry => entry.clipKey == adjustment.clipKey);
            if (existingIndex >= 0)
            {
                database.adjustments[existingIndex] = adjustment.Clone();
            }
            else
            {
                database.adjustments.Add(adjustment.Clone());
            }

            SaveDatabase(database);
        }

        public static void Delete(string clipKey)
        {
            if (string.IsNullOrWhiteSpace(clipKey))
            {
                return;
            }

            var database = LoadDatabase();
            if (database.adjustments.RemoveAll(entry => entry.clipKey == clipKey) > 0)
            {
                SaveDatabase(database);
            }
        }

        private static MotionAdjustmentDatabase LoadDatabase()
        {
            var path = GetProfilePath();
            if (!File.Exists(path))
            {
                return new MotionAdjustmentDatabase();
            }

            try
            {
                var database = JsonUtility.FromJson<MotionAdjustmentDatabase>(File.ReadAllText(path));
                if (database == null)
                {
                    database = new MotionAdjustmentDatabase();
                }

                database.adjustments ??= new List<MotionAdjustmentData>();
                return database;
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"Could not load motion calibration profile at {path}: {exception.Message}");
                return new MotionAdjustmentDatabase();
            }
        }

        private static void SaveDatabase(MotionAdjustmentDatabase database)
        {
            var path = GetProfilePath();
            Directory.CreateDirectory(Path.GetDirectoryName(path) ?? Application.persistentDataPath);
            File.WriteAllText(path, JsonUtility.ToJson(database, true));

#if UNITY_EDITOR
            AssetDatabase.ImportAsset(EditorAssetPath);
#endif
        }

        private static string GetProfilePath()
        {
#if UNITY_EDITOR
            var projectRoot = Directory.GetParent(Application.dataPath)?.FullName ?? Application.dataPath;
            return Path.Combine(projectRoot, EditorAssetPath.Replace("/", Path.DirectorySeparatorChar.ToString()));
#else
            return Path.Combine(Application.persistentDataPath, "MotionCalibration.json");
#endif
        }
    }

    [DefaultExecutionOrder(150)]
    public sealed class AIGalgameHumanoidIKAdjuster : MonoBehaviour
    {
        [SerializeField] private Animator animator;
        [SerializeField] private float targetMarkerScale = 0.055f;
        [SerializeField] private bool showTargetMarkers;

        private Transform targetRoot;
        private Transform leftHandTarget;
        private Transform rightHandTarget;
        private Transform leftFootTarget;
        private Transform rightFootTarget;
        private Transform lookAtTarget;
        private Transform leftHandPole;
        private Transform rightHandPole;
        private Transform leftFootPole;
        private Transform rightFootPole;
        private float leftHandWeight;
        private float rightHandWeight;
        private float leftFootWeight;
        private float rightFootWeight;
        private float lookAtWeight;
        private bool sceneHandleChanged;

        public bool HasSceneHandleChanged => sceneHandleChanged;

        public bool ShowTargetMarkers
        {
            get => showTargetMarkers;
            set
            {
                if (showTargetMarkers == value)
                {
                    return;
                }

                showTargetMarkers = value;
                ApplyAllTargetMarkerVisibility();
            }
        }

        private void OnValidate()
        {
            ApplyAllTargetMarkerVisibility();
        }

        public void ClearSceneHandleChanged()
        {
            sceneHandleChanged = false;
        }

        public void SetAnimator(Animator targetAnimator)
        {
            if (animator == targetAnimator)
            {
                return;
            }

            animator = targetAnimator;
            targetRoot = null;
            EnsureTargets();
        }

        public void ApplyAdjustment(MotionAdjustmentData adjustment)
        {
            if (adjustment == null)
            {
                return;
            }

            adjustment.Sanitize(0.35f);
            EnsureTargets();

            leftHandWeight = adjustment.leftHandIkWeight;
            rightHandWeight = adjustment.rightHandIkWeight;
            leftFootWeight = adjustment.leftFootIkWeight;
            rightFootWeight = adjustment.rightFootIkWeight;
            lookAtWeight = adjustment.lookAtWeight;

            ApplyTargetFromAdjustment(MotionIkTarget.LeftHand, adjustment.hasLeftHandTarget, adjustment.leftHandTargetLocalPosition);
            ApplyTargetFromAdjustment(MotionIkTarget.RightHand, adjustment.hasRightHandTarget, adjustment.rightHandTargetLocalPosition);
            ApplyTargetFromAdjustment(MotionIkTarget.LeftFoot, adjustment.hasLeftFootTarget, adjustment.leftFootTargetLocalPosition);
            ApplyTargetFromAdjustment(MotionIkTarget.RightFoot, adjustment.hasRightFootTarget, adjustment.rightFootTargetLocalPosition);
            ApplyTargetFromAdjustment(MotionIkTarget.LookAt, adjustment.hasLookAtTarget, adjustment.lookAtTargetLocalPosition);
            ApplyTargetRotation(MotionIkTarget.LeftHand, adjustment.leftHandTargetLocalEuler);
            ApplyTargetRotation(MotionIkTarget.RightHand, adjustment.rightHandTargetLocalEuler);
            ApplyTargetRotation(MotionIkTarget.LeftFoot, adjustment.leftFootTargetLocalEuler);
            ApplyTargetRotation(MotionIkTarget.RightFoot, adjustment.rightFootTargetLocalEuler);
            ApplyPoleFromAdjustment(MotionIkTarget.LeftHand, adjustment.hasLeftHandPole, adjustment.leftHandPoleLocalPosition);
            ApplyPoleFromAdjustment(MotionIkTarget.RightHand, adjustment.hasRightHandPole, adjustment.rightHandPoleLocalPosition);
            ApplyPoleFromAdjustment(MotionIkTarget.LeftFoot, adjustment.hasLeftFootPole, adjustment.leftFootPoleLocalPosition);
            ApplyPoleFromAdjustment(MotionIkTarget.RightFoot, adjustment.hasRightFootPole, adjustment.rightFootPoleLocalPosition);
        }

        public void WriteToAdjustment(MotionAdjustmentData adjustment)
        {
            if (adjustment == null)
            {
                return;
            }

            EnsureTargets();
            adjustment.leftHandIkWeight = leftHandWeight;
            adjustment.rightHandIkWeight = rightHandWeight;
            adjustment.leftFootIkWeight = leftFootWeight;
            adjustment.rightFootIkWeight = rightFootWeight;
            adjustment.lookAtWeight = lookAtWeight;

            WriteTargetToAdjustment(MotionIkTarget.LeftHand, out adjustment.hasLeftHandTarget, out adjustment.leftHandTargetLocalPosition);
            WriteTargetToAdjustment(MotionIkTarget.RightHand, out adjustment.hasRightHandTarget, out adjustment.rightHandTargetLocalPosition);
            WriteTargetToAdjustment(MotionIkTarget.LeftFoot, out adjustment.hasLeftFootTarget, out adjustment.leftFootTargetLocalPosition);
            WriteTargetToAdjustment(MotionIkTarget.RightFoot, out adjustment.hasRightFootTarget, out adjustment.rightFootTargetLocalPosition);
            WriteTargetToAdjustment(MotionIkTarget.LookAt, out adjustment.hasLookAtTarget, out adjustment.lookAtTargetLocalPosition);
            adjustment.leftHandTargetLocalEuler = GetTargetLocalEuler(MotionIkTarget.LeftHand);
            adjustment.rightHandTargetLocalEuler = GetTargetLocalEuler(MotionIkTarget.RightHand);
            adjustment.leftFootTargetLocalEuler = GetTargetLocalEuler(MotionIkTarget.LeftFoot);
            adjustment.rightFootTargetLocalEuler = GetTargetLocalEuler(MotionIkTarget.RightFoot);
            WritePoleToAdjustment(MotionIkTarget.LeftHand, out adjustment.hasLeftHandPole, out adjustment.leftHandPoleLocalPosition);
            WritePoleToAdjustment(MotionIkTarget.RightHand, out adjustment.hasRightHandPole, out adjustment.rightHandPoleLocalPosition);
            WritePoleToAdjustment(MotionIkTarget.LeftFoot, out adjustment.hasLeftFootPole, out adjustment.leftFootPoleLocalPosition);
            WritePoleToAdjustment(MotionIkTarget.RightFoot, out adjustment.hasRightFootPole, out adjustment.rightFootPoleLocalPosition);
        }

        public float GetWeight(MotionIkTarget target)
        {
            return target switch
            {
                MotionIkTarget.LeftHand => leftHandWeight,
                MotionIkTarget.RightHand => rightHandWeight,
                MotionIkTarget.LeftFoot => leftFootWeight,
                MotionIkTarget.RightFoot => rightFootWeight,
                MotionIkTarget.LookAt => lookAtWeight,
                _ => 0f
            };
        }

        public void SetWeight(MotionIkTarget target, float value)
        {
            value = Mathf.Clamp01(value);
            switch (target)
            {
                case MotionIkTarget.LeftHand:
                    leftHandWeight = value;
                    break;
                case MotionIkTarget.RightHand:
                    rightHandWeight = value;
                    break;
                case MotionIkTarget.LeftFoot:
                    leftFootWeight = value;
                    break;
                case MotionIkTarget.RightFoot:
                    rightFootWeight = value;
                    break;
                case MotionIkTarget.LookAt:
                    lookAtWeight = value;
                    break;
            }
        }

        public void ResetAllWeights()
        {
            leftHandWeight = 0f;
            rightHandWeight = 0f;
            leftFootWeight = 0f;
            rightFootWeight = 0f;
            lookAtWeight = 0f;
        }

        public Vector3 GetTargetLocalPosition(MotionIkTarget target)
        {
            EnsureTargets();
            var targetTransform = GetTarget(target);
            return targetTransform != null ? targetTransform.localPosition : Vector3.zero;
        }

        public void SetTargetLocalPosition(MotionIkTarget target, Vector3 localPosition)
        {
            EnsureTargets();
            var targetTransform = GetTarget(target);
            if (targetTransform != null)
            {
                targetTransform.localPosition = localPosition;
            }
        }

        public Vector3 GetTargetLocalEuler(MotionIkTarget target)
        {
            EnsureTargets();
            var targetTransform = GetTarget(target);
            return targetTransform != null ? NormalizeEuler(targetTransform.localEulerAngles) : Vector3.zero;
        }

        public void SetTargetLocalEuler(MotionIkTarget target, Vector3 localEuler)
        {
            EnsureTargets();
            var targetTransform = GetTarget(target);
            if (targetTransform != null)
            {
                targetTransform.localRotation = Quaternion.Euler(localEuler);
            }
        }

        public Vector3 GetPoleLocalPosition(MotionIkTarget target)
        {
            EnsureTargets();
            var poleTransform = GetPole(target);
            return poleTransform != null ? poleTransform.localPosition : Vector3.zero;
        }

        public void SetPoleLocalPosition(MotionIkTarget target, Vector3 localPosition)
        {
            EnsureTargets();
            var poleTransform = GetPole(target);
            if (poleTransform != null)
            {
                poleTransform.localPosition = localPosition;
            }
        }

        public void SnapTargetToCurrentBone(MotionIkTarget target)
        {
            EnsureTargets();
            var targetTransform = GetTarget(target);
            var boneTransform = GetEndBone(target);
            if (targetTransform == null || boneTransform == null || targetRoot == null)
            {
                return;
            }

            targetTransform.localPosition = targetRoot.InverseTransformPoint(boneTransform.position);
        }

        public void SnapPoleToCurrentBend(MotionIkTarget target)
        {
            EnsureTargets();
            var poleTransform = GetPole(target);
            var upper = GetUpperBone(target);
            var lower = GetMidBone(target);
            if (poleTransform == null || upper == null || lower == null || targetRoot == null)
            {
                return;
            }

            var bendDirection = (lower.position - upper.position).normalized;
            var length = Mathf.Max(0.25f, Vector3.Distance(upper.position, lower.position));
            poleTransform.localPosition = targetRoot.InverseTransformPoint(lower.position + bendDirection * length);
        }

        public Transform GetControlTransform(MotionIkTarget target, MotionIkHandleKind handleKind)
        {
            EnsureTargets();
            return handleKind == MotionIkHandleKind.Pole ? GetPole(target) : GetTarget(target);
        }

        public void MarkSceneHandleChanged()
        {
            sceneHandleChanged = true;
        }

        private void LateUpdate()
        {
            if (!CanApplyIk())
            {
                return;
            }

            EnsureTargets();
            SolveTwoBone(HumanBodyBones.LeftUpperArm, HumanBodyBones.LeftLowerArm, HumanBodyBones.LeftHand, leftHandTarget, leftHandPole, leftHandWeight);
            SolveTwoBone(HumanBodyBones.RightUpperArm, HumanBodyBones.RightLowerArm, HumanBodyBones.RightHand, rightHandTarget, rightHandPole, rightHandWeight);
            SolveTwoBone(HumanBodyBones.LeftUpperLeg, HumanBodyBones.LeftLowerLeg, HumanBodyBones.LeftFoot, leftFootTarget, leftFootPole, leftFootWeight);
            SolveTwoBone(HumanBodyBones.RightUpperLeg, HumanBodyBones.RightLowerLeg, HumanBodyBones.RightFoot, rightFootTarget, rightFootPole, rightFootWeight);
            ApplyLookAt();
        }

        private bool CanApplyIk()
        {
            if (animator == null)
            {
                animator = GetComponent<Animator>() ?? GetComponentInChildren<Animator>();
            }

            return animator != null && animator.avatar != null && animator.avatar.isHuman;
        }

        private void EnsureTargets()
        {
            if (animator == null)
            {
                animator = GetComponent<Animator>() ?? GetComponentInChildren<Animator>();
            }

            if (animator == null)
            {
                return;
            }

            if (targetRoot == null)
            {
                var existingRoot = animator.transform.Find("AIgalgame_IKTargets");
                if (existingRoot != null)
                {
                    targetRoot = existingRoot;
                }
                else
                {
                    var root = new GameObject("AIgalgame_IKTargets");
                    targetRoot = root.transform;
                    targetRoot.SetParent(animator.transform, false);
                    targetRoot.localPosition = Vector3.zero;
                    targetRoot.localRotation = Quaternion.identity;
                    targetRoot.localScale = Vector3.one;
                }
            }

            leftHandTarget = EnsureTarget(leftHandTarget, "LeftHand_Target", MotionIkTarget.LeftHand);
            rightHandTarget = EnsureTarget(rightHandTarget, "RightHand_Target", MotionIkTarget.RightHand);
            leftFootTarget = EnsureTarget(leftFootTarget, "LeftFoot_Target", MotionIkTarget.LeftFoot);
            rightFootTarget = EnsureTarget(rightFootTarget, "RightFoot_Target", MotionIkTarget.RightFoot);
            lookAtTarget = EnsureTarget(lookAtTarget, "LookAt_Target", MotionIkTarget.LookAt);
            leftHandPole = EnsurePole(leftHandPole, "LeftHand_Pole", MotionIkTarget.LeftHand);
            rightHandPole = EnsurePole(rightHandPole, "RightHand_Pole", MotionIkTarget.RightHand);
            leftFootPole = EnsurePole(leftFootPole, "LeftFoot_Pole", MotionIkTarget.LeftFoot);
            rightFootPole = EnsurePole(rightFootPole, "RightFoot_Pole", MotionIkTarget.RightFoot);
        }

        private Transform EnsureTarget(Transform existing, string name, MotionIkTarget target)
        {
            if (existing == null && targetRoot != null)
            {
                existing = targetRoot.Find(name);
            }

            if (existing != null)
            {
                ApplyTargetMarkerVisibility(existing.gameObject);
                return existing;
            }

            var go = new GameObject(name);
            var transformTarget = go.transform;
            transformTarget.SetParent(targetRoot, false);
            transformTarget.localScale = Vector3.one * targetMarkerScale;
            transformTarget.localPosition = GetDefaultLocalPosition(target);
            ApplyTargetMarkerVisibility(go);
            return transformTarget;
        }

        private Transform EnsurePole(Transform existing, string name, MotionIkTarget target)
        {
            if (existing == null && targetRoot != null)
            {
                existing = targetRoot.Find(name);
            }

            if (existing != null)
            {
                ApplyTargetMarkerVisibility(existing.gameObject);
                return existing;
            }

            var go = new GameObject(name);
            var pole = go.transform;
            pole.SetParent(targetRoot, false);
            pole.localScale = Vector3.one * targetMarkerScale * 0.85f;
            pole.localPosition = GetDefaultPoleLocalPosition(target);
            ApplyTargetMarkerVisibility(go);
            return pole;
        }

        private void ApplyAllTargetMarkerVisibility()
        {
            if (targetRoot == null)
            {
                return;
            }

            for (var i = 0; i < targetRoot.childCount; i++)
            {
                ApplyTargetMarkerVisibility(targetRoot.GetChild(i).gameObject);
            }
        }

        private void ApplyTargetMarkerVisibility(GameObject targetObject)
        {
            if (targetObject == null)
            {
                return;
            }

            foreach (var renderer in targetObject.GetComponentsInChildren<Renderer>(true))
            {
                renderer.enabled = showTargetMarkers;
            }

            foreach (var collider in targetObject.GetComponentsInChildren<Collider>(true))
            {
                collider.enabled = showTargetMarkers;
            }
        }

        private Vector3 GetDefaultLocalPosition(MotionIkTarget target)
        {
            var bone = GetEndBone(target);
            if (bone != null && targetRoot != null)
            {
                return targetRoot.InverseTransformPoint(bone.position);
            }

            return target switch
            {
                MotionIkTarget.LeftHand => new Vector3(-0.28f, 1.05f, 0.24f),
                MotionIkTarget.RightHand => new Vector3(0.28f, 1.05f, 0.24f),
                MotionIkTarget.LeftFoot => new Vector3(-0.12f, 0.05f, 0.18f),
                MotionIkTarget.RightFoot => new Vector3(0.12f, 0.05f, 0.18f),
                MotionIkTarget.LookAt => new Vector3(0f, 1.45f, 1.2f),
                _ => Vector3.zero
            };
        }

        private Vector3 GetDefaultPoleLocalPosition(MotionIkTarget target)
        {
            var upper = GetUpperBone(target);
            var lower = GetMidBone(target);
            if (upper != null && lower != null && targetRoot != null)
            {
                var bendDirection = (lower.position - upper.position).normalized;
                var distance = Mathf.Max(0.25f, Vector3.Distance(upper.position, lower.position));
                return targetRoot.InverseTransformPoint(lower.position + bendDirection * distance);
            }

            return target switch
            {
                MotionIkTarget.LeftHand => new Vector3(-0.45f, 1.1f, 0.05f),
                MotionIkTarget.RightHand => new Vector3(0.45f, 1.1f, 0.05f),
                MotionIkTarget.LeftFoot => new Vector3(-0.24f, 0.45f, 0.18f),
                MotionIkTarget.RightFoot => new Vector3(0.24f, 0.45f, 0.18f),
                _ => Vector3.zero
            };
        }

        private Transform GetTarget(MotionIkTarget target)
        {
            return target switch
            {
                MotionIkTarget.LeftHand => leftHandTarget,
                MotionIkTarget.RightHand => rightHandTarget,
                MotionIkTarget.LeftFoot => leftFootTarget,
                MotionIkTarget.RightFoot => rightFootTarget,
                MotionIkTarget.LookAt => lookAtTarget,
                _ => null
            };
        }

        private Transform GetPole(MotionIkTarget target)
        {
            return target switch
            {
                MotionIkTarget.LeftHand => leftHandPole,
                MotionIkTarget.RightHand => rightHandPole,
                MotionIkTarget.LeftFoot => leftFootPole,
                MotionIkTarget.RightFoot => rightFootPole,
                _ => null
            };
        }

        private Transform GetUpperBone(MotionIkTarget target)
        {
            if (animator == null)
            {
                return null;
            }

            return target switch
            {
                MotionIkTarget.LeftHand => animator.GetBoneTransform(HumanBodyBones.LeftUpperArm),
                MotionIkTarget.RightHand => animator.GetBoneTransform(HumanBodyBones.RightUpperArm),
                MotionIkTarget.LeftFoot => animator.GetBoneTransform(HumanBodyBones.LeftUpperLeg),
                MotionIkTarget.RightFoot => animator.GetBoneTransform(HumanBodyBones.RightUpperLeg),
                _ => null
            };
        }

        private Transform GetMidBone(MotionIkTarget target)
        {
            if (animator == null)
            {
                return null;
            }

            return target switch
            {
                MotionIkTarget.LeftHand => animator.GetBoneTransform(HumanBodyBones.LeftLowerArm),
                MotionIkTarget.RightHand => animator.GetBoneTransform(HumanBodyBones.RightLowerArm),
                MotionIkTarget.LeftFoot => animator.GetBoneTransform(HumanBodyBones.LeftLowerLeg),
                MotionIkTarget.RightFoot => animator.GetBoneTransform(HumanBodyBones.RightLowerLeg),
                _ => null
            };
        }

        private Transform GetEndBone(MotionIkTarget target)
        {
            if (animator == null)
            {
                return null;
            }

            return target switch
            {
                MotionIkTarget.LeftHand => animator.GetBoneTransform(HumanBodyBones.LeftHand),
                MotionIkTarget.RightHand => animator.GetBoneTransform(HumanBodyBones.RightHand),
                MotionIkTarget.LeftFoot => animator.GetBoneTransform(HumanBodyBones.LeftFoot),
                MotionIkTarget.RightFoot => animator.GetBoneTransform(HumanBodyBones.RightFoot),
                MotionIkTarget.LookAt => animator.GetBoneTransform(HumanBodyBones.Head),
                _ => null
            };
        }

        private void ApplyTargetFromAdjustment(MotionIkTarget target, bool hasTarget, Vector3 localPosition)
        {
            var targetTransform = GetTarget(target);
            if (targetTransform == null)
            {
                return;
            }

            targetTransform.localPosition = hasTarget ? localPosition : GetDefaultLocalPosition(target);
        }

        private void ApplyTargetRotation(MotionIkTarget target, Vector3 localEuler)
        {
            var targetTransform = GetTarget(target);
            if (targetTransform != null)
            {
                targetTransform.localRotation = Quaternion.Euler(localEuler);
            }
        }

        private void ApplyPoleFromAdjustment(MotionIkTarget target, bool hasTarget, Vector3 localPosition)
        {
            var poleTransform = GetPole(target);
            if (poleTransform == null)
            {
                return;
            }

            poleTransform.localPosition = hasTarget ? localPosition : GetDefaultPoleLocalPosition(target);
        }

        private void WriteTargetToAdjustment(MotionIkTarget target, out bool hasTarget, out Vector3 localPosition)
        {
            var targetTransform = GetTarget(target);
            hasTarget = targetTransform != null;
            localPosition = hasTarget ? targetTransform.localPosition : Vector3.zero;
        }

        private void WritePoleToAdjustment(MotionIkTarget target, out bool hasPole, out Vector3 localPosition)
        {
            var poleTransform = GetPole(target);
            hasPole = poleTransform != null;
            localPosition = hasPole ? poleTransform.localPosition : Vector3.zero;
        }

        private void SolveTwoBone(
            HumanBodyBones upperBone,
            HumanBodyBones lowerBone,
            HumanBodyBones endBone,
            Transform target,
            Transform pole,
            float weight)
        {
            if (weight <= 0.001f || target == null)
            {
                return;
            }

            var upper = animator.GetBoneTransform(upperBone);
            var lower = animator.GetBoneTransform(lowerBone);
            var end = animator.GetBoneTransform(endBone);
            if (upper == null || lower == null || end == null)
            {
                return;
            }

            var rootPos = upper.position;
            var currentMidPos = lower.position;
            var currentEndPos = end.position;
            var desiredEndPos = Vector3.Lerp(currentEndPos, target.position, weight);

            var upperLength = Vector3.Distance(rootPos, currentMidPos);
            var lowerLength = Vector3.Distance(currentMidPos, currentEndPos);
            if (upperLength <= 0.0001f || lowerLength <= 0.0001f)
            {
                return;
            }

            var rootToTarget = desiredEndPos - rootPos;
            var distance = Mathf.Clamp(rootToTarget.magnitude, 0.0001f, upperLength + lowerLength - 0.0001f);
            var targetDirection = rootToTarget.normalized;
            var bendDirection = Vector3.ProjectOnPlane(currentMidPos - rootPos, targetDirection).normalized;
            if (pole != null)
            {
                var poleDirection = Vector3.ProjectOnPlane(pole.position - rootPos, targetDirection).normalized;
                if (poleDirection.sqrMagnitude > 0.0001f)
                {
                    bendDirection = poleDirection;
                }
            }

            if (bendDirection.sqrMagnitude <= 0.0001f)
            {
                bendDirection = Vector3.ProjectOnPlane(animator.transform.forward, targetDirection).normalized;
            }

            if (bendDirection.sqrMagnitude <= 0.0001f)
            {
                bendDirection = Vector3.Cross(targetDirection, animator.transform.up).normalized;
            }

            var alongTarget = (upperLength * upperLength + distance * distance - lowerLength * lowerLength) / (2f * distance);
            var bendDistance = Mathf.Sqrt(Mathf.Max(0f, upperLength * upperLength - alongTarget * alongTarget));
            var desiredMidPos = rootPos + targetDirection * alongTarget + bendDirection * bendDistance;

            var upperRotation = Quaternion.FromToRotation(currentMidPos - rootPos, desiredMidPos - rootPos) * upper.rotation;
            upper.rotation = Quaternion.Slerp(upper.rotation, upperRotation, weight);

            var lowerToEnd = end.position - lower.position;
            var lowerToTarget = desiredEndPos - lower.position;
            if (lowerToEnd.sqrMagnitude > 0.0001f && lowerToTarget.sqrMagnitude > 0.0001f)
            {
                var lowerRotation = Quaternion.FromToRotation(lowerToEnd, lowerToTarget) * lower.rotation;
                lower.rotation = Quaternion.Slerp(lower.rotation, lowerRotation, weight);
            }

            end.rotation = Quaternion.Slerp(end.rotation, target.rotation, weight);
        }

        private void ApplyLookAt()
        {
            if (lookAtWeight <= 0.001f || lookAtTarget == null)
            {
                return;
            }

            var head = animator.GetBoneTransform(HumanBodyBones.Head);
            if (head == null)
            {
                return;
            }

            var direction = lookAtTarget.position - head.position;
            if (direction.sqrMagnitude <= 0.0001f)
            {
                return;
            }

            var desired = Quaternion.FromToRotation(head.forward, direction.normalized) * head.rotation;
            head.rotation = Quaternion.Slerp(head.rotation, desired, lookAtWeight);
        }

        private static Vector3 NormalizeEuler(Vector3 euler)
        {
            return new Vector3(NormalizeAngle(euler.x), NormalizeAngle(euler.y), NormalizeAngle(euler.z));
        }

        private static float NormalizeAngle(float value)
        {
            value %= 360f;
            return value > 180f ? value - 360f : value;
        }
    }

    [DefaultExecutionOrder(-120)]
    [DisallowMultipleComponent]
    public sealed class AIGalgamePlayableMotionPlayer : MonoBehaviour
    {
        private const string DefaultMotionFolder = "Assets/motion";

        [SerializeField] private Animator animator;
        [SerializeField] private List<MotionClipEntry> clips = new();
        [SerializeField] private float blendSeconds = 0.35f;
        [SerializeField] private bool playFirstClipOnStart = true;
        [SerializeField] private bool loopClips = true;
        [SerializeField] private bool applyFootIk = true;
        [SerializeField] private bool disableLegacyMotionPlayer = true;
        [SerializeField] private bool clearAnimatorControllerOnStart = true;
        [SerializeField] private Vector3 rootPositionOffset;
        [SerializeField] private Vector3 rootRotationOffset;
        [SerializeField] private float rootScale = 1f;
        [SerializeField] private float playbackSpeed = 1f;
        [SerializeField] private AIGalgameHumanoidIKAdjuster ikAdjuster;

        private bool preferAnimatorController;
        private readonly AnimationClip[] inputClips = new AnimationClip[2];
        private PlayableGraph graph;
        private AnimationMixerPlayable mixer;
        private int currentInput = -1;
        private int fadingFromInput = -1;
        private int fadingToInput = -1;
        private float fadeElapsed;
        private float fadeDuration;
        private int currentClipIndex = -1;
        private Vector3 baseWorldPosition;
        private Quaternion baseWorldRotation;
        private Vector3 baseLocalScale;
        private bool hasBaseTransform;

        public event Action CatalogChanged;
        public event Action<int, MotionClipEntry> MotionChanged;

        public IReadOnlyList<MotionClipEntry> Clips => clips;
        public int CurrentClipIndex => currentClipIndex;
        public Animator Animator => animator;
        public AIGalgameHumanoidIKAdjuster IkAdjuster => EnsureIkAdjuster();
        public bool PrefersAnimatorController => preferAnimatorController;
        public float BlendSeconds
        {
            get => blendSeconds;
            set => blendSeconds = Mathf.Clamp(value, 0f, 2f);
        }
        public float PlaybackSpeed
        {
            get => playbackSpeed;
            set => SetPlaybackSpeed(value);
        }

        private void Awake()
        {
            ResolveReferences();
            CaptureBaseTransform(force: true);

            if (disableLegacyMotionPlayer && TryGetComponent<AIGalgameMotionPlayer>(out var legacyPlayer))
            {
                legacyPlayer.enabled = false;
            }

            EnsureIkAdjuster();
        }

        private void Start()
        {
            if (clips.Count == 0)
            {
                RefreshClipCatalog();
            }

            if (playFirstClipOnStart && clips.Count > 0)
            {
                PlayClip(0, 0f);
            }
        }

        private void Update()
        {
#if UNITY_EDITOR
            if (IsEditorAnimationPreviewActive())
            {
                DestroyGraph();
                return;
            }
#endif

            if (!graph.IsValid())
            {
                return;
            }

            WrapInput(currentInput);
            WrapInput(fadingFromInput);
            WrapInput(fadingToInput);

            if (fadingToInput < 0)
            {
                return;
            }

            fadeElapsed += Time.deltaTime;
            var t = fadeDuration <= 0f ? 1f : Mathf.Clamp01(fadeElapsed / fadeDuration);

            mixer.SetInputWeight(fadingFromInput, 1f - t);
            mixer.SetInputWeight(fadingToInput, t);

            if (t < 1f)
            {
                return;
            }

            DestroyInput(fadingFromInput);
            currentInput = fadingToInput;
            mixer.SetInputWeight(currentInput, 1f);
            fadingFromInput = -1;
            fadingToInput = -1;
        }

        private void LateUpdate()
        {
#if UNITY_EDITOR
            if (IsEditorAnimationPreviewActive())
            {
                return;
            }
#endif

            if (!graph.IsValid())
            {
                return;
            }

            ApplyRootAdjustment();
        }

        private void OnDestroy()
        {
            DestroyGraph();
        }

        public void SetAnimator(Animator targetAnimator)
        {
            animator = targetAnimator;
            DestroyGraph();

            hasBaseTransform = false;
            CaptureBaseTransform(force: true);
            EnsureIkAdjuster();
        }

        public void ConfigureForAnimatorControllerDriver()
        {
            playFirstClipOnStart = false;
            clearAnimatorControllerOnStart = false;
            preferAnimatorController = true;
            Stop();
        }

        public string GetClipKey(int index)
        {
            if (index < 0 || index >= clips.Count || clips[index].clip == null)
            {
                return string.Empty;
            }

            var clip = clips[index].clip;
#if UNITY_EDITOR
            var assetPath = AssetDatabase.GetAssetPath(clip);
            if (!string.IsNullOrWhiteSpace(assetPath))
            {
                return $"{assetPath}::{clip.name}";
            }
#endif
            return clip.name;
        }

        public string GetClipAssetPath(int index)
        {
            if (index < 0 || index >= clips.Count || clips[index].clip == null)
            {
                return string.Empty;
            }

#if UNITY_EDITOR
            return AssetDatabase.GetAssetPath(clips[index].clip);
#else
            return string.Empty;
#endif
        }

        public int FindClipIndex(string motionHint, params string[] pathHints)
        {
            if (clips.Count == 0)
            {
                RefreshClipCatalog();
            }

            var normalizedHint = NormalizeToken(motionHint);
            var normalizedPathHints = (pathHints ?? Array.Empty<string>())
                .Select(NormalizeToken)
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .Distinct()
                .ToArray();

            for (var pass = 0; pass < 2; pass++)
            {
                for (var i = 0; i < clips.Count; i++)
                {
                    var entry = clips[i];
                    var normalizedNames = new[]
                    {
                        entry?.displayName,
                        entry?.clip != null ? entry.clip.name : ""
                    }
                        .Select(NormalizeToken)
                        .Where(item => !string.IsNullOrWhiteSpace(item))
                        .ToArray();

                    if (!string.IsNullOrWhiteSpace(normalizedHint))
                    {
                        var nameMatches = pass == 0
                            ? normalizedNames.Any(name => name == normalizedHint)
                            : normalizedNames.Any(name => name.Contains(normalizedHint) || normalizedHint.Contains(name));
                        if (!nameMatches)
                        {
                            continue;
                        }
                    }

                    if (!PathMatches(i, normalizedPathHints))
                    {
                        continue;
                    }

                    return i;
                }
            }

            return -1;
        }

        public bool PlayClip(string motionHint, float transitionSeconds, params string[] pathHints)
        {
            var index = FindClipIndex(motionHint, pathHints);
            if (index < 0)
            {
                return false;
            }

            PlayClip(index, transitionSeconds);
            return true;
        }

        public MotionAdjustmentData GetAdjustmentSnapshot()
        {
            var entry = currentClipIndex >= 0 && currentClipIndex < clips.Count ? clips[currentClipIndex] : null;
            var snapshot = new MotionAdjustmentData
            {
                clipKey = GetClipKey(currentClipIndex),
                displayName = entry?.displayName ?? string.Empty,
                rootPositionOffset = rootPositionOffset,
                rootRotationOffset = rootRotationOffset,
                rootScale = rootScale,
                playbackSpeed = playbackSpeed,
                blendSeconds = blendSeconds
            };

            ikAdjuster?.WriteToAdjustment(snapshot);
            return snapshot;
        }

        public void ApplyMotionAdjustment(MotionAdjustmentData adjustment)
        {
            if (adjustment == null)
            {
                return;
            }

            adjustment.Sanitize(blendSeconds);
            rootPositionOffset = adjustment.rootPositionOffset;
            rootRotationOffset = adjustment.rootRotationOffset;
            rootScale = adjustment.rootScale;
            BlendSeconds = adjustment.blendSeconds;
            PlaybackSpeed = adjustment.playbackSpeed;
            EnsureIkAdjuster()?.ApplyAdjustment(adjustment);
            ApplyRootAdjustment();
        }

        public void WriteIkToAdjustment(MotionAdjustmentData adjustment)
        {
            EnsureIkAdjuster()?.WriteToAdjustment(adjustment);
        }

        public void RefreshClipCatalog()
        {
            var foundClips = LoadMotionClipsFromDefaultFolder();
            clips = foundClips
                .Select(clip => new MotionClipEntry
                {
                    displayName = MakeDisplayName(clip),
                    clip = clip
                })
                .OrderBy(entry => entry.displayName, StringComparer.OrdinalIgnoreCase)
                .ToList();

            CatalogChanged?.Invoke();
        }

        public void PlayClip(int index)
        {
            PlayClip(index, blendSeconds);
        }

        public void PlayClip(int index, float transitionSeconds)
        {
#if UNITY_EDITOR
            if (IsEditorAnimationPreviewActive())
            {
                DestroyGraph();
                return;
            }
#endif

            if (index < 0 || index >= clips.Count)
            {
                return;
            }

            var clip = clips[index].clip;
            if (clip == null)
            {
                return;
            }

            ResolveReferences();
            if (animator == null)
            {
                Debug.LogWarning($"{nameof(AIGalgamePlayableMotionPlayer)} needs an Animator target.", this);
                return;
            }

            if (preferAnimatorController && animator.runtimeAnimatorController != null)
            {
                Debug.LogWarning(
                    $"{nameof(AIGalgamePlayableMotionPlayer)} is in Animator Controller driver mode. Use the state machine instead of PlayClip.",
                    this);
                return;
            }

            if (clearAnimatorControllerOnStart && animator.runtimeAnimatorController != null)
            {
                animator.runtimeAnimatorController = null;
            }

            EnsureGraph();
            if (!graph.IsValid() || !mixer.IsValid())
            {
                return;
            }

            CompleteActiveFade();

            var nextInput = currentInput == 0 ? 1 : 0;
            DestroyInput(nextInput);

            var playable = AnimationClipPlayable.Create(graph, clip);
            playable.SetApplyFootIK(applyFootIk);
            playable.SetSpeed(playbackSpeed);
            playable.SetTime(0d);
            graph.Connect(playable, 0, mixer, nextInput);
            mixer.SetInputWeight(nextInput, currentInput < 0 || transitionSeconds <= 0f ? 1f : 0f);
            inputClips[nextInput] = clip;

            currentClipIndex = index;
            MotionChanged?.Invoke(currentClipIndex, clips[currentClipIndex]);

            if (currentInput < 0 || transitionSeconds <= 0f)
            {
                DestroyInput(currentInput);
                currentInput = nextInput;
                return;
            }

            fadingFromInput = currentInput;
            fadingToInput = nextInput;
            fadeElapsed = 0f;
            fadeDuration = transitionSeconds;
        }

        public void ReplayCurrent()
        {
            if (currentClipIndex >= 0)
            {
                PlayClip(currentClipIndex, 0f);
            }
            else if (clips.Count > 0)
            {
                PlayClip(0, 0f);
            }
        }

        public void PlayNext()
        {
            if (clips.Count == 0)
            {
                return;
            }

            PlayClip((Mathf.Max(currentClipIndex, 0) + 1) % clips.Count);
        }

        public void PlayPrevious()
        {
            if (clips.Count == 0)
            {
                return;
            }

            var next = currentClipIndex <= 0 ? clips.Count - 1 : currentClipIndex - 1;
            PlayClip(next);
        }

        public void Stop()
        {
            currentClipIndex = -1;
            DestroyGraph();
            MotionChanged?.Invoke(-1, null);
        }

        private void DestroyGraph()
        {
            fadingFromInput = -1;
            fadingToInput = -1;
            currentInput = -1;
            Array.Clear(inputClips, 0, inputClips.Length);
            if (graph.IsValid())
            {
                graph.Destroy();
            }

            graph = default;
            mixer = default;
        }

        private void ResolveReferences()
        {
            if (animator == null)
            {
                animator = GetComponent<Animator>();
            }

            if (animator == null)
            {
                animator = GetComponentInChildren<Animator>();
            }
        }

        private AIGalgameHumanoidIKAdjuster EnsureIkAdjuster()
        {
            ResolveReferences();
            if (animator == null)
            {
                return null;
            }

            if (ikAdjuster == null)
            {
                ikAdjuster = animator.GetComponent<AIGalgameHumanoidIKAdjuster>();
            }

            if (ikAdjuster == null)
            {
                Debug.LogWarning(
                    "AIGalgamePlayableMotionPlayer is missing AIGalgameHumanoidIKAdjuster. Add it to the character in the scene if IK handles are needed; runtime AddComponent is disabled.",
                    this);
                return null;
            }

            ikAdjuster.SetAnimator(animator);
            return ikAdjuster;
        }

        private void CaptureBaseTransform(bool force = false)
        {
            ResolveReferences();
            if (!force && hasBaseTransform)
            {
                return;
            }

            var target = animator != null ? animator.transform : transform;
            if (target == null)
            {
                return;
            }

            baseWorldPosition = target.position;
            baseWorldRotation = target.rotation;
            baseLocalScale = target.localScale;
            hasBaseTransform = true;
        }

        private void ApplyRootAdjustment()
        {
            if (!hasBaseTransform)
            {
                CaptureBaseTransform();
            }

            var target = animator != null ? animator.transform : transform;
            if (target == null || !hasBaseTransform)
            {
                return;
            }

            target.SetPositionAndRotation(
                baseWorldPosition + rootPositionOffset,
                baseWorldRotation * Quaternion.Euler(rootRotationOffset));
            target.localScale = baseLocalScale * rootScale;
        }

        private void SetPlaybackSpeed(float value)
        {
            playbackSpeed = Mathf.Clamp(value, 0.1f, 3f);
            ApplyPlaybackSpeedToInput(currentInput);
            ApplyPlaybackSpeedToInput(fadingFromInput);
            ApplyPlaybackSpeedToInput(fadingToInput);
        }

        private void ApplyPlaybackSpeedToInput(int input)
        {
            if (!graph.IsValid() || !mixer.IsValid() || input < 0 || input > 1)
            {
                return;
            }

            var playable = mixer.GetInput(input);
            if (playable.IsValid())
            {
                playable.SetSpeed(playbackSpeed);
            }
        }

        private void EnsureGraph()
        {
#if UNITY_EDITOR
            if (IsEditorAnimationPreviewActive())
            {
                return;
            }
#endif

            if (graph.IsValid())
            {
                return;
            }

            graph = PlayableGraph.Create("AIgalgame Motion Test");
            graph.SetTimeUpdateMode(DirectorUpdateMode.GameTime);
            mixer = AnimationMixerPlayable.Create(graph, 2);
            var output = AnimationPlayableOutput.Create(graph, "Motion Output", animator);
            if (!output.IsOutputValid())
            {
                DestroyGraph();
                return;
            }

            output.SetSourcePlayable(mixer);
            graph.Play();
        }

#if UNITY_EDITOR
        private static bool IsEditorAnimationPreviewActive()
        {
            return AnimationMode.InAnimationMode();
        }
#endif

        private void CompleteActiveFade()
        {
            if (fadingToInput < 0)
            {
                return;
            }

            DestroyInput(fadingFromInput);
            currentInput = fadingToInput;
            mixer.SetInputWeight(currentInput, 1f);
            fadingFromInput = -1;
            fadingToInput = -1;
        }

        private void DestroyInput(int input)
        {
            if (!graph.IsValid() || !mixer.IsValid() || input < 0 || input > 1)
            {
                return;
            }

            var playable = mixer.GetInput(input);
            if (playable.IsValid())
            {
                mixer.DisconnectInput(input);
                graph.DestroyPlayable(playable);
            }

            mixer.SetInputWeight(input, 0f);
            inputClips[input] = null;
        }

        private void WrapInput(int input)
        {
            if (!loopClips || !graph.IsValid() || !mixer.IsValid() || input < 0 || input > 1)
            {
                return;
            }

            var clip = inputClips[input];
            if (clip == null || clip.length <= 0.01f)
            {
                return;
            }

            var playable = mixer.GetInput(input);
            if (!playable.IsValid())
            {
                return;
            }

            var time = playable.GetTime();
            if (time >= clip.length)
            {
                playable.SetTime(time % clip.length);
            }
        }

        private static string MakeDisplayName(AnimationClip clip)
        {
            if (clip == null)
            {
                return "Missing Clip";
            }

            return clip.name
                .Replace("@", " ")
                .Replace("_", " ")
                .Trim();
        }

        private bool PathMatches(int index, IReadOnlyCollection<string> normalizedPathHints)
        {
            if (normalizedPathHints == null || normalizedPathHints.Count == 0)
            {
                return true;
            }

            var path = NormalizeToken(GetClipAssetPath(index));
            if (string.IsNullOrWhiteSpace(path))
            {
                return true;
            }

            return normalizedPathHints.All(path.Contains);
        }

        private static string NormalizeToken(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "";
            }

            return new string(value.Where(char.IsLetterOrDigit).Select(char.ToLowerInvariant).ToArray());
        }

        private static List<AnimationClip> LoadMotionClipsFromDefaultFolder()
        {
            var result = new List<AnimationClip>();

#if UNITY_EDITOR
            var paths = AssetDatabase.FindAssets("t:Model", new[] { DefaultMotionFolder })
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(path => path.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase))
                .Concat(AssetDatabase.FindAssets("t:AnimationClip", new[] { DefaultMotionFolder })
                    .Select(AssetDatabase.GUIDToAssetPath)
                    .Where(path => path.EndsWith(".anim", StringComparison.OrdinalIgnoreCase)))
                .Distinct(StringComparer.OrdinalIgnoreCase);

            foreach (var path in paths)
            {
                if (path.EndsWith(".anim", StringComparison.OrdinalIgnoreCase))
                {
                    var standaloneClip = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
                    if (IsUsableMotionClip(standaloneClip) && !result.Contains(standaloneClip))
                    {
                        result.Add(standaloneClip);
                    }

                    continue;
                }

                var clipsAtPath = AssetDatabase.LoadAllAssetRepresentationsAtPath(path)
                    .Concat(AssetDatabase.LoadAllAssetsAtPath(path))
                    .OfType<AnimationClip>()
                    .Where(IsUsableMotionClip);

                foreach (var clip in clipsAtPath)
                {
                    if (!result.Contains(clip))
                    {
                        result.Add(clip);
                    }
                }
            }
#endif

            return result;
        }

        private static bool IsUsableMotionClip(AnimationClip clip)
        {
            if (clip == null)
            {
                return false;
            }

            if (clip.name.StartsWith("__preview__", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            return clip.length > 0.01f;
        }
    }
}
