using System.Reflection;
using UniVRM10;
using UnityEngine;

namespace AIgalgame.Motion
{
    [DisallowMultipleComponent]
    public sealed class AIGalgameMotionPlayer : MonoBehaviour
    {
        [Header("Targets")]
        [SerializeField] private Animator animator;
        [SerializeField] private Vrm10Instance vrmInstance;

        [Header("FBX / Mecanim")]
        [SerializeField] private RuntimeAnimatorController fbxController;
        [SerializeField] private string initialAnimatorState = "SittingTalking";
        [SerializeField] private bool playInitialAnimatorStateOnStart = true;
        [SerializeField] private float defaultTransitionSeconds = 0.25f;

        [Header("VRMA")]
        [SerializeField] private GameObject initialVrmaPrefab;
        [SerializeField] private bool playInitialVrmaOnStart;
        [SerializeField] private bool enableVrmControlRig = true;
        [SerializeField] private bool hideVrmaDriverBoxMan = true;

        private static readonly FieldInfo UseControlRigField = typeof(Vrm10Instance).GetField(
            "m_useControlRig",
            BindingFlags.Instance | BindingFlags.NonPublic);

        private static readonly FieldInfo RuntimeField = typeof(Vrm10Instance).GetField(
            "m_runtime",
            BindingFlags.Instance | BindingFlags.NonPublic);

        private Vrm10AnimationInstance activeVrma;
        private GameObject activeVrmaRoot;

        private void Reset()
        {
            ResolveReferences();
        }

        private void Awake()
        {
            ResolveReferences();

            if (fbxController != null && animator != null)
            {
                animator.runtimeAnimatorController = fbxController;
            }

            if (enableVrmControlRig)
            {
                TryEnableVrmControlRigBeforeRuntimeStarts();
            }
        }

        private void Start()
        {
            if (playInitialVrmaOnStart && initialVrmaPrefab != null)
            {
                PlayVrma(initialVrmaPrefab);
                return;
            }

            if (playInitialAnimatorStateOnStart && !string.IsNullOrWhiteSpace(initialAnimatorState))
            {
                PlayAnimatorState(initialAnimatorState, 0f);
            }
        }

        private void OnDestroy()
        {
            ClearVrma();
        }

        public void PlayAnimatorState(string stateName)
        {
            PlayAnimatorState(stateName, defaultTransitionSeconds);
        }

        public void PlayAnimatorState(string stateName, float transitionSeconds)
        {
            if (string.IsNullOrWhiteSpace(stateName))
            {
                return;
            }

            ResolveReferences();
            ClearVrma();

            if (animator == null)
            {
                Debug.LogWarning($"{nameof(AIGalgameMotionPlayer)} cannot play '{stateName}' because no Animator was found.", this);
                return;
            }

            if (fbxController != null && animator.runtimeAnimatorController == null)
            {
                animator.runtimeAnimatorController = fbxController;
            }

            animator.enabled = true;
            if (transitionSeconds <= 0f)
            {
                animator.Play(stateName, 0, 0f);
            }
            else
            {
                animator.CrossFadeInFixedTime(stateName, transitionSeconds, 0, 0f);
            }
        }

        public void PlayVrma(GameObject vrmaPrefab)
        {
            ResolveReferences();

            if (vrmInstance == null)
            {
                Debug.LogWarning($"{nameof(AIGalgameMotionPlayer)} cannot play VRMA because no Vrm10Instance was found.", this);
                return;
            }

            if (vrmaPrefab == null)
            {
                Debug.LogWarning($"{nameof(AIGalgameMotionPlayer)} cannot play VRMA because the prefab is empty.", this);
                return;
            }

            if (!TryEnableVrmControlRigBeforeRuntimeStarts())
            {
                return;
            }

            ClearVrma();

            activeVrmaRoot = Instantiate(vrmaPrefab);
            activeVrmaRoot.name = $"{vrmaPrefab.name} Driver";
            activeVrmaRoot.transform.SetPositionAndRotation(transform.position, transform.rotation);

            activeVrma = activeVrmaRoot.GetComponent<Vrm10AnimationInstance>();
            if (activeVrma == null)
            {
                activeVrma = activeVrmaRoot.GetComponentInChildren<Vrm10AnimationInstance>();
            }

            if (activeVrma == null)
            {
                Debug.LogWarning($"{nameof(AIGalgameMotionPlayer)} could not find Vrm10AnimationInstance on '{vrmaPrefab.name}'.", this);
                Destroy(activeVrmaRoot);
                activeVrmaRoot = null;
                return;
            }

            if (hideVrmaDriverBoxMan && activeVrma.BoxMan != null)
            {
                activeVrma.ShowBoxMan(false);
            }

            PlayLegacyAnimationOnVrmaDriver(activeVrma);
            vrmInstance.Runtime.VrmAnimation = activeVrma;
        }

        public void ClearVrma()
        {
            if (vrmInstance != null &&
                RuntimeField?.GetValue(vrmInstance) is Vrm10Runtime runtime &&
                ReferenceEquals(runtime.VrmAnimation, activeVrma))
            {
                runtime.VrmAnimation = null;
            }

            if (activeVrmaRoot != null)
            {
                Destroy(activeVrmaRoot);
            }

            activeVrma = null;
            activeVrmaRoot = null;
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

            if (vrmInstance == null)
            {
                vrmInstance = GetComponent<Vrm10Instance>();
            }

            if (vrmInstance == null)
            {
                vrmInstance = GetComponentInChildren<Vrm10Instance>();
            }
        }

        private bool TryEnableVrmControlRigBeforeRuntimeStarts()
        {
            if (vrmInstance == null)
            {
                return true;
            }

            if (RuntimeField?.GetValue(vrmInstance) is Vrm10Runtime runtime)
            {
                if (runtime.ControlRig != null)
                {
                    return true;
                }

                Debug.LogWarning(
                    "VRMA playback needs the VRM ControlRig, but this Vrm10Instance runtime was already created without it. " +
                    "Add or enable AIGalgameMotionPlayer before Vrm10Instance starts, then enter Play Mode again.",
                    vrmInstance);
                return false;
            }

            if (UseControlRigField == null)
            {
                Debug.LogWarning("Could not enable UniVRM ControlRig because its private field was not found.", vrmInstance);
                return false;
            }

            UseControlRigField.SetValue(vrmInstance, true);
            return true;
        }

        private static void PlayLegacyAnimationOnVrmaDriver(Vrm10AnimationInstance vrma)
        {
            var animation = vrma.GetComponent<UnityEngine.Animation>();
            if (animation == null)
            {
                return;
            }

            foreach (AnimationState state in animation)
            {
                state.wrapMode = WrapMode.Loop;
            }

            animation.wrapMode = WrapMode.Loop;
            animation.Play();
        }
    }
}
