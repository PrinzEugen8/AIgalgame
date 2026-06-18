using UnityEngine;

namespace SojaExiles
{
    /// <summary>
    /// Legacy input shim for Apartment Kit interaction scripts.
    /// RN/mobile builds use Input Manager, so this avoids the Input System package at runtime.
    /// </summary>
    public static class InputCompat
    {
        public static bool GetMouseButtonDown(int button)
        {
            return Input.GetMouseButtonDown(button);
        }
    }
}
