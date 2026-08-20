using System.Security.Cryptography;
using System.Text;

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

        /// <summary>LLM API Key（阿里云百炼 / 其他 LLM 服务）</summary>
      

        /// <summary>是否已配置</summary>
        public static bool IsConfigured => !string.IsNullOrEmpty(AuthToken);

        /// <summary>连接超时（秒）</summary>
        public static int ConnectTimeoutSeconds { get; set; } = 10;

        #region 账号服务（登录后填充）

        /// <summary>账号服务 Token（登录成功后设置）</summary>
        public static string AccountToken { get; set; } = string.Empty;

        /// <summary>当前用户 ID</summary>
        public static string UserId { get; set; } = string.Empty;

        /// <summary>授权级别</summary>
        public static int Authorise { get; set; }

        public static string Phone { get; set; } = string.Empty;
        public static string Password { get; set; } = string.Empty;
        

        //用户名称
        public static string UserName { get; set; } = string.Empty;


        /// <summary>是否已登录</summary>
        public static bool IsLoggedIn => !string.IsNullOrEmpty(AccountToken);

        /// <summary>本地登录信息文件路径（程序同目录）</summary>
        public static string LoginDataPath =>
            System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "login.dat");

        #endregion

        #region 加密 / 解密（DPAPI，仅本机本用户可解密）

        /// <summary>
        /// 加密明文字符串，返回 Base64 密文
        /// </summary>
        public static string EncryptString(string plainText)
        {
            if (string.IsNullOrEmpty(plainText))
                return string.Empty;

            byte[] plainBytes = Encoding.UTF8.GetBytes(plainText);
            byte[] encryptedBytes = ProtectedData.Protect(plainBytes, null, DataProtectionScope.CurrentUser);
            return Convert.ToBase64String(encryptedBytes);
        }

        /// <summary>
        /// 解密 Base64 密文，返回明文字符串
        /// </summary>
        public static string DecryptString(string cipherBase64)
        {
            if (string.IsNullOrEmpty(cipherBase64))
                return string.Empty;

            byte[] encryptedBytes = Convert.FromBase64String(cipherBase64);
            byte[] plainBytes = ProtectedData.Unprotect(encryptedBytes, null, DataProtectionScope.CurrentUser);
            return Encoding.UTF8.GetString(plainBytes);
        }

        #endregion
    }
}
