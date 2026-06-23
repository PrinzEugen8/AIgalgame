using UniVRM10;
using UnityEngine;

[DefaultExecutionOrder(200)]
public sealed class VrmExpressionKeyframeDriver : MonoBehaviour
{
    [SerializeField] private Vrm10Instance vrmInstance;

    [SerializeField, Range(0f, 1f)] private float happy;
    [SerializeField, Range(0f, 1f)] private float angry;
    [SerializeField, Range(0f, 1f)] private float sad;
    [SerializeField, Range(0f, 1f)] private float relaxed;
    [SerializeField, Range(0f, 1f)] private float surprised;
    [SerializeField, Range(0f, 1f)] private float blink;
    [SerializeField, Range(0f, 1f)] private float aa;

    private void Reset()
    {
        vrmInstance = GetComponent<Vrm10Instance>()
            ?? GetComponentInChildren<Vrm10Instance>()
            ?? GetComponentInParent<Vrm10Instance>();
    }

    private void LateUpdate()
    {
        if (vrmInstance == null || vrmInstance.Runtime?.Expression == null)
            return;

        var expression = vrmInstance.Runtime.Expression;
        expression.SetWeight(ExpressionKey.Happy, happy);
        expression.SetWeight(ExpressionKey.Angry, angry);
        expression.SetWeight(ExpressionKey.Sad, sad);
        expression.SetWeight(ExpressionKey.Relaxed, relaxed);
        expression.SetWeight(ExpressionKey.Surprised, surprised);
        expression.SetWeight(ExpressionKey.Blink, blink);
        expression.SetWeight(ExpressionKey.Aa, aa);
    }
}