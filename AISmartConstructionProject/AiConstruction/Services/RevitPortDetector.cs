using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Timers;
using Timer = System.Timers.Timer;

namespace AiConstruction.Services
{
    /// <summary>
    /// Revit 多实例端口检测器
    /// 通过扫描 %TEMP%/BeeSync/revit_ports/{PID}.port 发现所有 Revit 实例，
    /// 并监控前台窗口自动切换活跃实例
    /// </summary>
    public class RevitPortDetector : IDisposable
    {
        // Win32 API for foreground window detection
        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        private static readonly string PortDir = Path.Combine(
            Path.GetTempPath(), "BeeSync", "revit_ports");

        private readonly Timer _scanTimer;
        private bool _disposed;

        /// <summary>当前活跃的 Revit 端口（前台窗口对应的实例），null 表示无 Revit 运行</summary>
        public int? ActivePort { get; private set; }

        /// <summary>所有存活的 Revit 实例 { PID → Port }</summary>
        public IReadOnlyDictionary<int, int> ActiveInstances => _instances;
        private Dictionary<int, int> _instances = new();

        /// <summary>每个实例 .port 文件的最后修改时间 { PID → LastWriteTime }，用于判断最新启动的实例</summary>
        private Dictionary<int, DateTime> _instanceTimestamps = new();

        /// <summary>活跃端口变化事件</summary>
        public event EventHandler<int?>? ActivePortChanged;


        public RevitPortDetector(int scanIntervalMs = 500)
        {
            // 确保 port 目录存在（给插件用）
            if (!Directory.Exists(PortDir))
                Directory.CreateDirectory(PortDir);

            _scanTimer = new Timer(scanIntervalMs);
            _scanTimer.Elapsed += OnScan;
            _scanTimer.AutoReset = true;
        }

        /// <summary>开始监控</summary>
        public void Start()
        {
            // 先立即扫描一次
            ScanNow();
            _scanTimer.Start();
            LogHelper.Info("[端口检测] 开始监控 Revit 实例");
        }

        /// <summary>立即扫描一次（不等待定时器）</summary>
        private void ScanNow()
        {
            try
            {
                var previousPort = ActivePort;

                // 1. 扫描所有 .port 文件，过滤已退出的进程
                RefreshInstances();

                // 2. 根据前台窗口确定活跃实例
                ActivePort = ResolveActivePort();

                // 3. 如果变化，触发事件
                if (ActivePort != previousPort)
                {
                    LogHelper.Info(ActivePort.HasValue
                        ? $"[端口检测] 活跃 Revit 切换 → 端口 {ActivePort}"
                        : "[端口检测] Revit 已退出，无活跃实例");

                    ActivePortChanged?.Invoke(this, ActivePort);
                }
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[端口检测] 扫描异常: {ex.Message}");
            }
        }

        private void OnScan(object? sender, ElapsedEventArgs e) => ScanNow();

        /// <summary>读取 .port 文件，过滤已退出进程，同时记录文件修改时间</summary>
        private void RefreshInstances()
        {
            var result = new Dictionary<int, int>();
            var timestamps = new Dictionary<int, DateTime>();

            if (!Directory.Exists(PortDir))
                return;

            foreach (var file in Directory.GetFiles(PortDir, "*.port"))
            {
                var name = Path.GetFileNameWithoutExtension(file);
                if (!int.TryParse(name, out var pid))
                    continue;

                // 检查进程是否存活
                try
                {
                    using var proc = Process.GetProcessById(pid);
                    if (proc.HasExited || !proc.ProcessName.Contains("revit", StringComparison.OrdinalIgnoreCase))
                        continue;
                }
                catch (ArgumentException)
                {
                    // 进程不存在 → 清理 .port 文件
                    try { File.Delete(file); } catch { }
                    continue;
                }
                catch
                {
                    continue;
                }

                // 读取端口号
                try
                {
                    var portText = File.ReadAllText(file).Trim();
                    if (int.TryParse(portText, out var port))
                    {
                        result[pid] = port;
                        timestamps[pid] = File.GetLastWriteTime(file);
                    }
                }
                catch { }
            }

            _instances = result;
            _instanceTimestamps = timestamps;
        }

        /// <summary>
        /// 根据前台窗口确定活跃 Revit 端口。
        /// 多实例时前台不是 Revit，选择最新启动的实例（.port 文件修改时间最新），
        /// 确保新启动的 Revit 实例能被正确路由。
        /// </summary>
        private int? ResolveActivePort()
        {
            if (_instances.Count == 0)
                return null;

            // 只有 1 个实例 → 直接用
            if (_instances.Count == 1)
                return _instances.Values.First();

            // 多个实例 → 先尝试前台窗口
            var fgHwnd = GetForegroundWindow();
            if (fgHwnd != IntPtr.Zero)
            {
                GetWindowThreadProcessId(fgHwnd, out var fgPid);
                var pid = (int)fgPid;

                if (_instances.TryGetValue(pid, out var fgPort))
                    return fgPort;
            }

            // 前台不是 Revit → 选择最新启动的实例（.port 文件修改时间最新）
            var newestPid = _instances.Keys
                .OrderByDescending(p => _instanceTimestamps.TryGetValue(p, out var ts) ? ts : DateTime.MinValue)
                .FirstOrDefault();

            if (newestPid != 0 && _instances.TryGetValue(newestPid, out var newestPort))
            {
                LogHelper.Info($"[端口检测] 前台非 Revit，选择最新实例 PID={newestPid} 端口={newestPort}");
                return newestPort;
            }

            return _instances.Values.First();
        }


        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            _scanTimer.Stop();
            _scanTimer.Dispose();
            LogHelper.Info("[端口检测] 已停止监控");
        }
    }
}
