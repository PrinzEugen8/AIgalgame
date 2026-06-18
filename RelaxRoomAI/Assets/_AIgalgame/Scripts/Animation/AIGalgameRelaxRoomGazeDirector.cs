using System;
using System.Linq;
using UniVRM10;
using UnityEngine;

namespace AIgalgame.Motion
{
    public enum RelaxRoomGazeFocus
    {
        Idle,
        User,
        Computer,
        Phone,
        Touch,
        Down,
        Away
    }

    [DefaultExecutionOrder(150)]
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomGazeDirector : MonoBehaviour
    {
        [Header("Targets")]
        [SerializeField] private Vrm10Instance vrmInstance;
        [SerializeField] private Animator animator;
        [SerializeField] private AIGalgameRelaxRoomMotionDirector motionDirector;
        [SerializeField] private AIGalgameRelaxRoomPhoneAttachmentController phoneAttachment;
        [SerializeField] private Transform userCameraTarget;
        [SerializeField] private Transform computerScreenTarget;
        [SerializeField] private Transform phoneScreenTarget;
        [SerializeField] private Transform touchTarget;
        [SerializeField] private Transform lookAtTargetFallback;

        [Header("Motion")]
        [SerializeField] private float gazeSmoothSeconds = 0.34f;
        [SerializeField] private float scanSmoothSeconds = 0.16f;
        [SerializeField] private float focusSettleScanDelay = 0.28f;
        [SerializeField] private Vector2 idleFocusIntervalRange = new(3.5f, 8.5f);
        [SerializeField] private Vector2 scanIntervalRange = new(0.75f, 2.2f);
        [SerializeField] private float screenScanRadius = 0.07f;
        [SerializeField] private float phoneScanRadius = 0.045f;
        [SerializeField] private float userSaccadeRadius = 0.022f;

        [Header("Optional pupil blendshapes")]
        [SerializeField] private float pupilSmoothSpeed = 3.8f;
        [SerializeField, Range(0f, 1f)] private float screenPupilConstrictWeight = 0.22f;
        [SerializeField, Range(0f, 1f)] private float userPupilDilateWeight = 0.08f;

        private Transform proxyTarget;
        private RelaxRoomGazeFocus focus = RelaxRoomGazeFocus.Idle;
        private RelaxRoomGazeFocus idleFocus = RelaxRoomGazeFocus.User;
        private string lastMotionKey = "";
        private float focusUntil;
        private float nextIdleFocusAt;
        private float nextScanAt;
        private Vector3 smoothVelocity;
        private Vector3 scanOffset;
        private Vector3 desiredScanOffset;
        private Vector3 scanOffsetVelocity;
        private Vector3 touchPoint;
        private bool hasTouchPoint;
        private float awaySide = 1f;
        private RelaxRoomGazeFocus lastEffectiveFocus = RelaxRoomGazeFocus.Idle;
        private ExpressionKey? pupilDilateKey;
        private ExpressionKey? pupilConstrictKey;
        private bool pupilKeysResolved;
        private float pupilDilateWeight;
        private float pupilConstrictWeight;

        public string CurrentFocusName => EffectiveFocus().ToString();

        private void Reset()
        {
            ResolveReferences();
        }

        private void Awake()
        {
            ResolveReferences();
            EnsureProxyTarget();
            nextIdleFocusAt = Time.time + 1f;
        }

        private void LateUpdate()
        {
            ResolveReferences();
            EnsureProxyTarget();
            if (proxyTarget == null)
            {
                return;
            }

            UpdateFocusExpiry();
            UpdateIdleFocus();
            var effectiveFocus = EffectiveFocus();
            UpdateFocusTransition(effectiveFocus);
            UpdateScanOffset(effectiveFocus);

            scanOffset = Vector3.SmoothDamp(scanOffset, desiredScanOffset, ref scanOffsetVelocity, Mathf.Max(0.02f, scanSmoothSeconds));
            var desired = ResolveFocusPosition(effectiveFocus);
            proxyTarget.position = Vector3.SmoothDamp(proxyTarget.position, desired, ref smoothVelocity, Mathf.Max(0.02f, gazeSmoothSeconds));

            if (vrmInstance != null)
            {
                vrmInstance.LookAtTargetType = VRM10ObjectLookAt.LookAtTargetTypes.SpecifiedTransform;
                vrmInstance.LookAtTarget = proxyTarget;
            }

            UpdateOptionalPupilBlendshapes();
        }

        public void SetTargets(Vrm10Instance targetVrm, Animator targetAnimator, AIGalgameRelaxRoomMotionDirector targetMotionDirector = null)
        {
            if (targetVrm != null)
            {
                vrmInstance = targetVrm;
            }

            if (targetAnimator != null)
            {
                animator = targetAnimator;
            }

            if (targetMotionDirector != null)
            {
                motionDirector = targetMotionDirector;
            }
        }

        public void SetAvatarState(AIGalgameAvatarState state, string motionName = "")
        {
            lastMotionKey = NormalizeToken(motionName);
            switch (state)
            {
                case AIGalgameAvatarState.Typing:
                    SetFocus(RelaxRoomGazeFocus.Computer, 0f);
                    break;
                case AIGalgameAvatarState.Speaking:
                    SetFocus(RelaxRoomGazeFocus.User, 0f);
                    break;
                default:
                    SetFocus(FocusForMotion(lastMotionKey), 0f);
                    break;
            }
        }

        public void SetMotionMode(RelaxRoomMotionMode mode, string motionName = "")
        {
            lastMotionKey = NormalizeToken(motionName);
            switch (mode)
            {
                case RelaxRoomMotionMode.Reply:
                    SetFocus(RelaxRoomGazeFocus.Phone, 0f);
                    break;
                case RelaxRoomMotionMode.Speaking:
                    SetFocus(RelaxRoomGazeFocus.User, 0f);
                    break;
                case RelaxRoomMotionMode.Touch:
                    SetFocus(RelaxRoomGazeFocus.Touch, 1.6f);
                    break;
                case RelaxRoomMotionMode.IdleAction:
                    SetFocus(FocusForMotion(lastMotionKey), 0f);
                    break;
                default:
                    SetFocus(RelaxRoomGazeFocus.Idle, 0f);
                    break;
            }
        }

        public void SetFocus(string focusName, float holdSeconds = 0f)
        {
            SetFocus(ParseFocus(focusName), holdSeconds);
        }

        public void SetFocus(RelaxRoomGazeFocus nextFocus, float holdSeconds = 0f)
        {
            focus = nextFocus;
            focusUntil = holdSeconds > 0f ? Time.time + holdSeconds : 0f;
            desiredScanOffset = Vector3.zero;
            nextScanAt = Time.time + Mathf.Max(0f, focusSettleScanDelay);
        }

        public void SetTouchPoint(Vector3 worldPosition, float holdSeconds = 1.8f)
        {
            touchPoint = worldPosition;
            hasTouchPoint = true;
            SetFocus(RelaxRoomGazeFocus.Touch, holdSeconds);
        }

        private void UpdateFocusExpiry()
        {
            if (focusUntil > 0f && Time.time >= focusUntil)
            {
                focusUntil = 0f;
                focus = string.IsNullOrWhiteSpace(lastMotionKey) ? RelaxRoomGazeFocus.Idle : FocusForMotion(lastMotionKey);
                hasTouchPoint = false;
            }
        }

        private void UpdateIdleFocus()
        {
            if (focus != RelaxRoomGazeFocus.Idle || Time.time < nextIdleFocusAt)
            {
                return;
            }

            var roll = UnityEngine.Random.value;
            idleFocus = roll < 0.58f
                ? RelaxRoomGazeFocus.User
                : roll < 0.76f
                    ? RelaxRoomGazeFocus.Computer
                    : roll < 0.9f
                        ? RelaxRoomGazeFocus.Down
                        : RelaxRoomGazeFocus.Away;
            nextIdleFocusAt = Time.time + RandomRange(idleFocusIntervalRange, 3.5f, 8.5f);
            nextScanAt = 0f;
        }

        private RelaxRoomGazeFocus EffectiveFocus()
        {
            return focus == RelaxRoomGazeFocus.Idle ? idleFocus : focus;
        }

        private void UpdateFocusTransition(RelaxRoomGazeFocus effective)
        {
            if (effective == lastEffectiveFocus)
            {
                return;
            }

            lastEffectiveFocus = effective;
            smoothVelocity = Vector3.zero;
            desiredScanOffset = Vector3.zero;
            nextScanAt = Time.time + Mathf.Max(0f, focusSettleScanDelay);
            if (effective == RelaxRoomGazeFocus.Away)
            {
                awaySide = UnityEngine.Random.value < 0.5f ? -1f : 1f;
            }
        }

        private void UpdateScanOffset(RelaxRoomGazeFocus effective)
        {
            if (Time.time < nextScanAt)
            {
                return;
            }

            var radius = effective switch
            {
                RelaxRoomGazeFocus.Computer => screenScanRadius,
                RelaxRoomGazeFocus.Phone => phoneScanRadius,
                RelaxRoomGazeFocus.User => userSaccadeRadius,
                _ => userSaccadeRadius * 0.65f
            };
            desiredScanOffset = CameraAlignedOffset(UnityEngine.Random.Range(-radius, radius), UnityEngine.Random.Range(-radius * 0.65f, radius * 0.65f));
            nextScanAt = Time.time + RandomRange(scanIntervalRange, 0.75f, 2.2f);
        }

        private Vector3 ResolveFocusPosition(RelaxRoomGazeFocus targetFocus)
        {
            var head = GetHead();
            switch (targetFocus)
            {
                case RelaxRoomGazeFocus.Computer:
                    var screen = computerScreenTarget != null ? computerScreenTarget : FindBestSceneTransform("screen", "monitor", "computer");
                    computerScreenTarget = screen != null ? screen : computerScreenTarget;
                    return (screen != null ? screen.position : FallbackForwardPoint(head, 1.6f, 0.05f)) + scanOffset;
                case RelaxRoomGazeFocus.Phone:
                    var phone = ResolvePhoneTarget();
                    return (phone != null ? phone.position : FallbackForwardPoint(head, 0.55f, -0.25f)) + scanOffset;
                case RelaxRoomGazeFocus.Touch:
                    if (hasTouchPoint)
                    {
                        return touchPoint + scanOffset;
                    }

                    return FallbackForwardPoint(head, 0.75f, -0.05f) + scanOffset;
                case RelaxRoomGazeFocus.Down:
                    return FallbackForwardPoint(head, 0.85f, -0.45f) + scanOffset;
                case RelaxRoomGazeFocus.Away:
                    return FallbackSidePoint(head, 1.25f, awaySide * 0.5f) + scanOffset;
                case RelaxRoomGazeFocus.User:
                default:
                    var user = ResolveUserTarget();
                    return (user != null ? user.position : FallbackForwardPoint(head, 1.6f, 0.08f)) + scanOffset;
            }
        }

        private Transform ResolveUserTarget()
        {
            if (userCameraTarget != null)
            {
                return userCameraTarget;
            }

            var camera = Camera.main;
            if (camera != null)
            {
                userCameraTarget = camera.transform;
                return userCameraTarget;
            }

            lookAtTargetFallback = lookAtTargetFallback != null ? lookAtTargetFallback : FindBestSceneTransform("lookattarget", "lookat");
            return lookAtTargetFallback;
        }

        private Transform ResolvePhoneTarget()
        {
            if (phoneAttachment == null)
            {
                phoneAttachment = GetComponent<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInParent<AIGalgameRelaxRoomPhoneAttachmentController>();
            }

            if (phoneAttachment != null && phoneAttachment.PhoneInstance != null)
            {
                phoneScreenTarget = FindPhoneScreenTarget(phoneAttachment.PhoneInstance);
                return phoneScreenTarget;
            }

            if (phoneScreenTarget != null && phoneScreenTarget.gameObject.activeInHierarchy)
            {
                return phoneScreenTarget;
            }

            return FindBestSceneTransform("phone", "手机");
        }

        private static Transform FindPhoneScreenTarget(Transform phoneRoot)
        {
            if (phoneRoot == null || !phoneRoot.gameObject.activeInHierarchy)
            {
                return null;
            }

            var renderers = phoneRoot.GetComponentsInChildren<Renderer>(true);
            foreach (var renderer in renderers)
            {
                if (renderer == null)
                {
                    continue;
                }

                var materials = renderer.sharedMaterials;
                for (var i = 0; i < materials.Length; i++)
                {
                    var materialName = NormalizeToken(materials[i] != null ? materials[i].name : "");
                    if (materialName.Contains("material010") || materialName.Contains("screen"))
                    {
                        return renderer.transform;
                    }
                }
            }

            var transforms = phoneRoot.GetComponentsInChildren<Transform>(true);
            foreach (var candidate in transforms)
            {
                var name = NormalizeToken(candidate != null ? candidate.name : "");
                if (name.Contains("screen") || name.Contains("display"))
                {
                    return candidate;
                }
            }

            return phoneRoot;
        }

        private Transform FindBestSceneTransform(params string[] keywords)
        {
            var normalizedKeywords = keywords.Select(NormalizeToken).Where(item => !string.IsNullOrWhiteSpace(item)).ToArray();
            if (normalizedKeywords.Length == 0)
            {
                return null;
            }

            var origin = GetHead() != null ? GetHead().position : transform.position;
            Transform best = null;
            var bestScore = float.MinValue;
            var transforms = FindObjectsOfType<Transform>();
            foreach (var candidate in transforms)
            {
                if (candidate == null || !candidate.gameObject.activeInHierarchy)
                {
                    continue;
                }

                var name = NormalizeToken(candidate.name);
                var score = 0f;
                foreach (var keyword in normalizedKeywords)
                {
                    if (name == keyword)
                    {
                        score += 12f;
                    }
                    else if (name.Contains(keyword))
                    {
                        score += 5f;
                    }
                }

                if (score <= 0f)
                {
                    continue;
                }

                score -= Vector3.Distance(origin, candidate.position) * 0.15f;
                if (score > bestScore)
                {
                    bestScore = score;
                    best = candidate;
                }
            }

            return best;
        }

        private Transform GetHead()
        {
            if (animator != null && animator.avatar != null && animator.avatar.isHuman)
            {
                var head = animator.GetBoneTransform(HumanBodyBones.Head);
                if (head != null)
                {
                    return head;
                }
            }

            return vrmInstance != null ? vrmInstance.transform : transform;
        }

        private Vector3 FallbackForwardPoint(Transform head, float distance, float verticalOffset)
        {
            var basis = Camera.main != null ? Camera.main.transform : transform;
            var source = head != null ? head.position : transform.position + Vector3.up * 1.4f;
            return source + basis.forward * distance + Vector3.up * verticalOffset;
        }

        private Vector3 FallbackSidePoint(Transform head, float distance, float horizontalOffset)
        {
            var basis = Camera.main != null ? Camera.main.transform : transform;
            var source = head != null ? head.position : transform.position + Vector3.up * 1.4f;
            return source + basis.forward * distance + basis.right * horizontalOffset + Vector3.up * 0.02f;
        }

        private Vector3 CameraAlignedOffset(float horizontal, float vertical)
        {
            var camera = Camera.main;
            if (camera == null)
            {
                return (Vector3.right * horizontal) + (Vector3.up * vertical);
            }

            return (camera.transform.right * horizontal) + (camera.transform.up * vertical);
        }

        private RelaxRoomGazeFocus FocusForMotion(string motionKey)
        {
            if (motionKey.Contains("typing") || motionKey == "type" || motionKey == "input")
            {
                return RelaxRoomGazeFocus.Computer;
            }

            if (motionKey.Contains("phone") || motionKey.Contains("texting") || motionKey.Contains("tablet") || motionKey.Contains("touchscreen"))
            {
                return RelaxRoomGazeFocus.Phone;
            }

            if (motionKey.Contains("waking") || motionKey.Contains("sleep") || motionKey.Contains("doz") || motionKey.Contains("yawn"))
            {
                return RelaxRoomGazeFocus.Down;
            }

            return RelaxRoomGazeFocus.Idle;
        }

        private RelaxRoomGazeFocus ParseFocus(string focusName)
        {
            switch (NormalizeToken(focusName))
            {
                case "camera":
                case "maincamera":
                case "user":
                case "viewer":
                    return RelaxRoomGazeFocus.User;
                case "computer":
                case "screen":
                case "monitor":
                    return RelaxRoomGazeFocus.Computer;
                case "phone":
                case "tablet":
                case "mobile":
                    return RelaxRoomGazeFocus.Phone;
                case "touch":
                    return RelaxRoomGazeFocus.Touch;
                case "down":
                    return RelaxRoomGazeFocus.Down;
                case "away":
                case "lookaway":
                    return RelaxRoomGazeFocus.Away;
                default:
                    return RelaxRoomGazeFocus.Idle;
            }
        }

        private void UpdateOptionalPupilBlendshapes()
        {
            if (vrmInstance == null || vrmInstance.Runtime?.Expression == null)
            {
                return;
            }

            ResolvePupilKeys();
            var targetDilate = EffectiveFocus() == RelaxRoomGazeFocus.User ? userPupilDilateWeight : 0f;
            var targetConstrict = EffectiveFocus() == RelaxRoomGazeFocus.Computer || EffectiveFocus() == RelaxRoomGazeFocus.Phone
                ? screenPupilConstrictWeight
                : 0f;
            pupilDilateWeight = Mathf.MoveTowards(pupilDilateWeight, targetDilate, Time.deltaTime * pupilSmoothSpeed);
            pupilConstrictWeight = Mathf.MoveTowards(pupilConstrictWeight, targetConstrict, Time.deltaTime * pupilSmoothSpeed);

            if (pupilDilateKey.HasValue)
            {
                vrmInstance.Runtime.Expression.SetWeight(pupilDilateKey.Value, pupilDilateWeight);
            }

            if (pupilConstrictKey.HasValue)
            {
                vrmInstance.Runtime.Expression.SetWeight(pupilConstrictKey.Value, pupilConstrictWeight);
            }
        }

        private void ResolvePupilKeys()
        {
            if (pupilKeysResolved || vrmInstance == null || vrmInstance.Runtime?.Expression == null)
            {
                return;
            }

            pupilKeysResolved = true;
            foreach (var key in vrmInstance.Runtime.Expression.ExpressionKeys)
            {
                if (key.IsProcedual)
                {
                    continue;
                }

                var normalized = NormalizeToken(key.Name);
                if (!pupilDilateKey.HasValue && (normalized.Contains("pupildilate") || normalized.Contains("pupillarge") || normalized.Contains("瞳孔大") || normalized.Contains("瞳孔放大")))
                {
                    pupilDilateKey = key;
                }

                if (!pupilConstrictKey.HasValue && (normalized.Contains("pupilsmall") || normalized.Contains("pupilconstrict") || normalized.Contains("瞳孔小") || normalized.Contains("瞳孔收缩")))
                {
                    pupilConstrictKey = key;
                }
            }
        }

        private void EnsureProxyTarget()
        {
            if (proxyTarget != null)
            {
                return;
            }

            var existing = transform.Find("AIgalgame_GazeProxy");
            if (existing != null)
            {
                proxyTarget = existing;
                return;
            }

            var proxy = new GameObject("AIgalgame_GazeProxy");
            proxy.hideFlags = HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild;
            proxy.transform.SetParent(transform, false);
            proxy.transform.position = ResolveFocusPosition(RelaxRoomGazeFocus.User);
            proxyTarget = proxy.transform;
        }

        private void ResolveReferences()
        {
            if (animator == null)
            {
                animator = GetComponent<Animator>() ?? GetComponentInChildren<Animator>() ?? GetComponentInParent<Animator>();
            }

            if (vrmInstance == null)
            {
                vrmInstance = GetComponent<Vrm10Instance>() ?? GetComponentInChildren<Vrm10Instance>() ?? GetComponentInParent<Vrm10Instance>();
            }

            if (motionDirector == null)
            {
                motionDirector = GetComponent<AIGalgameRelaxRoomMotionDirector>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomMotionDirector>() ??
                    GetComponentInParent<AIGalgameRelaxRoomMotionDirector>();
            }

            if (phoneAttachment == null)
            {
                phoneAttachment = GetComponent<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInParent<AIGalgameRelaxRoomPhoneAttachmentController>();
            }
        }

        private static float RandomRange(Vector2 range, float fallbackMin, float fallbackMax)
        {
            var min = range.x > 0f ? range.x : fallbackMin;
            var max = range.y >= min ? range.y : fallbackMax;
            return UnityEngine.Random.Range(min, max);
        }

        private static string NormalizeToken(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "";
            }

            return new string(value.Where(char.IsLetterOrDigit).Select(char.ToLowerInvariant).ToArray());
        }
    }
}
