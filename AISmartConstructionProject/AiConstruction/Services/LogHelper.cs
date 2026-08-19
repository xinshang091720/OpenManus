using System;
using System.IO;

namespace AiConstruction.Services
{
    /// <summary>
    /// 文件日志工具 —— 按日期写入程序目录下的 Logs 文件夹
    /// </summary>
    public static class LogHelper
    {
        private static readonly object _lock = new();
        private static readonly string _logDir;

        static LogHelper()
        {
            var exeDir = AppDomain.CurrentDomain.BaseDirectory;
            _logDir = Path.Combine(exeDir, "Logs");
        }

        private static string GetLogPath()
        {
            var date = DateTime.Now.ToString("yyyy-MM-dd");
            return Path.Combine(_logDir, $"{date}.log");
        }

        private static void WriteLog(string level, string message)
        {
            try
            {
                lock (_lock)
                {
                    Directory.CreateDirectory(_logDir);
                    var timestamp = DateTime.Now.ToString("HH:mm:ss.fff");
                    File.AppendAllText(GetLogPath(), $"[{timestamp}] [{level}] {message}{Environment.NewLine}");
                }
            }
            catch
            {
                // 日志写入失败不应影响主流程
            }
        }

        public static void Info(string message) => WriteLog("INFO", message);
        public static void Warn(string message) => WriteLog("WARN", message);
        public static void Error(string message) => WriteLog("ERROR", message);
    }
}
