using System;
using UnityEngine;

namespace AIgalgame.Motion
{
    public enum RelaxRoomPhoneGrip
    {
        Default,
        ReplyTexting,
        ReplyWaiting,
        ReplyDoubleTyping,
        IdleTablet,
        IdleTexting,
        VideoCallSelfie
    }

    public enum RelaxRoomPhonePlacement
    {
        Hidden,
        Table,
        Hand,
        Bedroom
    }

    [DefaultExecutionOrder(-111)]
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomPhoneAttachmentController : MonoBehaviour
    {
        [Serializable]
        public sealed class PhoneAttachmentPreset
        {
            public HumanBodyBones handBone = HumanBodyBones.RightHand;
            public Vector3 localPosition = Vector3.zero;
            public Vector3 localEulerAngles = Vector3.zero;
            public Vector3 localScale = Vector3.one;

            public PhoneAttachmentPreset()
            {
            }

            public PhoneAttachmentPreset(HumanBodyBones bone)
            {
                handBone = bone;
            }
        }

        private const string EditorPhoneAssetPath = "Assets/Item/\u624b\u673a.fbx";
        private const string AutoPhoneTableSlotName = "RelaxRoom Phone TableSlot";
        private const string BedroomPhoneSlotName = "BedRoomPhoneSlot";

        [Header("Targets")]
        [SerializeField] private Animator animator;

        [Header("Phone Prop")]
        [SerializeField] private GameObject phonePrefab;
        [SerializeField] private Transform phoneInstance;
        [SerializeField] private bool autoLoadEditorPhonePrefab = true;
        [SerializeField] private bool autoFindScenePhoneInstance = true;

        [Header("Table Slot")]
        [SerializeField] private Transform phoneTableSlot;
        [SerializeField] private bool keepVisibleOnTable = true;

        [Header("Bedroom Slot")]
        [SerializeField] private Transform bedroomPhoneSlot;
        [SerializeField] private bool keepVisibleOnBedroomSlot = true;
        [SerializeField] private bool hideDuplicatePhonePropsInSlots = true;

        [Header("Preview")]
        [SerializeField] private RelaxRoomPhoneGrip previewPhoneGrip = RelaxRoomPhoneGrip.ReplyTexting;

        [Header("Grip Presets")]
        [SerializeField] private PhoneAttachmentPreset defaultPhoneAttachment = new(HumanBodyBones.RightHand);
        [SerializeField] private PhoneAttachmentPreset replyTextingPhoneAttachment = new(HumanBodyBones.RightHand);
        [SerializeField] private PhoneAttachmentPreset replyWaitingPhoneAttachment = new(HumanBodyBones.RightHand);
        [SerializeField] private PhoneAttachmentPreset replyDoubleTypingPhoneAttachment = new(HumanBodyBones.RightHand);
        [SerializeField] private PhoneAttachmentPreset idleTabletPhoneAttachment = new(HumanBodyBones.RightHand);
        [SerializeField] private PhoneAttachmentPreset idleTextingPhoneAttachment = new(HumanBodyBones.RightHand);
        [SerializeField] private PhoneAttachmentPreset videoCallSelfiePhoneAttachment = new(HumanBodyBones.RightHand);

        [HideInInspector] [SerializeField] private HumanBodyBones phoneHandBone = HumanBodyBones.RightHand;
        [HideInInspector] [SerializeField] private Vector3 phoneLocalPosition = Vector3.zero;
        [HideInInspector] [SerializeField] private Vector3 phoneLocalEulerAngles = Vector3.zero;
        [HideInInspector] [SerializeField] private Vector3 phoneLocalScale = Vector3.one;

        private bool visible;
        private RelaxRoomPhoneGrip currentGrip = RelaxRoomPhoneGrip.Default;
        private RelaxRoomPhonePlacement placement = RelaxRoomPhonePlacement.Hidden;
        private bool loggedMissingPhoneInstance;
        private Vector3 tableLocalPosition;
        private Quaternion tableLocalRotation = Quaternion.identity;
        private bool hasTableLocalPose;
        private Vector3 bedroomLocalPosition;
        private Quaternion bedroomLocalRotation = Quaternion.identity;
        private bool hasBedroomLocalPose;

        public bool IsVisible => visible;
        public Transform PhoneInstance => phoneInstance;
        public Transform PhoneTableSlot => EnsurePhoneTableSlot();
        public RelaxRoomPhoneGrip CurrentGrip => currentGrip;
        public RelaxRoomPhonePlacement Placement => placement;
        public bool IsAttachedToHand => placement == RelaxRoomPhonePlacement.Hand;
        public bool IsOnTable => placement == RelaxRoomPhonePlacement.Table;
        public bool IsInBedroomSlot => placement == RelaxRoomPhonePlacement.Bedroom;
        public Animator Animator => animator;

#if UNITY_EDITOR
        public Transform EditorPhoneInstance => phoneInstance;

        public void EditorShowPhoneGrip(int gripIndex)
        {
            var grip = Enum.IsDefined(typeof(RelaxRoomPhoneGrip), gripIndex)
                ? (RelaxRoomPhoneGrip)gripIndex
                : RelaxRoomPhoneGrip.Default;
            Show(grip);
        }

        public void EditorHidePhoneGrip()
        {
            PlaceOnTable(true);
        }
#endif

        private void Reset()
        {
            ResolveReferences();
            EnsurePhoneAttachmentDefaults();
            TryLoadEditorPhonePrefab();
            TryFindScenePhoneInstance();
            EnsurePhoneTableSlot();
            KeepPhoneVisibleOnTableIfNeeded();
            HideDuplicatePhonePropsInKnownSlots();
        }

#if UNITY_EDITOR
        private void OnValidate()
        {
            EnsurePhoneAttachmentDefaults();
            TryLoadEditorPhonePrefab();
            TryFindScenePhoneInstance();
            EnsurePhoneTableSlot();
            KeepPhoneVisibleOnTableIfNeeded();
            HideDuplicatePhonePropsInKnownSlots();
            if (Application.isPlaying && visible)
            {
                ApplyAttachment();
            }
        }
#endif

        private void Awake()
        {
            ResolveReferences();
            EnsurePhoneAttachmentDefaults();
            TryLoadEditorPhonePrefab();
            TryFindScenePhoneInstance();
            EnsurePhoneTableSlot();
            KeepPhoneVisibleOnTableIfNeeded();
            HideDuplicatePhonePropsInKnownSlots();
        }

        private void LateUpdate()
        {
            if (visible)
            {
                ApplyAttachment();
            }
        }

        public void SetAnimator(Animator targetAnimator)
        {
            animator = targetAnimator;
        }

        public void SetPhoneTableSlot(Transform slot)
        {
            if (slot != null)
            {
                phoneTableSlot = slot;
                HideDuplicatePhonePropsInKnownSlots();
            }
        }

        public void SetBedroomPhoneSlot(Transform slot)
        {
            if (slot != null)
            {
                bedroomPhoneSlot = slot;
                HideDuplicatePhonePropsInKnownSlots();
            }
        }

        public void Show(RelaxRoomPhoneGrip grip = RelaxRoomPhoneGrip.Default)
        {
            AttachToHand(grip);
        }

        public void AttachToHand(RelaxRoomPhoneGrip grip = RelaxRoomPhoneGrip.Default)
        {
            var phone = ResolvePhoneInstance();
            if (phone == null)
            {
                if (!loggedMissingPhoneInstance)
                {
                    Debug.LogWarning("RelaxRoom phone prop is not assigned. Create a phone child on the character and assign it to Phone Instance.", this);
                    loggedMissingPhoneInstance = true;
                }

                return;
            }

            CacheTablePoseIfPhoneOnTable(phone);
            visible = true;
            placement = RelaxRoomPhonePlacement.Hand;
            currentGrip = grip;
            phone.gameObject.SetActive(true);
            ApplyAttachment();
        }

        public void PlaceOnTable(bool visibleOnTable = true)
        {
            visible = visibleOnTable || keepVisibleOnTable;
            placement = RelaxRoomPhonePlacement.Table;

            var phone = ResolvePhoneInstance();
            if (phone == null)
            {
                return;
            }

            phone.gameObject.SetActive(visible);
            ApplyAttachment();
        }

        public void PlaceOnBedroomSlot(bool visibleOnSlot = true)
        {
            visible = visibleOnSlot || keepVisibleOnBedroomSlot;
            placement = RelaxRoomPhonePlacement.Bedroom;

            var phone = ResolvePhoneInstance();
            if (phone == null)
            {
                return;
            }

            phone.gameObject.SetActive(visible);
            ApplyAttachment();
        }

        public void Hide()
        {
            visible = false;
            placement = RelaxRoomPhonePlacement.Hidden;
            if (phoneInstance != null)
            {
                phoneInstance.gameObject.SetActive(false);
            }
        }

        public void RefreshAttachment()
        {
            if (visible)
            {
                ApplyAttachment();
            }
        }

        [ContextMenu("RelaxRoom/Show Phone Prop")]
        private void ContextShowPhoneProp()
        {
            Show(previewPhoneGrip);
        }

        [ContextMenu("RelaxRoom/Hide Phone Prop")]
        private void ContextHidePhoneProp()
        {
            PlaceOnTable(true);
        }

        [ContextMenu("RelaxRoom/Refresh Phone Attachment")]
        private void ContextRefreshPhoneAttachment()
        {
            ApplyAttachment();
        }

        private void ApplyAttachment()
        {
            var phone = ResolvePhoneInstance();
            if (phone == null)
            {
                return;
            }

            if (placement == RelaxRoomPhonePlacement.Table)
            {
                ApplyTableAttachment(phone);
                HideDuplicatePhonePropsInKnownSlots();
                return;
            }

            if (placement == RelaxRoomPhonePlacement.Bedroom)
            {
                ApplyBedroomAttachment(phone);
                HideDuplicatePhonePropsInKnownSlots();
                return;
            }

            ApplyHandAttachment(phone);
            HideDuplicatePhonePropsInKnownSlots();
        }

        private void KeepPhoneVisibleOnTableIfNeeded()
        {
            if (!keepVisibleOnTable || placement == RelaxRoomPhonePlacement.Hand || phoneInstance == null)
            {
                return;
            }

            visible = true;
            placement = RelaxRoomPhonePlacement.Table;
            phoneInstance.gameObject.SetActive(true);
            ApplyTableAttachment(phoneInstance);
        }

        private void ApplyHandAttachment(Transform phone)
        {
            var preset = GetPhoneAttachmentPreset(currentGrip);
            var socket = GetHandSocket(currentGrip);
            var parent = socket != null ? socket : GetPhoneParent(preset);
            if (phone.parent != parent)
            {
                phone.SetParent(parent, false);
            }

            if (socket != null)
            {
                phone.localPosition = Vector3.zero;
                phone.localRotation = Quaternion.identity;
                phone.localScale = Vector3.one;
                return;
            }

            phone.localPosition = preset.localPosition;
            phone.localRotation = Quaternion.Euler(preset.localEulerAngles);
            phone.localScale = preset.localScale;
        }

        private void ApplyTableAttachment(Transform phone)
        {
            var slot = EnsurePhoneTableSlot();
            if (slot == null)
            {
                return;
            }

            if (phone.parent != slot)
            {
                phone.SetParent(slot, false);
                if (hasTableLocalPose)
                {
                    phone.localPosition = tableLocalPosition;
                    phone.localRotation = tableLocalRotation;
                }
                else
                {
                    phone.localPosition = Vector3.zero;
                    phone.localRotation = Quaternion.identity;
                }
            }
            else
            {
                CacheTablePoseIfPhoneOnTable(phone);
            }

            phone.localScale = Vector3.one;
            HideDuplicatePhonePropsInKnownSlots();
        }

        private void ApplyBedroomAttachment(Transform phone)
        {
            var slot = EnsureBedroomPhoneSlot();
            if (slot == null)
            {
                ApplyTableAttachment(phone);
                return;
            }

            if (phone.parent != slot)
            {
                phone.SetParent(slot, false);
                if (hasBedroomLocalPose)
                {
                    phone.localPosition = bedroomLocalPosition;
                    phone.localRotation = bedroomLocalRotation;
                }
                else
                {
                    phone.localPosition = Vector3.zero;
                    phone.localRotation = Quaternion.identity;
                }
            }
            else
            {
                CacheBedroomPoseIfPhoneOnBedroomSlot(phone);
            }

            phone.localScale = Vector3.one;
            HideDuplicatePhonePropsInKnownSlots();
        }

        private void CacheTablePoseIfPhoneOnTable(Transform phone)
        {
            var slot = EnsurePhoneTableSlot();
            if (phone == null || slot == null || phone.parent != slot)
            {
                return;
            }

            tableLocalPosition = phone.localPosition;
            tableLocalRotation = phone.localRotation;
            hasTableLocalPose = true;
        }

        private void CacheBedroomPoseIfPhoneOnBedroomSlot(Transform phone)
        {
            var slot = EnsureBedroomPhoneSlot();
            if (phone == null || slot == null || phone.parent != slot)
            {
                return;
            }

            bedroomLocalPosition = phone.localPosition;
            bedroomLocalRotation = phone.localRotation;
            hasBedroomLocalPose = true;
        }

        public Transform GetPhoneParent(PhoneAttachmentPreset preset = null)
        {
            ResolveReferences();
            if (animator != null && animator.avatar != null && animator.avatar.isHuman)
            {
                var hand = animator.GetBoneTransform((preset ?? defaultPhoneAttachment).handBone);
                if (hand != null)
                {
                    return hand;
                }
            }

            return transform;
        }

        public Transform GetPhoneParent(RelaxRoomPhoneGrip grip)
        {
            return GetPhoneParent(GetPhoneAttachmentPreset(grip));
        }

        public Transform GetHandSocket(RelaxRoomPhoneGrip grip)
        {
            var parent = GetPhoneParent(grip);
            if (parent == null)
            {
                return null;
            }

            var socketName = GetPhoneAttachmentPreset(grip).handBone == HumanBodyBones.LeftHand
                ? "LeftHand_Socket"
                : "RightHand_Socket";
            var socket = FindChildRecursive(parent, socketName);
            return socket != null ? socket : parent;
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

        public HumanBodyBones GetGripHandBone(RelaxRoomPhoneGrip grip)
        {
            return GetPhoneAttachmentPreset(grip).handBone;
        }

        public PhoneAttachmentPreset GetPhoneAttachmentPreset(RelaxRoomPhoneGrip grip)
        {
            EnsurePhoneAttachmentDefaults();
            return grip switch
            {
                RelaxRoomPhoneGrip.ReplyTexting => replyTextingPhoneAttachment,
                RelaxRoomPhoneGrip.ReplyWaiting => replyWaitingPhoneAttachment,
                RelaxRoomPhoneGrip.ReplyDoubleTyping => replyDoubleTypingPhoneAttachment,
                RelaxRoomPhoneGrip.IdleTablet => idleTabletPhoneAttachment,
                RelaxRoomPhoneGrip.IdleTexting => idleTextingPhoneAttachment,
                RelaxRoomPhoneGrip.VideoCallSelfie => videoCallSelfiePhoneAttachment,
                _ => defaultPhoneAttachment,
            };
        }

        public Transform ResolvePhoneInstance()
        {
            if (phoneInstance == null)
            {
                TryFindScenePhoneInstance();
            }

            if (phoneInstance == null && phonePrefab != null && EnsurePhoneTableSlot() != null)
            {
                var instance = Instantiate(phonePrefab, phoneTableSlot);
                instance.name = "RelaxRoom Phone Prop";
                instance.transform.localPosition = Vector3.zero;
                instance.transform.localRotation = Quaternion.identity;
                instance.transform.localScale = Vector3.one;
                phoneInstance = instance.transform;
            }

            if (phoneInstance != null)
            {
                loggedMissingPhoneInstance = false;
            }

            return phoneInstance;
        }

        private void ResolveReferences()
        {
            if (animator == null)
            {
                animator = GetComponent<Animator>() ?? GetComponentInChildren<Animator>() ?? GetComponentInParent<Animator>();
            }
        }

        private void EnsurePhoneAttachmentDefaults()
        {
            if (defaultPhoneAttachment == null)
            {
                defaultPhoneAttachment = new PhoneAttachmentPreset(phoneHandBone)
                {
                    localPosition = phoneLocalPosition,
                    localEulerAngles = phoneLocalEulerAngles,
                    localScale = phoneLocalScale,
                };
            }

            replyTextingPhoneAttachment ??= new PhoneAttachmentPreset(HumanBodyBones.RightHand);
            replyWaitingPhoneAttachment ??= new PhoneAttachmentPreset(HumanBodyBones.RightHand);
            replyDoubleTypingPhoneAttachment ??= new PhoneAttachmentPreset(HumanBodyBones.LeftHand);
            idleTabletPhoneAttachment ??= new PhoneAttachmentPreset(HumanBodyBones.RightHand);
            idleTextingPhoneAttachment ??= new PhoneAttachmentPreset(HumanBodyBones.RightHand);
            videoCallSelfiePhoneAttachment ??= new PhoneAttachmentPreset(HumanBodyBones.RightHand);

            EnsurePhoneScale(defaultPhoneAttachment);
            EnsurePhoneScale(replyTextingPhoneAttachment);
            EnsurePhoneScale(replyWaitingPhoneAttachment);
            EnsurePhoneScale(replyDoubleTypingPhoneAttachment);
            EnsurePhoneScale(idleTabletPhoneAttachment);
            EnsurePhoneScale(idleTextingPhoneAttachment);
            EnsurePhoneScale(videoCallSelfiePhoneAttachment);
        }

        private static void EnsurePhoneScale(PhoneAttachmentPreset preset)
        {
            if (preset != null && preset.localScale == Vector3.zero)
            {
                preset.localScale = Vector3.one;
            }
        }

        private void TryLoadEditorPhonePrefab()
        {
#if UNITY_EDITOR
            if (autoLoadEditorPhonePrefab && phonePrefab == null)
            {
                phonePrefab = UnityEditor.AssetDatabase.LoadAssetAtPath<GameObject>(EditorPhoneAssetPath);
            }
#endif
        }

        private void TryFindScenePhoneInstance()
        {
            if (!autoFindScenePhoneInstance || phoneInstance != null)
            {
                return;
            }

            phoneInstance = FindPhoneInPreferredSlot();
            if (phoneInstance == null)
            {
                phoneInstance = FindPhoneNearThisController();
            }

            if (phoneInstance == null)
            {
                phoneInstance = FindSceneTransformByName("RelaxRoom Phone Prop", "\u624b\u673a", phonePrefab != null ? phonePrefab.name : "Phone");
            }

            if (phoneInstance != null)
            {
                loggedMissingPhoneInstance = false;
            }
        }

        private Transform FindPhoneInPreferredSlot()
        {
            var slot = phoneTableSlot != null
                ? phoneTableSlot
                : FindSceneTransformByName(AutoPhoneTableSlotName, "Phone_TableSlot", "\u624b\u673a_TableSlot");
            return FindDirectPhoneChild(slot);
        }

        private Transform FindPhoneNearThisController()
        {
            var direct = FindDirectPhoneChild(transform);
            if (direct != null)
            {
                return direct;
            }

            var transforms = GetComponentsInChildren<Transform>(true);
            for (var i = 0; i < transforms.Length; i++)
            {
                var candidate = transforms[i];
                if (candidate != null && candidate != transform && LooksLikePhone(candidate))
                {
                    return candidate;
                }
            }

            return null;
        }

        private static Transform FindDirectPhoneChild(Transform parent)
        {
            if (parent == null)
            {
                return null;
            }

            for (var i = 0; i < parent.childCount; i++)
            {
                var child = parent.GetChild(i);
                if (LooksLikePhone(child))
                {
                    return child;
                }
            }

            return null;
        }

        private Transform EnsurePhoneTableSlot(bool alignExistingToPhone = false)
        {
            if (phoneTableSlot != null)
            {
                if (alignExistingToPhone)
                {
                    AlignAutoTableSlotToPhonePose();
                }

                return phoneTableSlot;
            }

            phoneTableSlot = FindSceneTransformByName(AutoPhoneTableSlotName, "Phone_TableSlot", "\u624b\u673a_TableSlot");
            if (phoneTableSlot != null)
            {
                if (alignExistingToPhone)
                {
                    AlignAutoTableSlotToPhonePose();
                }

                return phoneTableSlot;
            }

            if (phoneInstance == null)
            {
                TryFindScenePhoneInstance();
            }

            if (phoneInstance == null)
            {
                return null;
            }

            var slot = new GameObject(AutoPhoneTableSlotName);
            slot.hideFlags = HideFlags.None;
            CopyPhonePoseToTableSlot(slot.transform);
            phoneTableSlot = slot.transform;
            return phoneTableSlot;
        }

        private Transform EnsureBedroomPhoneSlot()
        {
            if (bedroomPhoneSlot != null)
            {
                return bedroomPhoneSlot;
            }

            bedroomPhoneSlot = FindSceneTransformByName(
                BedroomPhoneSlotName,
                "BedroomPhoneSlot",
                "Bedroom Phone Slot",
                "Bedroom_PhoneSlot",
                "\u5367\u5ba4\u624b\u673a\u70b9");
            HideDuplicatePhonePropsInKnownSlots();
            return bedroomPhoneSlot;
        }

        private void HideDuplicatePhonePropsInKnownSlots()
        {
            if (!hideDuplicatePhonePropsInSlots)
            {
                return;
            }

            HideDuplicatePhonePropsUnder(phoneTableSlot);
            HideDuplicatePhonePropsUnder(bedroomPhoneSlot);
        }

        private void HideDuplicatePhonePropsUnder(Transform slot)
        {
            if (slot == null)
            {
                return;
            }

            for (var i = 0; i < slot.childCount; i++)
            {
                var child = slot.GetChild(i);
                if (child == null ||
                    child == phoneInstance ||
                    (phoneInstance != null && child.IsChildOf(phoneInstance)) ||
                    !LooksLikePhone(child))
                {
                    continue;
                }

                child.gameObject.SetActive(false);
            }
        }

        private static bool LooksLikePhone(Transform candidate)
        {
            if (candidate == null)
            {
                return false;
            }

            var normalized = NormalizeToken(candidate.name);
            return normalized.Contains("phone") ||
                candidate.name.IndexOf("\u624b\u673a", StringComparison.Ordinal) >= 0;
        }

        private static string NormalizeToken(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "";
            }

            var buffer = new char[value.Length];
            var count = 0;
            for (var i = 0; i < value.Length; i++)
            {
                var c = value[i];
                if (char.IsLetterOrDigit(c))
                {
                    buffer[count++] = char.ToLowerInvariant(c);
                }
            }

            return new string(buffer, 0, count);
        }

        private void AlignAutoTableSlotToPhonePose()
        {
            if (phoneTableSlot == null || phoneInstance == null || phoneInstance.parent == phoneTableSlot)
            {
                return;
            }

            if (!string.Equals(phoneTableSlot.name, AutoPhoneTableSlotName, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            CopyPhonePoseToTableSlot(phoneTableSlot);
        }

        private void CopyPhonePoseToTableSlot(Transform slot)
        {
            if (slot == null || phoneInstance == null)
            {
                return;
            }

            var phoneParent = phoneInstance.parent;
            if (phoneParent != null)
            {
                slot.SetParent(phoneParent, false);
                slot.localPosition = phoneInstance.localPosition;
                slot.localRotation = phoneInstance.localRotation;
                slot.localScale = phoneInstance.localScale;
                return;
            }

            slot.SetParent(null, false);
            slot.SetPositionAndRotation(phoneInstance.position, phoneInstance.rotation);
            slot.localScale = phoneInstance.localScale;
        }

        private static Transform FindSceneTransformByName(params string[] names)
        {
            var transforms = UnityEngine.Object.FindObjectsByType<Transform>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            foreach (var name in names)
            {
                if (string.IsNullOrWhiteSpace(name))
                {
                    continue;
                }

                for (var i = 0; i < transforms.Length; i++)
                {
                    var candidate = transforms[i];
                    if (candidate != null &&
                        candidate.gameObject.scene.IsValid() &&
                        string.Equals(candidate.name, name, StringComparison.OrdinalIgnoreCase))
                    {
                        return candidate;
                    }
                }
            }

            return null;
        }
    }
}
