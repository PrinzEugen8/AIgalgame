using UniVRM10;
using UnityEngine;
using UnityEngine.SceneManagement;

#if UNITY_EDITOR
using UnityEditor;
#endif

namespace AIgalgame.Motion
{
    public static class AIGalgameMotionTestBootstrap
    {
        private const string DefaultVrmCharacterPath = "Assets/Character/wogua/Hayaseyuuka.vrm";
        private const string FallbackFbxCharacterPath = "Assets/Character/keyi/kei.fbx";
        private const string TestCharacterName = "AIgalgame_MotionTestCharacter";
        private static readonly Vector3 TestCharacterPosition = new(2.087f, 4.126f, -6.942f);
        private static readonly Vector3 TestCharacterEuler = new(0f, 189.595f, 0f);
        private static readonly Vector3 TestCharacterScale = new(1.4f, 1.4f, 1.4f);

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void InstallAfterSceneLoad()
        {
            TryInstall(SceneManager.GetActiveScene());
        }

        public static void Install(Scene scene)
        {
            TryInstall(scene);
        }

        private static void TryInstall(Scene scene)
        {
            if (!scene.IsValid())
            {
                return;
            }

            AIGalgameStartupDiagnostics.Log("bootstrap_begin", $"scene={scene.name}");

            if (AIGalgameRelaxRoomMobileRenderOptimizer.ShouldApply())
            {
                AIGalgameRelaxRoomMobileRenderOptimizer.Apply();
            }

            AIGalgameStartupDiagnostics.Log("character_search_begin");
            var animator = FindCharacterAnimator();
            if (animator == null)
            {
                animator = CreateEditorTestCharacter();
            }

            if (animator == null)
            {
                Debug.LogWarning($"AIgalgame RelaxRoom UI did not find a humanoid Animator in scene '{scene.name}'.");
                AIGalgameStartupDiagnostics.Log("character_search_failed", $"scene={scene.name}");
                return;
            }

            AIGalgameStartupDiagnostics.Log("character_found", animator.gameObject.name);

            var player = FindExistingNear<AIGalgamePlayableMotionPlayer>(animator);
            if (player != null)
            {
                player.SetAnimator(animator);
                AIGalgameStartupDiagnostics.Log("motion_player_ready");
            }
            else
            {
                AIGalgameStartupDiagnostics.Log("motion_player_optional_missing", animator.gameObject.name);
            }

            var phoneAttachment = RequireExistingNear<AIGalgameRelaxRoomPhoneAttachmentController>(animator);
            if (phoneAttachment == null)
            {
                return;
            }
            phoneAttachment.SetAnimator(animator);

            var phonePickup = RequireExistingNear<AIGalgameRelaxRoomPhonePickupController>(animator);
            if (phonePickup == null)
            {
                return;
            }
            phonePickup.SetTargets(animator, player, phoneAttachment);
            AIGalgameStartupDiagnostics.Log("phone_pickup_ready");

            var director = RequireExistingNear<AIGalgameRelaxRoomMotionDirector>(animator);
            if (director == null)
            {
                return;
            }
            director.SetTargets(animator, player);
            AIGalgameStartupDiagnostics.Log("motion_director_ready");

            var controller = RequireExistingNear<AIGalgameChatdollController>(animator);
            if (controller == null)
            {
                return;
            }

            var audioSource = FindExistingNear<AudioSource>(animator);
            if (audioSource == null)
            {
                Debug.LogError(
                    $"AIgalgame RelaxRoom setup is missing AudioSource near '{animator.gameObject.name}'. Add it in the scene so it is editable in the Inspector.",
                    animator);
                AIGalgameStartupDiagnostics.Log("audio_source_missing", animator.gameObject.name);
                return;
            }

            var vrm = animator.GetComponent<Vrm10Instance>() ?? animator.GetComponentInChildren<Vrm10Instance>() ?? animator.GetComponentInParent<Vrm10Instance>();
            controller.SetTargets(vrm, player, animator, audioSource, director);
            AIGalgameStartupDiagnostics.Log("chatdoll_controller_ready");

            var touch = RequireExistingNear<AIGalgameRelaxRoomTouchController>(animator);
            if (touch == null)
            {
                return;
            }
            touch.SetTargets(controller, director, animator, audioSource);
            AIGalgameStartupDiagnostics.Log("touch_controller_ready");

            var expressionDirector = RequireExistingNear<AIGalgameRelaxRoomExpressionDirector>(animator);
            if (expressionDirector == null)
            {
                return;
            }
            expressionDirector.SetTargets(vrm);

            var gazeDirector = RequireExistingNear<AIGalgameRelaxRoomGazeDirector>(animator);
            if (gazeDirector == null)
            {
                return;
            }
            gazeDirector.SetTargets(vrm, animator, director);

            controller.SetVisualDirectors(expressionDirector, gazeDirector);
            director.SetVisualDirectors(gazeDirector, expressionDirector);
            director.SetTargets(animator, player);
            AIGalgameStartupDiagnostics.Log("visual_directors_ready");

            var bridgeRoot = GameObject.Find("RelaxRoomBridge");
            var bridge = bridgeRoot != null ? bridgeRoot.GetComponent<RelaxRoomPresentationBridge>() : null;
            if (bridge == null)
            {
                Debug.LogError(
                    "AIgalgame RelaxRoom setup is missing RelaxRoomBridge/RelaxRoomPresentationBridge in the scene. Add the RelaxRoomBridge GameObject and attach the bridge component in the Inspector.",
                    animator);
                AIGalgameStartupDiagnostics.Log("bridge_missing");
                return;
            }
            AIGalgameStartupDiagnostics.Log("bridge_found");

            AIGalgameStartupDiagnostics.Log("bridge_set_targets_begin");
            bridge.SetTargets(controller, director, expressionDirector, gazeDirector, touch, audioSource);
            AIGalgameStartupDiagnostics.Log("bootstrap_done");
        }

