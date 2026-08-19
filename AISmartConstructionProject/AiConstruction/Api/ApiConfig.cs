namespace AiConstruction.Api
{
    public static class ApiConfig
    {
        /// <summary>Runtime HTTP 基地址（RuntimeManager 启动成功后会覆盖）</summary>
        public static string BaseUrl { get; set; } = "http://127.0.0.1:18765";

        /// <summary>Bearer Token（RuntimeManager 每次启动随机生成）</summary>
        public static string AuthToken { get; set; } = string.Empty;

        /// <summary>LLM API Key（阿里云百炼 / 其他 LLM 服务）</summary>
        public static string LlmApiKey { get; set; } = "sk-0109fd095312495c8b4539c10e1deb0c";

        /// <summary>是否已配置</summary>
        public static bool IsConfigured => !string.IsNullOrEmpty(AuthToken);

        /// <summary>连接超时（秒）</summary>
        public static int ConnectTimeoutSeconds { get; set; } = 10;
    }
}
