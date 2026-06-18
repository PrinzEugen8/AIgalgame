namespace AIgalgame.Motion
{
    public readonly struct AIGalgameLocalAsrResult
    {
        public AIGalgameLocalAsrResult(bool ok, string text, string error)
        {
            Ok = ok;
            Text = text ?? "";
            Error = error ?? "";
        }

        public bool Ok { get; }
        public string Text { get; }
        public string Error { get; }

        public static AIGalgameLocalAsrResult Success(string text)
        {
            return new AIGalgameLocalAsrResult(true, text, "");
        }

        public static AIGalgameLocalAsrResult Fail(string error)
        {
            return new AIGalgameLocalAsrResult(false, "", error);
        }
    }
}