        private static T RequireExistingNear<T>(Animator animator) where T : Component
        {
            var component = FindExistingNear<T>(animator);
            if (component != null)
            {
                return component;
            }

            Debug.LogError(
                $"AIgalgame RelaxRoom setup is missing {typeof(T).Name} near '{animator.gameObject.name}'. Add it in the scene so it is editable in the Inspector.",
                animator);
            AIGalgameStartupDiagnostics.Log("component_missing", typeof(T).Name);
            return null;
        }

        private static T FindExistingNear<T>(Animator animator) where T : Component
        {
            if (animator == null)
            {
                return null;
            }

            return animator.GetComponent<T>()
                ?? animator.GetComponentInChildren<T>(true)
                ?? animator.GetComponentInParent<T>(true);
        }

        private static Animator FindCharacterAnimator()
        {
            var scriptedAnimator = FindAnimatorFromExistingRelaxRoomComponents();
            if (scriptedAnimator != null)
            {
                return scriptedAnimator;
            }

            var taggedAnimator = FindTaggedPlayerAnimator();
            if (taggedAnimator != null)
            {
                return taggedAnimator;
            }

            var vrms = Object.FindObjectsByType<Vrm10Instance>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            foreach (var vrm in vrms)
            {
                if (vrm != null && TryGetAnimatorNear(vrm, out var vrmAnimator) && IsUsableAnimator(vrmAnimator))
                {
                    return vrmAnimator;
                }
            }

            var animators = Object.FindObjectsByType<Animator>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            foreach (var candidate in animators)
            {
                if (IsUsableAnimator(candidate))
                {
                    return candidate;
                }
            }

            return animators.Length > 0 ? animators[0] : null;
        }

        private static Animator FindAnimatorFromExistingRelaxRoomComponents()
        {
            var motionAnimator = FindAnimatorFromComponents<AIGalgameRelaxRoomMotionDirector>();
            if (motionAnimator != null)
            {
                return motionAnimator;
            }

            var controllerAnimator = FindAnimatorFromComponents<AIGalgameChatdollController>();
            if (controllerAnimator != null)
            {
                return controllerAnimator;
            }

            var touchAnimator = FindAnimatorFromComponents<AIGalgameRelaxRoomTouchController>();
            if (touchAnimator != null)
            {
                return touchAnimator;
            }

            return FindAnimatorFromComponents<AIGalgameRelaxRoomPhoneAttachmentController>();
        }

        private static Animator FindAnimatorFromComponents<T>() where T : Component
        {
            var components = Object.FindObjectsByType<T>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            Animator best = null;
            var bestScore = int.MinValue;
            foreach (var component in components)
            {
                if (component == null || !component.gameObject.scene.IsValid())
                {
                    continue;
                }

                if (TryGetAnimatorNear(component, out var animator) && IsUsableAnimator(animator))
                {
                    var score = ScoreAnimatorCandidate(animator);
                    if (score > bestScore)
                    {
                        best = animator;
                        bestScore = score;
                    }
                }
            }

            return best;
        }

        private static Animator FindTaggedPlayerAnimator()
        {
            GameObject[] players;
            try
            {
                players = GameObject.FindGameObjectsWithTag("Player");
            }
            catch (UnityException)
            {
                return null;
            }

            foreach (var player in players)
            {
                if (player == null || !player.scene.IsValid())
                {
                    continue;
                }

                var animator = player.GetComponent<Animator>() ??
                    player.GetComponentInChildren<Animator>(true) ??
                    player.GetComponentInParent<Animator>();
                if (IsUsableAnimator(animator))
                {
                    return animator;
                }
            }

            return null;
        }

        private static bool TryGetAnimatorNear(Component component, out Animator animator)
        {
            animator = null;
            if (component == null)
            {
                return false;
            }

            animator = component.GetComponent<Animator>() ??
                component.GetComponentInChildren<Animator>(true) ??
                component.GetComponentInParent<Animator>();
            return animator != null;
        }

        private static bool IsUsableAnimator(Animator animator)
        {
            return animator != null &&
                animator.gameObject.scene.IsValid() &&
                animator.avatar != null &&
                animator.avatar.isHuman;
        }

        private static int ScoreAnimatorCandidate(Animator animator)
        {
            if (animator == null)
            {
                return int.MinValue;
            }

            var score = animator.gameObject.activeInHierarchy ? 1000 : 0;
            if (animator.CompareTag("Player"))
            {
                score += 100;
            }

            var root = animator.transform.root;
            if (root != null && root.CompareTag("Player"))
            {
                score += 80;
            }

            return score;
        }

        private static Animator CreateEditorTestCharacter()
        {
#if UNITY_EDITOR
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(DefaultVrmCharacterPath);
            if (prefab == null)
            {
                prefab = AssetDatabase.LoadAssetAtPath<GameObject>(FallbackFbxCharacterPath);
            }

            if (prefab == null)
            {
                return null;
            }

            var instance = Object.Instantiate(prefab);
            instance.name = TestCharacterName;
            instance.transform.SetPositionAndRotation(TestCharacterPosition, Quaternion.Euler(TestCharacterEuler));
            instance.transform.localScale = TestCharacterScale;

            return instance.GetComponent<Animator>() ?? instance.GetComponentInChildren<Animator>();
#else
            return null;
#endif
        }
    }
}
