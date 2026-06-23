using System;
using System.Collections.Generic;
using UnityEngine;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(-90)]
    [DisallowMultipleComponent]
    public sealed class RelaxRoomCameraDirector : MonoBehaviour
    {
        [SerializeField] private Camera[] cameras = Array.Empty<Camera>();
        [SerializeField] private string bedroomCameraName = "bedroom_camera";
        [SerializeField] private string videoCallCameraName = "PhoneFrontCamera";
        [SerializeField] private bool autoFindCameras = true;
        [SerializeField] private bool retagActiveCameraAsMain = true;

        private int activeIndex = -1;
        private Camera cameraBeforeVideoCall;
        private int cameraIndexBeforeVideoCall = -1;
        private bool videoCallCameraActive;

        public string CurrentCameraName =>
            activeIndex >= 0 && activeIndex < cameras.Length && cameras[activeIndex] != null
                ? cameras[activeIndex].name
                : "";

        private void Awake()
        {
            ResolveCameras();
            ActivateInitialCamera();
        }

        public void NextCamera()
        {
            Step(1);
        }

        public void PreviousCamera()
        {
            Step(-1);
        }

        public void SetCamera(string cameraName)
        {
            ResolveCameras();
            if (string.IsNullOrWhiteSpace(cameraName) || cameras.Length == 0)
            {
                return;
            }

            for (var i = 0; i < cameras.Length; i++)
            {
                var camera = cameras[i];
                if (camera != null &&
                    string.Equals(camera.name, cameraName.Trim(), StringComparison.OrdinalIgnoreCase))
                {
                    ActivateCamera(i);
                    return;
                }
            }
        }

        public void BeginVideoCallCamera()
        {
            ResolveCameras();
            if (cameras == null || cameras.Length == 0)
            {
                return;
            }

            if (!videoCallCameraActive)
            {
                cameraIndexBeforeVideoCall = FindActiveCameraIndex();
                cameraBeforeVideoCall = cameraIndexBeforeVideoCall >= 0 &&
                                        cameraIndexBeforeVideoCall < cameras.Length
                    ? cameras[cameraIndexBeforeVideoCall]
                    : null;
            }

            var videoCallIndex = FindVideoCallCameraIndex();
            if (videoCallIndex < 0)
            {
                Debug.LogWarning(
                    "[RelaxRoomCameraDirector] Video call camera not found. " +
                    "Set videoCallCameraName to the Unity camera mounted on the phone.");
                return;
            }

            videoCallCameraActive = true;
            ActivateCamera(videoCallIndex);
        }

        public void EndVideoCallCamera()
        {
            ResolveCameras();
            if (!videoCallCameraActive)
            {
                return;
            }

            videoCallCameraActive = false;
            var restoreIndex = IndexOfCamera(cameraBeforeVideoCall);
            if (restoreIndex < 0)
            {
                restoreIndex = cameraIndexBeforeVideoCall;
            }

            cameraBeforeVideoCall = null;
            cameraIndexBeforeVideoCall = -1;

            if (restoreIndex >= 0 && cameras != null && restoreIndex < cameras.Length)
            {
                ActivateCamera(restoreIndex);
                return;
            }

            ActivateInitialCamera();
        }

        private void Step(int direction)
        {
            ResolveCameras();
            if (cameras.Length == 0)
            {
                return;
            }

            var current = FindActiveCameraIndex();
            if (current < 0)
            {
                current = activeIndex >= 0 ? activeIndex : 0;
            }

            var next = Mod(current + direction, cameras.Length);
            ActivateCamera(next);
        }

        private void ResolveCameras()
        {
            if (!autoFindCameras && cameras != null && cameras.Length > 0)
            {
                return;
            }

            var resolved = new List<Camera>();
            AddCamera(resolved, Camera.main);
            AddCamera(resolved, FindCameraByName("Camera", "Main Camera"));
            AddCamera(resolved, FindCameraByName(bedroomCameraName, "Bedroom Camera", "bedroom camera"));
            AddCamera(
                resolved,
                FindCameraByName(
                    videoCallCameraName,
                    "PhoneFrontCamera",
                    "Phone Front Camera",
                    "phone_front_camera",
                    "PhoneSelfieCamera",
                    "Phone Selfie Camera",
                    "SelfieCamera",
                    "Selfie Camera",
                    "VideoCallCamera",
                    "Video Call Camera",
                    "video_call_camera"));

            var sceneCameras = UnityEngine.Object.FindObjectsByType<Camera>(
                FindObjectsInactive.Include,
                FindObjectsSortMode.None);
            for (var i = 0; i < sceneCameras.Length; i++)
            {
                AddCamera(resolved, sceneCameras[i]);
            }

            cameras = resolved.ToArray();
            if (activeIndex >= cameras.Length)
            {
                activeIndex = cameras.Length - 1;
            }
        }

        private void ActivateInitialCamera()
        {
            if (cameras == null || cameras.Length == 0)
            {
                return;
            }

            var current = FindActiveCameraIndex();
            ActivateCamera(current >= 0 ? current : 0);
        }

        private int FindVideoCallCameraIndex()
        {
            ResolveCameras();
            if (cameras == null || cameras.Length == 0)
            {
                return -1;
            }

            var configuredIndex = FindCameraIndexByName(videoCallCameraName);
            if (configuredIndex >= 0)
            {
                return configuredIndex;
            }

            var knownNames = new[]
            {
                "PhoneFrontCamera",
                "Phone Front Camera",
                "phone_front_camera",
                "PhoneSelfieCamera",
                "Phone Selfie Camera",
                "SelfieCamera",
                "Selfie Camera",
                "VideoCallCamera",
                "Video Call Camera",
                "video_call_camera",
                "FrontCamera",
                "Front Camera"
            };

            for (var i = 0; i < knownNames.Length; i++)
            {
                var index = FindCameraIndexByName(knownNames[i]);
                if (index >= 0)
                {
                    return index;
                }
            }

            for (var i = 0; i < cameras.Length; i++)
            {
                var camera = cameras[i];
                if (camera == null)
                {
                    continue;
                }

                var cameraName = camera.name ?? "";
                var phoneRelated = ContainsIgnoreCase(cameraName, "phone") ||
                                   ParentChainContains(camera.transform, "phone");
                if (!phoneRelated)
                {
                    continue;
                }

                if (ContainsIgnoreCase(cameraName, "front") ||
                    ContainsIgnoreCase(cameraName, "selfie") ||
                    ContainsIgnoreCase(cameraName, "video") ||
                    ContainsIgnoreCase(cameraName, "call") ||
                    ContainsIgnoreCase(cameraName, "camera"))
                {
                    return i;
                }
            }

            return -1;
        }

        private int FindCameraIndexByName(string cameraName)
        {
            if (string.IsNullOrWhiteSpace(cameraName) || cameras == null)
            {
                return -1;
            }

            var trimmed = cameraName.Trim();
            for (var i = 0; i < cameras.Length; i++)
            {
                var camera = cameras[i];
                if (camera != null &&
                    string.Equals(camera.name, trimmed, StringComparison.OrdinalIgnoreCase))
                {
                    return i;
                }
            }

            return -1;
        }

        private int IndexOfCamera(Camera target)
        {
            if (target == null || cameras == null)
            {
                return -1;
            }

            for (var i = 0; i < cameras.Length; i++)
            {
                if (cameras[i] == target)
                {
                    return i;
                }
            }

            return -1;
        }

        private int FindActiveCameraIndex()
        {
            if (cameras == null)
            {
                return -1;
            }

            for (var i = 0; i < cameras.Length; i++)
            {
                if (cameras[i] != null && cameras[i].enabled)
                {
                    return i;
                }
            }

            return -1;
        }

        private void ActivateCamera(int index)
        {
            if (cameras == null || cameras.Length == 0)
            {
                return;
            }

            activeIndex = Mod(index, cameras.Length);
            for (var i = 0; i < cameras.Length; i++)
            {
                var camera = cameras[i];
                if (camera == null)
                {
                    continue;
                }

                var active = i == activeIndex;
                camera.enabled = active;
                var listener = camera.GetComponent<AudioListener>();
                if (listener != null)
                {
                    listener.enabled = active;
                }
            }

            if (retagActiveCameraAsMain)
            {
                RetagActiveCameraAsMain();
            }
        }

        private void RetagActiveCameraAsMain()
        {
            for (var i = 0; i < cameras.Length; i++)
            {
                var camera = cameras[i];
                if (camera == null)
                {
                    continue;
                }

                try
                {
                    camera.gameObject.tag = i == activeIndex ? "MainCamera" : "Untagged";
                }
                catch (UnityException)
                {
                    retagActiveCameraAsMain = false;
                    return;
                }
            }
        }

        private static void AddCamera(List<Camera> list, Camera camera)
        {
            if (camera == null || list.Contains(camera))
            {
                return;
            }

            if (camera.gameObject.scene.IsValid())
            {
                list.Add(camera);
            }
        }

        private static Camera FindCameraByName(params string[] names)
        {
            var sceneCameras = UnityEngine.Object.FindObjectsByType<Camera>(
                FindObjectsInactive.Include,
                FindObjectsSortMode.None);
            for (var n = 0; n < names.Length; n++)
            {
                var name = names[n];
                if (string.IsNullOrWhiteSpace(name))
                {
                    continue;
                }

                for (var i = 0; i < sceneCameras.Length; i++)
                {
                    var camera = sceneCameras[i];
                    if (camera != null &&
                        camera.gameObject.scene.IsValid() &&
                        string.Equals(camera.name, name, StringComparison.OrdinalIgnoreCase))
                    {
                        return camera;
                    }
                }
            }

            return null;
        }

        private static bool ParentChainContains(Transform transform, string value)
        {
            var current = transform != null ? transform.parent : null;
            while (current != null)
            {
                if (ContainsIgnoreCase(current.name, value))
                {
                    return true;
                }

                current = current.parent;
            }

            return false;
        }

        private static bool ContainsIgnoreCase(string text, string value)
        {
            if (string.IsNullOrEmpty(text) || string.IsNullOrEmpty(value))
            {
                return false;
            }

            return text.IndexOf(value, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static int Mod(int value, int length)
        {
            if (length <= 0)
            {
                return 0;
            }

            return ((value % length) + length) % length;
        }
    }
}
