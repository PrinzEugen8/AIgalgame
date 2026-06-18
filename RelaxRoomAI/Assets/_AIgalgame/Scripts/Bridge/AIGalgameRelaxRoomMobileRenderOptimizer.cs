using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace AIgalgame.Motion
{
    public static class AIGalgameRelaxRoomMobileRenderOptimizer
    {
        private static bool applied;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void AutoApply()
        {
            if (ShouldApply())
            {
                Apply();
            }
        }

        public static bool ShouldApply()
        {
#if RELAXROOM_RN
            return true;
#else
            return Application.isMobilePlatform;
#endif
        }

        public static void Apply()
        {
            if (applied)
            {
                RefreshAfterSceneLoad();
                return;
            }

            applied = true;
            ApplyMobileQualityLevel();
            RefreshAfterSceneLoad();
            DisableExpensiveVolumeEffects();
            TuneActiveRenderPipeline();
            AIGalgameStartupDiagnostics.Log("mobile_render_optimized");
        }

        public static void RefreshAfterSceneLoad()
        {
            if (!ShouldApply())
            {
                return;
            }

            DisableAdditionalLightShadows();
        }

        private static void ApplyMobileQualityLevel()
        {
            var qualityNames = QualitySettings.names;
            for (var i = 0; i < qualityNames.Length; i++)
            {
                if (qualityNames[i] == "Mobile")
                {
                    QualitySettings.SetQualityLevel(i, applyExpensiveChanges: true);
                    QualitySettings.globalTextureMipmapLimit = 0;
                    return;
                }
            }

            QualitySettings.globalTextureMipmapLimit = 0;
        }

        private static void DisableAdditionalLightShadows()
        {
            var lights = Object.FindObjectsByType<Light>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            foreach (var light in lights)
            {
                if (light.type != LightType.Directional && light.shadows != LightShadows.None)
                {
                    light.shadows = LightShadows.None;
                }
            }
        }

        private static void DisableExpensiveVolumeEffects()
        {
            var volumes = Object.FindObjectsByType<Volume>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            foreach (var volume in volumes)
            {
                if (volume.profile == null)
                {
                    continue;
                }

                foreach (var component in volume.profile.components)
                {
                    if (component == null)
                    {
                        continue;
                    }

                    if (component.name is "ScreenSpaceReflection" or "AmbientOcclusion")
                    {
                        component.active = false;
                    }
                }

                if (volume.profile.TryGet<Bloom>(out var bloom))
                {
                    bloom.maxIterations.Override(3);
                    bloom.highQualityFiltering.Override(false);
                }
            }
        }

        private static void TuneActiveRenderPipeline()
        {
            if (GraphicsSettings.currentRenderPipeline is not UniversalRenderPipelineAsset urpAsset)
            {
                return;
            }

            urpAsset.renderScale = Mathf.Min(urpAsset.renderScale, 0.875f);
        }
    }
}
