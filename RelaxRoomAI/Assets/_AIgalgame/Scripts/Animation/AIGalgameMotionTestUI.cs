using System;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(-80)]
    public sealed class AIGalgameMotionTestUI : MonoBehaviour
    {
        [SerializeField] private AIGalgamePlayableMotionPlayer player;
        [SerializeField] private Vector2 panelSize = new(430f, 880f);
        [SerializeField] private bool startVisible;

        private RectTransform listContent;
        private Text currentText;
        private Text blendText;
        private Slider blendSlider;
        private Text calibrationText;
        private Text posXText;
        private Text posYText;
        private Text posZText;
        private Text rotXText;
        private Text rotYText;
        private Text rotZText;
        private Text scaleText;
        private Text speedText;
        private Text ikTargetText;
        private Text ikWeightText;
        private Text ikXText;
        private Text ikYText;
        private Text ikZText;
        private Text ikRotXText;
        private Text ikRotYText;
        private Text ikRotZText;
        private Slider posXSlider;
        private Slider posYSlider;
        private Slider posZSlider;
        private Slider rotXSlider;
        private Slider rotYSlider;
        private Slider rotZSlider;
        private Slider scaleSlider;
        private Slider speedSlider;
        private Slider ikWeightSlider;
        private Slider ikXSlider;
        private Slider ikYSlider;
        private Slider ikZSlider;
        private Slider ikRotXSlider;
        private Slider ikRotYSlider;
        private Slider ikRotZSlider;
        private GameObject canvasRoot;
        private Font font;
        private MotionAdjustmentData currentAdjustment;
        private MotionIkTarget selectedIkTarget = MotionIkTarget.LeftHand;
        private MotionIkHandleKind selectedIkHandleKind = MotionIkHandleKind.Effector;
        private bool suppressAdjustmentEvents;
        private bool visible;
        private bool visibilityConfigured;

        public bool IsVisible => canvasRoot != null && canvasRoot.activeSelf;

        private void Start()
        {
            ResolvePlayer();
            if (!visibilityConfigured)
            {
                visible = startVisible;
            }
            CreateInterface();
            BindPlayer();
            ApplyVisibility();
        }

        private void Update()
        {
            if (player == null || ikWeightSlider == null)
            {
                return;
            }

            var ik = player.IkAdjuster;
            if (ik != null && ik.HasSceneHandleChanged)
            {
                UpdateSelectedIkControlsFromPlayer();
            }
        }

        private void OnDestroy()
        {
            if (player != null)
            {
                player.CatalogChanged -= RebuildMotionList;
                player.MotionChanged -= UpdateCurrentMotion;
            }
        }

        public void SetPlayer(AIGalgamePlayableMotionPlayer motionPlayer)
        {
            player = motionPlayer;
        }

        public void SetStartVisible(bool show)
        {
            startVisible = show;
            if (!visibilityConfigured)
            {
                visible = show;
                ApplyVisibility();
            }
        }

        public void SetVisible(bool show)
        {
            visible = show;
            visibilityConfigured = true;
            ApplyVisibility();
        }

        private void ResolvePlayer()
        {
            if (player == null)
            {
                player = FindFirstObjectByType<AIGalgamePlayableMotionPlayer>();
            }
        }

        private void BindPlayer()
        {
            if (player == null)
            {
                SetCurrentText("No motion player");
                return;
            }

            player.CatalogChanged += RebuildMotionList;
            player.MotionChanged += UpdateCurrentMotion;

            blendSlider.SetValueWithoutNotify(player.BlendSeconds);
            UpdateBlendText(player.BlendSeconds);
            RebuildMotionList();
            UpdateCurrentMotion(player.CurrentClipIndex, player.CurrentClipIndex >= 0 ? player.Clips[player.CurrentClipIndex] : null);
        }

        private void TryPlayTestClip(int direction)
        {
            if (player == null)
            {
                SetCurrentText("No motion player");
                return;
            }

            if (player.PrefersAnimatorController)
            {
                SetCurrentText("Animator Controller mode: use RelaxRoom UI / triggers");
                return;
            }

            switch (direction)
            {
                case -1:
                    player.PlayPrevious();
                    break;
                case 0:
                    player.ReplayCurrent();
                    break;
                default:
                    player.PlayNext();
                    break;
            }
        }

        private void CreateInterface()
        {
            if (canvasRoot != null)
            {
                return;
            }

            font = LoadFont();
            EnsureEventSystem();

            canvasRoot = new GameObject("AIgalgame_MotionTestCanvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            canvasRoot.transform.SetParent(transform, false);

            var canvas = canvasRoot.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 500;

            var scaler = canvasRoot.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 0.5f;

            var panel = CreatePanel(canvasRoot.transform);
            CreateLabel(panel, "Motion Test", 18, FontStyle.Bold);
            currentText = CreateLabel(panel, "No motion", 14, FontStyle.Normal);

            var row = CreateRow(panel, 34f);
            CreateButton(row, "Prev", () => TryPlayTestClip(-1));
            CreateButton(row, "Replay", () => TryPlayTestClip(0));
            CreateButton(row, "Next", () => TryPlayTestClip(1));

            var importRow = CreateRow(panel, 34f);
            CreateButton(importRow, "Rescan FBX", () =>
            {
                if (player == null)
                {
                    return;
                }

                if (player.PrefersAnimatorController)
                {
                    SetCurrentText("Animator Controller mode: clip scan only");
                    player.RefreshClipCatalog();
                    RebuildMotionList();
                    return;
                }

                player.RefreshClipCatalog();
                if (player.Clips.Count > 0 && player.CurrentClipIndex < 0)
                {
                    player.PlayClip(0, 0f);
                }
            });
            CreateButton(importRow, "Stop", () => player?.Stop());

            var sliderRow = CreateRow(panel, 38f);
            blendText = CreateText(sliderRow, "Blend", 13, FontStyle.Normal);
            blendText.rectTransform.sizeDelta = new Vector2(96f, 30f);
            blendSlider = CreateSlider(sliderRow);
            blendSlider.onValueChanged.AddListener(value =>
            {
                if (player != null)
                {
                    player.BlendSeconds = value;
                }

                if (!suppressAdjustmentEvents && currentAdjustment != null)
                {
                    currentAdjustment.blendSeconds = value;
                    SetCalibrationText("Unsaved changes");
                }

                UpdateBlendText(value);
            });

            CreateCalibrationControls(panel);
            CreateMotionList(panel);
            ApplyVisibility();
        }

        private void ApplyVisibility()
        {
            if (canvasRoot != null)
            {
                canvasRoot.SetActive(visible);
            }
        }

        private Transform CreatePanel(Transform parent)
        {
            var panel = new GameObject("Panel", typeof(RectTransform), typeof(Image), typeof(VerticalLayoutGroup));
            panel.transform.SetParent(parent, false);

            var rect = panel.GetComponent<RectTransform>();
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(0f, 1f);
            rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = new Vector2(18f, -18f);
            rect.sizeDelta = panelSize;

            var image = panel.GetComponent<Image>();
            image.color = new Color(0.06f, 0.07f, 0.08f, 0.88f);

            var layout = panel.GetComponent<VerticalLayoutGroup>();
            layout.padding = new RectOffset(14, 14, 12, 12);
            layout.spacing = 4f;
            layout.childControlWidth = true;
            layout.childControlHeight = false;
            layout.childForceExpandWidth = true;
            layout.childForceExpandHeight = false;

            return panel.transform;
        }

        private Transform CreateRow(Transform parent, float height)
        {
            var row = new GameObject("Row", typeof(RectTransform), typeof(HorizontalLayoutGroup), typeof(LayoutElement));
            row.transform.SetParent(parent, false);

            var layout = row.GetComponent<HorizontalLayoutGroup>();
            layout.spacing = 8f;
            layout.childControlWidth = true;
            layout.childControlHeight = true;
            layout.childForceExpandWidth = true;

            var element = row.GetComponent<LayoutElement>();
            element.minHeight = height;
            element.preferredHeight = height;

            return row.transform;
        }

        private Text CreateLabel(Transform parent, string value, int size, FontStyle style)
        {
            var text = CreateText(parent, value, size, style);
            var element = text.gameObject.AddComponent<LayoutElement>();
            element.minHeight = size + 8f;
            element.preferredHeight = size + 10f;
            return text;
        }

        private Text CreateText(Transform parent, string value, int size, FontStyle style)
        {
            var go = new GameObject(value, typeof(RectTransform), typeof(Text));
            go.transform.SetParent(parent, false);

            var text = go.GetComponent<Text>();
            text.font = font;
            text.text = value;
            text.fontSize = size;
            text.fontStyle = style;
            text.color = new Color(0.93f, 0.95f, 0.96f, 1f);
            text.alignment = TextAnchor.MiddleLeft;
            text.horizontalOverflow = HorizontalWrapMode.Wrap;
            text.verticalOverflow = VerticalWrapMode.Truncate;
            return text;
        }

        private Button CreateButton(Transform parent, string label, Action onClick)
        {
            var go = new GameObject(label, typeof(RectTransform), typeof(Image), typeof(Button), typeof(LayoutElement));
            go.transform.SetParent(parent, false);

            var image = go.GetComponent<Image>();
            image.color = new Color(0.13f, 0.17f, 0.2f, 0.96f);

            var button = go.GetComponent<Button>();
            button.targetGraphic = image;
            button.onClick.AddListener(() => onClick?.Invoke());

            var text = CreateText(go.transform, label, 13, FontStyle.Normal);
            text.alignment = TextAnchor.MiddleCenter;
            var textRect = text.rectTransform;
            textRect.anchorMin = Vector2.zero;
            textRect.anchorMax = Vector2.one;
            textRect.offsetMin = Vector2.zero;
            textRect.offsetMax = Vector2.zero;

            var element = go.GetComponent<LayoutElement>();
            element.minHeight = 26f;
            element.preferredHeight = 26f;

            return button;
        }

        private Slider CreateSlider(Transform parent, float minValue = 0f, float maxValue = 1.2f, float value = 0.35f)
        {
            var root = new GameObject("Slider", typeof(RectTransform), typeof(Slider), typeof(LayoutElement));
            root.transform.SetParent(parent, false);

            var rect = root.GetComponent<RectTransform>();
            rect.sizeDelta = new Vector2(200f, 24f);

            var background = new GameObject("Background", typeof(RectTransform), typeof(Image));
            background.transform.SetParent(root.transform, false);
            var bgRect = background.GetComponent<RectTransform>();
            bgRect.anchorMin = new Vector2(0f, 0.5f);
            bgRect.anchorMax = new Vector2(1f, 0.5f);
            bgRect.pivot = new Vector2(0.5f, 0.5f);
            bgRect.offsetMin = new Vector2(0f, -2f);
            bgRect.offsetMax = new Vector2(0f, 2f);
            background.GetComponent<Image>().color = new Color(0.22f, 0.25f, 0.27f, 1f);

            var fillArea = new GameObject("Fill Area", typeof(RectTransform));
            fillArea.transform.SetParent(root.transform, false);
            var fillAreaRect = fillArea.GetComponent<RectTransform>();
            fillAreaRect.anchorMin = new Vector2(0f, 0.5f);
            fillAreaRect.anchorMax = new Vector2(1f, 0.5f);
            fillAreaRect.pivot = new Vector2(0.5f, 0.5f);
            fillAreaRect.offsetMin = new Vector2(6f, -2f);
            fillAreaRect.offsetMax = new Vector2(-6f, 2f);

            var fill = new GameObject("Fill", typeof(RectTransform), typeof(Image));
            fill.transform.SetParent(fillArea.transform, false);
            var fillRect = fill.GetComponent<RectTransform>();
            fillRect.anchorMin = new Vector2(0f, 0f);
            fillRect.anchorMax = new Vector2(0f, 1f);
            fillRect.pivot = new Vector2(0f, 0.5f);
            fillRect.offsetMin = Vector2.zero;
            fillRect.offsetMax = Vector2.zero;
            fill.GetComponent<Image>().color = new Color(0.34f, 0.62f, 0.95f, 1f);

            var handleArea = new GameObject("Handle Slide Area", typeof(RectTransform));
            handleArea.transform.SetParent(root.transform, false);
            var handleAreaRect = handleArea.GetComponent<RectTransform>();
            handleAreaRect.anchorMin = new Vector2(0f, 0.5f);
            handleAreaRect.anchorMax = new Vector2(1f, 0.5f);
            handleAreaRect.pivot = new Vector2(0.5f, 0.5f);
            handleAreaRect.offsetMin = new Vector2(8f, -6f);
            handleAreaRect.offsetMax = new Vector2(-8f, 6f);

            var handle = new GameObject("Handle", typeof(RectTransform), typeof(Image));
            handle.transform.SetParent(handleArea.transform, false);
            var handleRect = handle.GetComponent<RectTransform>();
            handleRect.anchorMin = new Vector2(0f, 0f);
            handleRect.anchorMax = new Vector2(0f, 1f);
            handleRect.pivot = new Vector2(0.5f, 0.5f);
            handleRect.sizeDelta = new Vector2(10f, 0f);
            handle.GetComponent<Image>().color = new Color(0.78f, 0.88f, 0.98f, 1f);

            var slider = root.GetComponent<Slider>();
            slider.minValue = minValue;
            slider.maxValue = maxValue;
            slider.value = value;
            slider.fillRect = fillRect;
            slider.handleRect = handleRect;
            slider.targetGraphic = handle.GetComponent<Image>();
            slider.direction = Slider.Direction.LeftToRight;

            var element = root.GetComponent<LayoutElement>();
            element.minHeight = 24f;
            element.preferredHeight = 24f;
            element.flexibleWidth = 1f;

            return slider;
        }

        private void CreateCalibrationControls(Transform parent)
        {
            CreateLabel(parent, "Calibration", 14, FontStyle.Bold);
            calibrationText = CreateLabel(parent, "Unsaved per-motion offsets", 12, FontStyle.Normal);

            posXSlider = CreateAdjustmentSlider(parent, "Pos X", -2f, 2f, out posXText);
            posYSlider = CreateAdjustmentSlider(parent, "Pos Y", -2f, 2f, out posYText);
            posZSlider = CreateAdjustmentSlider(parent, "Pos Z", -2f, 2f, out posZText);
            rotXSlider = CreateAdjustmentSlider(parent, "Rot X", -45f, 45f, out rotXText);
            rotYSlider = CreateAdjustmentSlider(parent, "Rot Y", -180f, 180f, out rotYText);
            rotZSlider = CreateAdjustmentSlider(parent, "Rot Z", -45f, 45f, out rotZText);
            scaleSlider = CreateAdjustmentSlider(parent, "Scale", 0.8f, 1.2f, out scaleText, 1f);
            speedSlider = CreateAdjustmentSlider(parent, "Speed", 0.5f, 1.5f, out speedText, 1f);

            CreateIkControls(parent);

            var row = CreateRow(parent, 30f);
            CreateButton(row, "Save", SaveCurrentAdjustment);
            CreateButton(row, "Reset Saved", ResetCurrentAdjustment);
            CreateButton(row, "Default", ResetCurrentAdjustmentToDefault);
        }

        private void CreateIkControls(Transform parent)
        {
            CreateLabel(parent, "IK", 14, FontStyle.Bold);
            ikTargetText = CreateLabel(parent, "Target: Left Hand", 12, FontStyle.Normal);

            var targetRow = CreateRow(parent, 30f);
            CreateButton(targetRow, "LH", () => SelectIkTarget(MotionIkTarget.LeftHand));
            CreateButton(targetRow, "RH", () => SelectIkTarget(MotionIkTarget.RightHand));
            CreateButton(targetRow, "LF", () => SelectIkTarget(MotionIkTarget.LeftFoot));
            CreateButton(targetRow, "RF", () => SelectIkTarget(MotionIkTarget.RightFoot));
            CreateButton(targetRow, "Look", () => SelectIkTarget(MotionIkTarget.LookAt));

            var handleRow = CreateRow(parent, 30f);
            CreateButton(handleRow, "Effector", () => SelectIkHandleKind(MotionIkHandleKind.Effector));
            CreateButton(handleRow, "Pole", () => SelectIkHandleKind(MotionIkHandleKind.Pole));

            ikWeightSlider = CreateAdjustmentSlider(parent, "Weight", 0f, 1f, out ikWeightText);
            ikXSlider = CreateAdjustmentSlider(parent, "IK X", -1.5f, 1.5f, out ikXText);
            ikYSlider = CreateAdjustmentSlider(parent, "IK Y", -0.5f, 2.2f, out ikYText);
            ikZSlider = CreateAdjustmentSlider(parent, "IK Z", -1.5f, 1.5f, out ikZText);
            ikRotXSlider = CreateAdjustmentSlider(parent, "Rot X", -180f, 180f, out ikRotXText);
            ikRotYSlider = CreateAdjustmentSlider(parent, "Rot Y", -180f, 180f, out ikRotYText);
            ikRotZSlider = CreateAdjustmentSlider(parent, "Rot Z", -180f, 180f, out ikRotZText);

            var snapRow = CreateRow(parent, 30f);
            CreateButton(snapRow, "Snap Bone", SnapSelectedIkTarget);
            CreateButton(snapRow, "Snap Pole", SnapSelectedPoleTarget);
        }

        private Slider CreateAdjustmentSlider(
            Transform parent,
            string label,
            float minValue,
            float maxValue,
            out Text valueText,
            float value = 0f)
        {
            var row = CreateSliderRow(parent, 24f);

            var labelText = CreateText(row, label, 12, FontStyle.Normal);
            SetLayoutWidth(labelText.gameObject, 52f);

            var slider = CreateSlider(row, minValue, maxValue, value);
            var sliderElement = slider.GetComponent<LayoutElement>();
            sliderElement.flexibleWidth = 1f;

            valueText = CreateText(row, "0.00", 12, FontStyle.Normal);
            valueText.alignment = TextAnchor.MiddleRight;
            SetLayoutWidth(valueText.gameObject, 58f);

            slider.onValueChanged.AddListener(_ => OnAdjustmentSliderChanged());
            return slider;
        }

        private Transform CreateSliderRow(Transform parent, float height)
        {
            var row = new GameObject("SliderRow", typeof(RectTransform), typeof(HorizontalLayoutGroup), typeof(LayoutElement));
            row.transform.SetParent(parent, false);

            var layout = row.GetComponent<HorizontalLayoutGroup>();
            layout.spacing = 8f;
            layout.childControlWidth = true;
            layout.childControlHeight = true;
            layout.childForceExpandWidth = false;

            var element = row.GetComponent<LayoutElement>();
            element.minHeight = height;
            element.preferredHeight = height;

            return row.transform;
        }

        private static void SetLayoutWidth(GameObject target, float width)
        {
            var element = target.GetComponent<LayoutElement>();
            if (element == null)
            {
                element = target.AddComponent<LayoutElement>();
            }

            element.minWidth = width;
            element.preferredWidth = width;
        }

        private void CreateMotionList(Transform parent)
        {
            var scroll = new GameObject("MotionList", typeof(RectTransform), typeof(Image), typeof(ScrollRect), typeof(LayoutElement));
            scroll.transform.SetParent(parent, false);
            scroll.GetComponent<Image>().color = new Color(0.03f, 0.035f, 0.04f, 0.75f);

            var element = scroll.GetComponent<LayoutElement>();
            element.minHeight = 110f;
            element.preferredHeight = 110f;

            var viewport = new GameObject("Viewport", typeof(RectTransform), typeof(Image), typeof(Mask));
            viewport.transform.SetParent(scroll.transform, false);
            viewport.GetComponent<Image>().color = Color.clear;
            viewport.GetComponent<Mask>().showMaskGraphic = false;

            var viewportRect = viewport.GetComponent<RectTransform>();
            viewportRect.anchorMin = Vector2.zero;
            viewportRect.anchorMax = Vector2.one;
            viewportRect.offsetMin = new Vector2(8f, 8f);
            viewportRect.offsetMax = new Vector2(-8f, -8f);

            var content = new GameObject("Content", typeof(RectTransform), typeof(VerticalLayoutGroup), typeof(ContentSizeFitter));
            content.transform.SetParent(viewport.transform, false);
            listContent = content.GetComponent<RectTransform>();
            listContent.anchorMin = new Vector2(0f, 1f);
            listContent.anchorMax = new Vector2(1f, 1f);
            listContent.pivot = new Vector2(0.5f, 1f);
            listContent.offsetMin = Vector2.zero;
            listContent.offsetMax = Vector2.zero;

            var layout = content.GetComponent<VerticalLayoutGroup>();
            layout.spacing = 6f;
            layout.childControlWidth = true;
            layout.childControlHeight = false;
            layout.childForceExpandWidth = true;

            var fitter = content.GetComponent<ContentSizeFitter>();
            fitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

            var scrollRect = scroll.GetComponent<ScrollRect>();
            scrollRect.viewport = viewportRect;
            scrollRect.content = listContent;
            scrollRect.horizontal = false;
            scrollRect.vertical = true;
            scrollRect.scrollSensitivity = 24f;
        }

        private void RebuildMotionList()
        {
            if (listContent == null)
            {
                return;
            }

            for (var i = listContent.childCount - 1; i >= 0; --i)
            {
                Destroy(listContent.GetChild(i).gameObject);
            }

            if (player == null || player.Clips.Count == 0)
            {
                CreateLabel(listContent, "No FBX clips", 13, FontStyle.Normal);
                return;
            }

            for (var i = 0; i < player.Clips.Count; ++i)
            {
                var index = i;
                var entry = player.Clips[i];
                CreateButton(listContent, entry.displayName, () => player.PlayClip(index));
            }
        }

        private void UpdateCurrentMotion(int index, MotionClipEntry entry)
        {
            SetCurrentText(entry == null ? "Stopped" : $"Now: {entry.displayName}");
            LoadAdjustmentForMotion(index, entry);
        }

        private void SetCurrentText(string value)
        {
            if (currentText != null)
            {
                currentText.text = value;
            }
        }

        private void UpdateBlendText(float value)
        {
            if (blendText != null)
            {
                blendText.text = $"Blend {value:0.00}s";
            }
        }

        private void LoadAdjustmentForMotion(int index, MotionClipEntry entry)
        {
            if (player == null || entry == null || index < 0)
            {
                currentAdjustment = null;
                SetCalibrationText("No active motion");
                return;
            }

            currentAdjustment = AIGalgameMotionAdjustmentStore.LoadOrDefault(
                player.GetClipKey(index),
                entry.displayName,
                player.BlendSeconds);

            player.ApplyMotionAdjustment(currentAdjustment);
            ApplyAdjustmentToControls(currentAdjustment);
            SetCalibrationText("Loaded calibration");
        }

        private void ApplyAdjustmentToControls(MotionAdjustmentData adjustment)
        {
            if (adjustment == null || posXSlider == null)
            {
                return;
            }

            suppressAdjustmentEvents = true;
            posXSlider.SetValueWithoutNotify(adjustment.rootPositionOffset.x);
            posYSlider.SetValueWithoutNotify(adjustment.rootPositionOffset.y);
            posZSlider.SetValueWithoutNotify(adjustment.rootPositionOffset.z);
            rotXSlider.SetValueWithoutNotify(adjustment.rootRotationOffset.x);
            rotYSlider.SetValueWithoutNotify(adjustment.rootRotationOffset.y);
            rotZSlider.SetValueWithoutNotify(adjustment.rootRotationOffset.z);
            scaleSlider.SetValueWithoutNotify(adjustment.rootScale);
            speedSlider.SetValueWithoutNotify(adjustment.playbackSpeed);
            blendSlider.SetValueWithoutNotify(adjustment.blendSeconds);
            suppressAdjustmentEvents = false;

            UpdateAdjustmentTexts();
            UpdateSelectedIkControlsFromPlayer();
            UpdateBlendText(adjustment.blendSeconds);
        }

        private void OnAdjustmentSliderChanged()
        {
            if (suppressAdjustmentEvents || player == null)
            {
                return;
            }

            EnsureCurrentAdjustment();
            if (currentAdjustment == null)
            {
                return;
            }

            ReadControlsToAdjustment(currentAdjustment);
            player.ApplyMotionAdjustment(currentAdjustment);
            UpdateAdjustmentTexts();
            UpdateSelectedIkTexts();
            SetCalibrationText("Unsaved changes");
        }

        private void EnsureCurrentAdjustment()
        {
            if (currentAdjustment != null || player == null)
            {
                return;
            }

            var index = player.CurrentClipIndex;
            if (index < 0 || index >= player.Clips.Count)
            {
                return;
            }

            var entry = player.Clips[index];
            currentAdjustment = MotionAdjustmentData.CreateDefault(
                player.GetClipKey(index),
                entry.displayName,
                player.BlendSeconds);
        }

        private void ReadControlsToAdjustment(MotionAdjustmentData adjustment)
        {
            adjustment.rootPositionOffset = new Vector3(posXSlider.value, posYSlider.value, posZSlider.value);
            adjustment.rootRotationOffset = new Vector3(rotXSlider.value, rotYSlider.value, rotZSlider.value);
            adjustment.rootScale = scaleSlider.value;
            adjustment.playbackSpeed = speedSlider.value;
            adjustment.blendSeconds = blendSlider.value;
            var ik = player != null ? player.IkAdjuster : null;
            if (ik == null || !ik.HasSceneHandleChanged)
            {
                WriteSelectedIkControlsToPlayer();
            }

            player.WriteIkToAdjustment(adjustment);
            ik?.ClearSceneHandleChanged();
        }

        private void SaveCurrentAdjustment()
        {
            if (player == null)
            {
                return;
            }

            EnsureCurrentAdjustment();
            if (currentAdjustment == null)
            {
                SetCalibrationText("No motion to save");
                return;
            }

            ReadControlsToAdjustment(currentAdjustment);
            AIGalgameMotionAdjustmentStore.Save(currentAdjustment);
            player.ApplyMotionAdjustment(currentAdjustment);
            SetCalibrationText("Saved calibration");
        }

        private void ResetCurrentAdjustment()
        {
            if (player == null)
            {
                return;
            }

            var index = player.CurrentClipIndex;
            if (index < 0 || index >= player.Clips.Count)
            {
                SetCalibrationText("No motion to reset");
                return;
            }

            var entry = player.Clips[index];
            currentAdjustment = AIGalgameMotionAdjustmentStore.LoadOrDefault(
                player.GetClipKey(index),
                entry.displayName,
                player.BlendSeconds);
            player.ApplyMotionAdjustment(currentAdjustment);
            ApplyAdjustmentToControls(currentAdjustment);
            SetCalibrationText("Reset to saved");
        }

        private void ResetCurrentAdjustmentToDefault()
        {
            if (player == null)
            {
                return;
            }

            var index = player.CurrentClipIndex;
            if (index < 0 || index >= player.Clips.Count)
            {
                SetCalibrationText("No motion to reset");
                return;
            }

            var entry = player.Clips[index];
            var clipKey = player.GetClipKey(index);
            AIGalgameMotionAdjustmentStore.Delete(clipKey);
            currentAdjustment = MotionAdjustmentData.CreateDefault(clipKey, entry.displayName, 0.35f);
            player.ApplyMotionAdjustment(currentAdjustment);
            ApplyAdjustmentToControls(currentAdjustment);
            SetCalibrationText("Default restored");
        }

        private void UpdateAdjustmentTexts()
        {
            SetText(posXText, $"{posXSlider.value:+0.00;-0.00;0.00}m");
            SetText(posYText, $"{posYSlider.value:+0.00;-0.00;0.00}m");
            SetText(posZText, $"{posZSlider.value:+0.00;-0.00;0.00}m");
            SetText(rotXText, $"{rotXSlider.value:+0;-0;0}deg");
            SetText(rotYText, $"{rotYSlider.value:+0;-0;0}deg");
            SetText(rotZText, $"{rotZSlider.value:+0;-0;0}deg");
            SetText(scaleText, $"{scaleSlider.value:0.00}x");
            SetText(speedText, $"{speedSlider.value:0.00}x");
            UpdateSelectedIkTexts();
        }

        private void SelectIkTarget(MotionIkTarget target)
        {
            selectedIkTarget = target;
            if (selectedIkTarget == MotionIkTarget.LookAt)
            {
                selectedIkHandleKind = MotionIkHandleKind.Effector;
            }

            UpdateSelectedIkControlsFromPlayer();
        }

        private void SelectIkHandleKind(MotionIkHandleKind handleKind)
        {
            selectedIkHandleKind = selectedIkTarget == MotionIkTarget.LookAt
                ? MotionIkHandleKind.Effector
                : handleKind;
            UpdateSelectedIkControlsFromPlayer();
        }

        private void SnapSelectedIkTarget()
        {
            if (player == null)
            {
                return;
            }

            var ik = player.IkAdjuster;
            if (ik == null)
            {
                return;
            }

            ik.SnapTargetToCurrentBone(selectedIkTarget);
            UpdateSelectedIkControlsFromPlayer();
            EnsureCurrentAdjustment();
            if (currentAdjustment != null)
            {
                player.WriteIkToAdjustment(currentAdjustment);
                SetCalibrationText("Unsaved changes");
            }
        }

        private void SnapSelectedPoleTarget()
        {
            if (player == null || selectedIkTarget == MotionIkTarget.LookAt)
            {
                return;
            }

            var ik = player.IkAdjuster;
            if (ik == null)
            {
                return;
            }

            ik.SnapPoleToCurrentBend(selectedIkTarget);
            selectedIkHandleKind = MotionIkHandleKind.Pole;
            UpdateSelectedIkControlsFromPlayer();
            EnsureCurrentAdjustment();
            if (currentAdjustment != null)
            {
                player.WriteIkToAdjustment(currentAdjustment);
                SetCalibrationText("Unsaved changes");
            }
        }

        private void UpdateSelectedIkControlsFromPlayer()
        {
            if (player == null || ikWeightSlider == null)
            {
                return;
            }

            var ik = player.IkAdjuster;
            if (ik == null)
            {
                return;
            }

            if (selectedIkTarget == MotionIkTarget.LookAt)
            {
                selectedIkHandleKind = MotionIkHandleKind.Effector;
            }

            var localPosition = selectedIkHandleKind == MotionIkHandleKind.Pole
                ? ik.GetPoleLocalPosition(selectedIkTarget)
                : ik.GetTargetLocalPosition(selectedIkTarget);
            var localEuler = selectedIkHandleKind == MotionIkHandleKind.Pole
                ? Vector3.zero
                : ik.GetTargetLocalEuler(selectedIkTarget);
            suppressAdjustmentEvents = true;
            ikWeightSlider.SetValueWithoutNotify(ik.GetWeight(selectedIkTarget));
            ikXSlider.SetValueWithoutNotify(localPosition.x);
            ikYSlider.SetValueWithoutNotify(localPosition.y);
            ikZSlider.SetValueWithoutNotify(localPosition.z);
            ikRotXSlider.SetValueWithoutNotify(localEuler.x);
            ikRotYSlider.SetValueWithoutNotify(localEuler.y);
            ikRotZSlider.SetValueWithoutNotify(localEuler.z);
            suppressAdjustmentEvents = false;
            UpdateSelectedIkTexts();
        }

        private void WriteSelectedIkControlsToPlayer()
        {
            if (player == null || ikWeightSlider == null)
            {
                return;
            }

            var ik = player.IkAdjuster;
            if (ik == null)
            {
                return;
            }

            ik.SetWeight(selectedIkTarget, ikWeightSlider.value);
            var localPosition = new Vector3(ikXSlider.value, ikYSlider.value, ikZSlider.value);
            if (selectedIkHandleKind == MotionIkHandleKind.Pole && selectedIkTarget != MotionIkTarget.LookAt)
            {
                ik.SetPoleLocalPosition(selectedIkTarget, localPosition);
                return;
            }

            ik.SetTargetLocalPosition(selectedIkTarget, localPosition);
            ik.SetTargetLocalEuler(selectedIkTarget, new Vector3(ikRotXSlider.value, ikRotYSlider.value, ikRotZSlider.value));
        }

        private void UpdateSelectedIkTexts()
        {
            var handleLabel = selectedIkHandleKind == MotionIkHandleKind.Pole ? "Pole" : "Effector";
            SetText(ikTargetText, $"Target: {GetIkTargetLabel(selectedIkTarget)} / {handleLabel}");
            SetText(ikWeightText, $"{ikWeightSlider.value:0.00}");
            SetText(ikXText, $"{ikXSlider.value:+0.00;-0.00;0.00}m");
            SetText(ikYText, $"{ikYSlider.value:+0.00;-0.00;0.00}m");
            SetText(ikZText, $"{ikZSlider.value:+0.00;-0.00;0.00}m");
            if (selectedIkHandleKind == MotionIkHandleKind.Pole)
            {
                SetText(ikRotXText, "--");
                SetText(ikRotYText, "--");
                SetText(ikRotZText, "--");
            }
            else
            {
                SetText(ikRotXText, $"{ikRotXSlider.value:+0;-0;0}deg");
                SetText(ikRotYText, $"{ikRotYSlider.value:+0;-0;0}deg");
                SetText(ikRotZText, $"{ikRotZSlider.value:+0;-0;0}deg");
            }
        }

        private static string GetIkTargetLabel(MotionIkTarget target)
        {
            return target switch
            {
                MotionIkTarget.LeftHand => "Left Hand",
                MotionIkTarget.RightHand => "Right Hand",
                MotionIkTarget.LeftFoot => "Left Foot",
                MotionIkTarget.RightFoot => "Right Foot",
                MotionIkTarget.LookAt => "Look At",
                _ => target.ToString()
            };
        }

        private void SetCalibrationText(string value)
        {
            SetText(calibrationText, value);
        }

        private static void SetText(Text text, string value)
        {
            if (text != null)
            {
                text.text = value;
            }
        }

        private static void EnsureEventSystem()
        {
            var eventSystem = FindFirstObjectByType<EventSystem>();
            if (eventSystem == null)
            {
                var go = new GameObject("EventSystem", typeof(EventSystem));
                eventSystem = go.GetComponent<EventSystem>();
            }

            if (eventSystem.GetComponent<BaseInputModule>() != null)
            {
                return;
            }

            var inputSystemModule = Type.GetType("UnityEngine.InputSystem.UI.InputSystemUIInputModule, Unity.InputSystem");
            if (inputSystemModule != null)
            {
                eventSystem.gameObject.AddComponent(inputSystemModule);
            }
            else
            {
                eventSystem.gameObject.AddComponent<StandaloneInputModule>();
            }
        }

        private static Font LoadFont()
        {
            var loaded = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            if (loaded == null)
            {
                loaded = Resources.GetBuiltinResource<Font>("Arial.ttf");
            }

            return loaded;
        }
    }
}
