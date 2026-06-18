using System;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(-70)]
    public sealed class AIGalgameChatdollDemoUI : MonoBehaviour
    {
        [SerializeField] private AIGalgameChatdollController controller;
        [SerializeField] private Vector2 panelSize = new(460f, 360f);
        [SerializeField] private bool startVisible;

        private GameObject canvasRoot;
        private InputField inputField;
        private Text statusText;
        private Font font;
        private bool visible;
        private bool visibilityConfigured;

        public bool IsVisible => canvasRoot != null && canvasRoot.activeSelf;

        private void Start()
        {
            ResolveController();
            if (!visibilityConfigured)
            {
                visible = startVisible;
            }
            CreateInterface();
            ApplyVisibility();
        }

        private void Update()
        {
            if (statusText != null && controller != null)
            {
                statusText.text = controller.GetDebugSummary();
            }
        }

        public void SetController(AIGalgameChatdollController targetController)
        {
            controller = targetController;
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

        private void ResolveController()
        {
            if (controller == null)
            {
                controller = FindFirstObjectByType<AIGalgameChatdollController>();
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

            canvasRoot = new GameObject("AIgalgame_ChatdollDemoCanvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            canvasRoot.transform.SetParent(transform, false);

            var canvas = canvasRoot.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 520;

            var scaler = canvasRoot.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 0.5f;

            var panel = CreatePanel(canvasRoot.transform);
            CreateLabel(panel, "Chatdoll Controller", 18, FontStyle.Bold);
            statusText = CreateLabel(panel, "Waiting for controller", 12, FontStyle.Normal);
            statusText.rectTransform.sizeDelta = new Vector2(0f, 118f);

            inputField = CreateInput(panel);
            inputField.text = "[face:happy][anim:speaking] Demo voice line with expression, motion and lip sync.";
            inputField.onValueChanged.AddListener(_ =>
            {
                if (inputField != null && inputField.isFocused)
                {
                    controller?.SetTyping();
                }
            });
            inputField.onEndEdit.AddListener(_ => controller?.SetIdle());

            var stateRow = CreateRow(panel, 34f);
            CreateButton(stateRow, "Idle", () => controller?.SetIdle());
            CreateButton(stateRow, "Typing", () => controller?.SetTyping());
            CreateButton(stateRow, "Speak", () => controller?.SayTagged(inputField.text));

            var faceRow = CreateRow(panel, 34f);
            CreateButton(faceRow, "Happy", () => controller?.SetFace("happy"));
            CreateButton(faceRow, "Thinking", () => controller?.SetFace("thinking"));
            CreateButton(faceRow, "Angry", () => controller?.SetFace("angry"));

            var mouthRow = CreateRow(panel, 34f);
            CreateButton(mouthRow, "Mouth", () => controller?.PreviewLipSync());
            CreateButton(mouthRow, "Tagged", () => controller?.SayTagged("[pause:0.2][face:shy][anim:typing] I am checking the controller tags now."));
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
            rect.anchorMin = new Vector2(1f, 1f);
            rect.anchorMax = new Vector2(1f, 1f);
            rect.pivot = new Vector2(1f, 1f);
            rect.anchoredPosition = new Vector2(-18f, -18f);
            rect.sizeDelta = panelSize;

            var image = panel.GetComponent<Image>();
            image.color = new Color(0.06f, 0.07f, 0.08f, 0.88f);

            var layout = panel.GetComponent<VerticalLayoutGroup>();
            layout.padding = new RectOffset(14, 14, 12, 12);
            layout.spacing = 6f;
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
            element.preferredHeight = height;

            return row.transform;
        }

        private Text CreateLabel(Transform parent, string text, int size, FontStyle style)
        {
            var label = new GameObject("Text", typeof(RectTransform), typeof(Text), typeof(LayoutElement));
            label.transform.SetParent(parent, false);
            var textComponent = label.GetComponent<Text>();
            textComponent.text = text;
            textComponent.font = font;
            textComponent.fontSize = size;
            textComponent.fontStyle = style;
            textComponent.color = Color.white;
            textComponent.alignment = TextAnchor.MiddleLeft;
            textComponent.horizontalOverflow = HorizontalWrapMode.Wrap;
            textComponent.verticalOverflow = VerticalWrapMode.Truncate;

            var element = label.GetComponent<LayoutElement>();
            element.preferredHeight = Mathf.Max(24f, size + 10f);
            return textComponent;
        }

        private InputField CreateInput(Transform parent)
        {
            var root = new GameObject("Input", typeof(RectTransform), typeof(Image), typeof(InputField), typeof(LayoutElement));
            root.transform.SetParent(parent, false);

            var image = root.GetComponent<Image>();
            image.color = new Color(0.13f, 0.15f, 0.18f, 0.95f);

            var element = root.GetComponent<LayoutElement>();
            element.preferredHeight = 42f;

            var textObject = new GameObject("Text", typeof(RectTransform), typeof(Text));
            textObject.transform.SetParent(root.transform, false);
            var textRect = textObject.GetComponent<RectTransform>();
            textRect.anchorMin = Vector2.zero;
            textRect.anchorMax = Vector2.one;
            textRect.offsetMin = new Vector2(10f, 5f);
            textRect.offsetMax = new Vector2(-10f, -5f);

            var text = textObject.GetComponent<Text>();
            text.font = font;
            text.fontSize = 13;
            text.color = Color.white;
            text.alignment = TextAnchor.MiddleLeft;
            text.horizontalOverflow = HorizontalWrapMode.Wrap;

            var input = root.GetComponent<InputField>();
            input.textComponent = text;
            input.lineType = InputField.LineType.MultiLineSubmit;
            input.characterLimit = 400;
            return input;
        }

        private Button CreateButton(Transform parent, string label, UnityEngine.Events.UnityAction action)
        {
            var root = new GameObject(label, typeof(RectTransform), typeof(Image), typeof(Button));
            root.transform.SetParent(parent, false);

            var image = root.GetComponent<Image>();
            image.color = new Color(0.18f, 0.22f, 0.27f, 0.96f);

            var button = root.GetComponent<Button>();
            button.targetGraphic = image;
            button.onClick.AddListener(action);

            var textObject = new GameObject("Text", typeof(RectTransform), typeof(Text));
            textObject.transform.SetParent(root.transform, false);
            var rect = textObject.GetComponent<RectTransform>();
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;

            var text = textObject.GetComponent<Text>();
            text.text = label;
            text.font = font;
            text.fontSize = 13;
            text.color = Color.white;
            text.alignment = TextAnchor.MiddleCenter;

            return button;
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
            return Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf") ??
                Resources.GetBuiltinResource<Font>("Arial.ttf");
        }
    }
}
