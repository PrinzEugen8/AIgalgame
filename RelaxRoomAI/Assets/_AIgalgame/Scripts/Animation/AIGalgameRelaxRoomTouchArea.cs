using UnityEngine;

namespace AIgalgame.Motion
{
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomTouchArea : MonoBehaviour
    {
        [SerializeField] private string hitArea = "body";

        public string HitArea
        {
            get => hitArea;
            set => hitArea = string.IsNullOrWhiteSpace(value) ? "body" : value.Trim().ToLowerInvariant();
        }
    }
}
