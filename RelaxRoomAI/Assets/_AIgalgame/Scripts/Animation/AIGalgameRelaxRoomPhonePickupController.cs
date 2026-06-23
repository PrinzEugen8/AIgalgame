using System.Collections;
using UnityEngine;
using UnityEngine.Animations.Rigging;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(-109)]
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomPhonePickupController : MonoBehaviour
    {
        private enum PhoneHand
        {
            Left,
            Right
        }

        [Header("Targets")]
        [SerializeField] private Animator animator;
        [SerializeField] private AIGalgamePlayableMotionPlayer motionPlayer;
        [SerializeField] private AIGalgameRelaxRoomPhoneAttachmentController phoneAttachment;
        [SerializeField] private AIGalgameRelaxRoomPhoneScreenController phoneScreen;

        [Header("Animation Rigging")]
        [SerializeField] private TwoBoneIKConstraint leftHandIk;
        [SerializeField] private TwoBoneIKConstraint rightHandIk;
        [SerializeField] private Transform leftHandTarget;
        [SerializeField] private Transform rightHandTarget;

        [Header("Phone slots")]
        [SerializeField] private Transform phoneGripTarget;
        [SerializeField] private Transform phonePlaceTarget;
        [SerializeField] private Vector3 tableGripOffset = Vector3.zero;
        [SerializeField] private Vector3 placeOffset = Vector3.zero;

        [Header("Timing")]
        [SerializeField] private bool usePickupFlow;
        [SerializeField] private float fallbackPickupSeconds = 0.9f;
        [SerializeField] private float fallbackPutDownSeconds = 0.9f;
        [SerializeField, Range(0.05f, 0.95f)] private float grabNormalizedTime = 0.58f;
        [SerializeField, Range(0.05f, 0.95f)] private float releaseNormalizedTime = 0.58f;
        [SerializeField] private bool releasePhoneOnlyFromAnimationEvent = true;
        [SerializeField] private bool driveHandIkDuringPickup;
        [SerializeField, Range(0f, 1f)] private float maxIkWeight = 0.92f;
        [SerializeField] private bool keepPhoneVisibleOnTable = true;

        private RelaxRoomPhoneGrip activeGrip = RelaxRoomPhoneGrip.ReplyTexting;
        private Coroutine activeRoutine;
        private bool referencesResolved;
        private bool phoneReleaseEventReceived;

        public bool UsePickupFlow => usePickupFlow && phoneAttachment != null;
        public bool IsPhoneHeld => phoneAttachment != null && phoneAttachment.IsAttachedToHand;

        private void Reset()
        {
            ResolveReferences();
        }

        private void Awake()
        {
            ResolveReferences();
            ResetAllPhoneIk();
        }

        public void SetTargets(
            Animator targetAnimator,
            AIGalgamePlayableMotionPlayer targetMotionPlayer,
            AIGalgameRelaxRoomPhoneAttachmentController targetPhoneAttachment)
        {
            animator = targetAnimator != null ? targetAnimator : animator;
            motionPlayer = targetMotionPlayer != null ? targetMotionPlayer : motionPlayer;
            phoneAttachment = targetPhoneAttachment != null ? targetPhoneAttachment : phoneAttachment;
            referencesResolved = false;
            ResolveReferences();
        }

        public void PlacePhoneOnTable()
        {
            ResolveReferences();
            StopActiveRoutine();
            ResetAllPhoneIk();
            phoneAttachment?.PlaceOnTable(keepPhoneVisibleOnTable);
        }

        public void EnsurePhoneInHand(RelaxRoomPhoneGrip grip)
        {
            ResolveReferences();
            activeGrip = grip;
            phoneAttachment?.AttachToHand(grip);
            phoneScreen?.ShowActiveScreen(0f, true);
        }

        public void SetActiveGrip(RelaxRoomPhoneGrip grip)
        {
            activeGrip = grip;
        }

        public IEnumerator PickupDuring(RelaxRoomPhoneGrip grip, float durationSeconds)
        {
            ResolveReferences();
            if (!UsePickupFlow)
            {
                phoneAttachment?.AttachToHand(grip);
                phoneScreen?.ShowActiveScreen(0f, true);
                yield break;
            }

            StopActiveRoutine();
            activeRoutine = StartCoroutine(PickupRoutine(grip, durationSeconds));
            yield return activeRoutine;
        }

        public IEnumerator PutDownDuring(RelaxRoomPhoneGrip grip, float durationSeconds)
        {
            ResolveReferences();
            if (!UsePickupFlow)
            {
                phoneAttachment?.PlaceOnTable(keepPhoneVisibleOnTable);
                phoneScreen?.ShowActiveScreen(5f, false);
                yield break;
            }

            StopActiveRoutine();
            activeRoutine = StartCoroutine(PutDownRoutine(grip, durationSeconds));
            yield return activeRoutine;
        }

        public void OnPhoneGrab()
        {
            ResolveReferences();
            phoneAttachment?.AttachToHand(activeGrip);
            phoneScreen?.ShowActiveScreen(0f, true);
        }

        public void OnPhoneRelease()
        {
            ResolveReferences();
            phoneReleaseEventReceived = true;
            ReleasePhoneToTable();
        }

        private IEnumerator PickupRoutine(RelaxRoomPhoneGrip grip, float durationSeconds)
        {
            activeGrip = grip;
            phoneAttachment.PlaceOnTable(keepPhoneVisibleOnTable);

            var hand = ResolveHand(grip);
            var start = GetCurrentHandPose(hand);
            var target = ResolvePhoneGripPose();
            var duration = Mathf.Max(0.15f, durationSeconds > 0f ? durationSeconds : fallbackPickupSeconds);
            var grabbed = false;
            var elapsed = 0f;

            while (elapsed < duration)
            {
                var t = Mathf.Clamp01(elapsed / duration);
                var weight = WeightForPickup(t);
                var aim = t < grabNormalizedTime
                    ? PoseLerp(start, target, Smooth01(t / Mathf.Max(0.01f, grabNormalizedTime)))
                    : PoseLerp(target, GetCurrentHandPose(hand), Smooth01((t - grabNormalizedTime) / Mathf.Max(0.01f, 1f - grabNormalizedTime)));

                if (driveHandIkDuringPickup)
                {
                    ApplyHandIk(hand, aim, weight);
                }

                if (!grabbed && t >= grabNormalizedTime)
                {
                    phoneAttachment.AttachToHand(grip);
                    phoneScreen?.ShowActiveScreen(0f, true);
                    grabbed = true;
                }

                elapsed += Time.deltaTime;
                yield return null;
            }

            if (!grabbed)
            {
                phoneAttachment.AttachToHand(grip);
                phoneScreen?.ShowActiveScreen(0f, true);
            }

            if (driveHandIkDuringPickup)
            {
                ResetHandIk(hand);
            }

            activeRoutine = null;
        }

        private IEnumerator PutDownRoutine(RelaxRoomPhoneGrip grip, float durationSeconds)
        {
            activeGrip = grip;
            phoneReleaseEventReceived = false;
            phoneAttachment.AttachToHand(grip);

            var hand = ResolveHand(grip);
            var start = GetCurrentHandPose(hand);
            var target = ResolvePhonePlacePose();
            var duration = Mathf.Max(0.15f, durationSeconds > 0f ? durationSeconds : fallbackPutDownSeconds);
            var released = false;
            var elapsed = 0f;

            while (elapsed < duration)
            {
                var t = Mathf.Clamp01(elapsed / duration);
                var weight = WeightForPutDown(t);
                var aim = t < releaseNormalizedTime
                    ? PoseLerp(start, target, Smooth01(t / Mathf.Max(0.01f, releaseNormalizedTime)))
                    : PoseLerp(target, GetCurrentHandPose(hand), Smooth01((t - releaseNormalizedTime) / Mathf.Max(0.01f, 1f - releaseNormalizedTime)));

                if (driveHandIkDuringPickup)
                {
                    ApplyHandIk(hand, aim, weight);
                }

                if (!released && phoneReleaseEventReceived)
                {
                    released = true;
                }
                else if (!released && !releasePhoneOnlyFromAnimationEvent && t >= releaseNormalizedTime)
                {
                    ReleasePhoneToTable();
                    released = true;
                }

                elapsed += Time.deltaTime;
                yield return null;
            }

            if (!released)
            {
                ReleasePhoneToTable();
            }

            if (driveHandIkDuringPickup)
            {
                ResetHandIk(hand);
            }

            activeRoutine = null;
        }

        private void StopActiveRoutine()
        {
            if (activeRoutine == null)
            {
                return;
            }

            StopCoroutine(activeRoutine);
            activeRoutine = null;
        }

        private PhoneHand ResolveHand(RelaxRoomPhoneGrip grip)
        {
            if (phoneAttachment == null)
            {
                return PhoneHand.Right;
            }

            return phoneAttachment.GetGripHandBone(grip) == HumanBodyBones.LeftHand
                ? PhoneHand.Left
                : PhoneHand.Right;
        }

        private Pose GetCurrentHandPose(PhoneHand hand)
        {
            var bone = GetHandBone(hand);
            if (bone != null)
            {
                return new Pose(bone.position, bone.rotation);
            }

            var target = GetIkTarget(hand);
            return target != null ? new Pose(target.position, target.rotation) : new Pose(transform.position, transform.rotation);
        }

        private Pose ResolvePhoneGripPose()
        {
            var target = phoneGripTarget != null ? phoneGripTarget : phoneAttachment != null ? phoneAttachment.PhoneInstance : null;
            if (target == null && phoneAttachment != null)
            {
                target = phoneAttachment.PhoneTableSlot;
            }

            if (target == null)
            {
                return new Pose(transform.position + transform.forward * 0.35f, transform.rotation);
            }

            return new Pose(target.position + target.TransformVector(tableGripOffset), target.rotation);
        }

        private Pose ResolvePhonePlacePose()
        {
            var target = phonePlaceTarget != null ? phonePlaceTarget : phoneAttachment != null ? phoneAttachment.PhoneTableSlot : null;
            if (target == null)
            {
                return ResolvePhoneGripPose();
            }

            return new Pose(target.position + target.TransformVector(placeOffset), target.rotation);
        }

        private void ApplyPlaceTargetAsTableSlot()
        {
            if (phonePlaceTarget != null)
            {
                phoneAttachment?.SetPhoneTableSlot(phonePlaceTarget);
            }
        }

        private void ReleasePhoneToTable()
        {
            ApplyPlaceTargetAsTableSlot();
            phoneAttachment?.PlaceOnTable(keepPhoneVisibleOnTable);
            phoneScreen?.ShowActiveScreen(5f, false);
        }

        private void ApplyHandIk(PhoneHand hand, Pose pose, float weight)
        {
            var clampedWeight = Mathf.Clamp01(weight) * Mathf.Clamp01(maxIkWeight);
            var target = GetIkTarget(hand);
            if (target != null)
            {
                target.SetPositionAndRotation(pose.position, pose.rotation);
            }

            var constraint = GetRigConstraint(hand);
            if (constraint != null)
            {
                constraint.weight = clampedWeight;
                var data = constraint.data;
                data.targetPositionWeight = 1f;
                data.targetRotationWeight = 1f;
                data.hintWeight = 1f;
                constraint.data = data;
            }

            var ikAdjuster = GetIkAdjuster();
            if (ikAdjuster != null)
            {
                ikAdjuster.SetWeight(ToMotionIkTarget(hand), clampedWeight);
                var control = ikAdjuster.GetControlTransform(ToMotionIkTarget(hand), MotionIkHandleKind.Effector);
                if (control != null)
                {
                    control.SetPositionAndRotation(pose.position, pose.rotation);
                }
            }
        }

        private void ResetHandIk(PhoneHand hand)
        {
            var constraint = GetRigConstraint(hand);
            if (constraint != null)
            {
                constraint.weight = 0f;
            }

            GetIkAdjuster()?.SetWeight(ToMotionIkTarget(hand), 0f);
        }

        private void ResetAllPhoneIk()
        {
            ResetHandIk(PhoneHand.Left);
            ResetHandIk(PhoneHand.Right);
        }

        private Transform GetIkTarget(PhoneHand hand)
        {
            ResolveReferences();
            if (hand == PhoneHand.Left)
            {
                return leftHandTarget != null ? leftHandTarget : GetIkAdjuster()?.GetControlTransform(MotionIkTarget.LeftHand, MotionIkHandleKind.Effector);
            }

            return rightHandTarget != null ? rightHandTarget : GetIkAdjuster()?.GetControlTransform(MotionIkTarget.RightHand, MotionIkHandleKind.Effector);
        }

        private AIGalgameHumanoidIKAdjuster GetIkAdjuster()
        {
            return motionPlayer != null ? motionPlayer.IkAdjuster : null;
        }

        private TwoBoneIKConstraint GetRigConstraint(PhoneHand hand)
        {
            ResolveReferences();
            return hand == PhoneHand.Left ? leftHandIk : rightHandIk;
        }

        private Transform GetHandBone(PhoneHand hand)
        {
            ResolveReferences();
            if (animator == null || animator.avatar == null || !animator.avatar.isHuman)
            {
                return null;
            }

            return animator.GetBoneTransform(hand == PhoneHand.Left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand);
        }

        private static MotionIkTarget ToMotionIkTarget(PhoneHand hand)
        {
            return hand == PhoneHand.Left ? MotionIkTarget.LeftHand : MotionIkTarget.RightHand;
        }

        private float WeightForPickup(float normalizedTime)
        {
            if (normalizedTime <= grabNormalizedTime)
            {
                return Smooth01(normalizedTime / Mathf.Max(0.01f, grabNormalizedTime));
            }

            return 1f - Smooth01((normalizedTime - grabNormalizedTime) / Mathf.Max(0.01f, 1f - grabNormalizedTime));
        }

        private float WeightForPutDown(float normalizedTime)
        {
            if (normalizedTime <= releaseNormalizedTime)
            {
                return Smooth01(normalizedTime / Mathf.Max(0.01f, releaseNormalizedTime));
            }

            return 1f - Smooth01((normalizedTime - releaseNormalizedTime) / Mathf.Max(0.01f, 1f - releaseNormalizedTime));
        }

        private void ResolveReferences()
        {
            if (referencesResolved)
            {
                return;
            }

            if (animator == null)
            {
                animator = GetComponent<Animator>() ?? GetComponentInChildren<Animator>() ?? GetComponentInParent<Animator>();
            }

            if (motionPlayer == null)
            {
                motionPlayer = GetComponent<AIGalgamePlayableMotionPlayer>() ??
                    GetComponentInChildren<AIGalgamePlayableMotionPlayer>() ??
                    GetComponentInParent<AIGalgamePlayableMotionPlayer>();
            }

            if (phoneAttachment == null)
            {
                phoneAttachment = GetComponent<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInParent<AIGalgameRelaxRoomPhoneAttachmentController>();
            }

            if (phoneScreen == null)
            {
                phoneScreen = GetComponent<AIGalgameRelaxRoomPhoneScreenController>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomPhoneScreenController>() ??
                    GetComponentInParent<AIGalgameRelaxRoomPhoneScreenController>();
            }

            ResolveAnimationRiggingTargets();
            referencesResolved = true;
        }

        private void ResolveAnimationRiggingTargets()
        {
            var constraints = GetComponentsInChildren<TwoBoneIKConstraint>(true);
            for (var i = 0; i < constraints.Length; i++)
            {
                var constraint = constraints[i];
                if (constraint == null)
                {
                    continue;
                }

                var data = constraint.data;
                if (leftHandIk == null && IsHandTip(data.tip, HumanBodyBones.LeftHand, "left"))
                {
                    leftHandIk = constraint;
                    leftHandTarget = leftHandTarget != null ? leftHandTarget : data.target;
                }
                else if (rightHandIk == null && IsHandTip(data.tip, HumanBodyBones.RightHand, "right"))
                {
                    rightHandIk = constraint;
                    rightHandTarget = rightHandTarget != null ? rightHandTarget : data.target;
                }
            }

            leftHandTarget = leftHandTarget != null ? leftHandTarget : FindChildTransform("LeftHand_Target");
            rightHandTarget = rightHandTarget != null ? rightHandTarget : FindChildTransform("RightHand_Target");
        }

        private bool IsHandTip(Transform tip, HumanBodyBones bone, string nameHint)
        {
            if (tip == null)
            {
                return false;
            }

            if (animator != null && animator.avatar != null && animator.avatar.isHuman && animator.GetBoneTransform(bone) == tip)
            {
                return true;
            }

            return tip.name.ToLowerInvariant().Contains(nameHint) && tip.name.ToLowerInvariant().Contains("hand");
        }

        private Transform FindChildTransform(string targetName)
        {
            var transforms = GetComponentsInChildren<Transform>(true);
            for (var i = 0; i < transforms.Length; i++)
            {
                if (transforms[i] != null && transforms[i].name == targetName)
                {
                    return transforms[i];
                }
            }

            return null;
        }

        private static Pose PoseLerp(Pose from, Pose to, float t)
        {
            t = Mathf.Clamp01(t);
            return new Pose(Vector3.Lerp(from.position, to.position, t), Quaternion.Slerp(from.rotation, to.rotation, t));
        }

        private static float Smooth01(float value)
        {
            value = Mathf.Clamp01(value);
            return value * value * (3f - (2f * value));
        }
    }
}
