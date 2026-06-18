using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(-108)]
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomPhoneScreenController : MonoBehaviour
    {
        [Serializable]
        private sealed class RendererSlot
        {
            public Renderer renderer;
            public int materialIndex;
        }

        [Header("Targets")]
        [SerializeField] private AIGalgameRelaxRoomPhoneAttachmentController phoneAttachment;
        [SerializeField] private Transform phoneRoot;
        [SerializeField] private Texture activeScreenTexture;
        [SerializeField] private Texture messageTexture;
        [SerializeField] private string screenMaterialName = "Material.010";

        [Header("Screen")]
        [SerializeField] private Color screenOffColor = Color.black;
        [SerializeField] private Color screenOnColor = Color.white;
        [SerializeField] private bool screenOnAtStart;
        [SerializeField] private float colorLerpSpeed = 9f;
        [SerializeField] private float defaultMessageSeconds = 4.5f;
        [SerializeField] private float putDownScreenSeconds = 5f;

        private readonly List<RendererSlot> slots = new();
        private MaterialPropertyBlock propertyBlock;
        private Transform cachedPhoneRoot;
        private Color currentColor;
        private Color targetColor;
        private Texture currentTexture;
        private float screenHoldUntil;
        private bool screenOn;

        public bool IsScreenOn => screenOn;
        public Transform ScreenTarget => slots.Count > 0 && slots[0].renderer != null ? slots[0].renderer.transform : phoneRoot;

        private void Reset()
        {
            ResolveReferences();
        }

        private void Awake()
        {
            ResolveReferences();
            RebuildRendererSlots();
            screenOn = screenOnAtStart;
            currentColor = screenOn ? screenOnColor : screenOffColor;
            targetColor = currentColor;
            currentTexture = activeScreenTexture != null ? activeScreenTexture : messageTexture;
            ApplyScreenProperties(currentColor);
        }

        private void LateUpdate()
        {
            ResolveReferences();
            if (cachedPhoneRoot != phoneRoot)
            {
                RebuildRendererSlots();
            }

            if (screenHoldUntil > 0f && Time.time >= screenHoldUntil)
            {
                SetScreenOn(false);
            }

            currentColor = Color.Lerp(currentColor, targetColor, Time.deltaTime * Mathf.Max(0.01f, colorLerpSpeed));
            ApplyScreenProperties(currentColor);
        }

        public void ShowMessage(float holdSeconds = 0f, bool keepOn = false)
        {
            ShowTexture(messageTexture != null ? messageTexture : activeScreenTexture, holdSeconds > 0f ? holdSeconds : defaultMessageSeconds, keepOn);
        }

        public void ShowActiveScreen(float holdSeconds = 0f, bool keepOn = true)
        {
            ShowTexture(activeScreenTexture != null ? activeScreenTexture : messageTexture, holdSeconds > 0f ? holdSeconds : putDownScreenSeconds, keepOn);
        }

        public void SetScreenOn(bool value)
        {
            screenOn = value;
            if (value && currentTexture == null)
            {
                currentTexture = activeScreenTexture != null ? activeScreenTexture : messageTexture;
            }

            targetColor = value ? screenOnColor : screenOffColor;
            if (!value)
            {
                screenHoldUntil = 0f;
            }
        }

        private void ShowTexture(Texture texture, float holdSeconds, bool keepOn)
        {
            currentTexture = texture != null ? texture : currentTexture;
            SetScreenOn(true);
            screenHoldUntil = keepOn ? 0f : Time.time + Mathf.Max(0.1f, holdSeconds);
        }

        [ContextMenu("RelaxRoom/Phone Screen On")]
        private void ContextScreenOn()
        {
            ShowActiveScreen(0f, true);
        }

        [ContextMenu("RelaxRoom/Phone Screen Off")]
        private void ContextScreenOff()
        {
            SetScreenOn(false);
            currentColor = screenOffColor;
            ApplyScreenProperties(currentColor);
        }

        private void ResolveReferences()
        {
            if (phoneAttachment == null)
            {
                phoneAttachment = GetComponent<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInParent<AIGalgameRelaxRoomPhoneAttachmentController>();
            }

            if (phoneAttachment != null && phoneAttachment.PhoneInstance != null)
            {
                phoneRoot = phoneAttachment.PhoneInstance;
            }
        }

        private void RebuildRendererSlots()
        {
            cachedPhoneRoot = phoneRoot;
            slots.Clear();
            if (phoneRoot == null)
            {
                return;
            }

            var normalizedMaterialName = NormalizeToken(screenMaterialName);
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
                    if ((!string.IsNullOrWhiteSpace(normalizedMaterialName) && materialName.Contains(normalizedMaterialName)) ||
                        materialName.Contains("screen"))
                    {
                        slots.Add(new RendererSlot { renderer = renderer, materialIndex = i });
                    }
                }
            }
        }

        private void ApplyScreenProperties(Color color)
        {
            if (propertyBlock == null)
            {
                propertyBlock = new MaterialPropertyBlock();
            }

            for (var i = 0; i < slots.Count; i++)
            {
                var slot = slots[i];
                if (slot.renderer == null)
                {
                    continue;
                }

                slot.renderer.GetPropertyBlock(propertyBlock, slot.materialIndex);
                propertyBlock.SetColor("_BaseColor", color);
                propertyBlock.SetColor("_Color", color);
                propertyBlock.SetColor("_EmissionColor", screenOn ? color * 0.35f : Color.black);
                if (currentTexture != null)
                {
                    propertyBlock.SetTexture("_BaseMap", currentTexture);
                    propertyBlock.SetTexture("_MainTex", currentTexture);
                    propertyBlock.SetTexture("_EmissionMap", currentTexture);
                }

                slot.renderer.SetPropertyBlock(propertyBlock, slot.materialIndex);
                propertyBlock.Clear();
            }
        }

        private static string NormalizeToken(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "";
            }

            return new string(value.ToLowerInvariant().Where(char.IsLetterOrDigit).ToArray());
        }
    }
}
