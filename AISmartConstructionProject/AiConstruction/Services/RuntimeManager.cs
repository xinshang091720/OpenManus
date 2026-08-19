using AiConstruction.Api;
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace AiConstruction.Services
{
    /// <summary>
    /// BeeSync.AgentRuntime 进程管理器
    /// 职责：动态端口分配、启动/停止 exe、Token/Key 注入、健康检查、进程树清理
    /// </summary>
    public class RuntimeManager : IDisposable
    {
        private Process? _process;
        private string? _sessionToken;
        private int _port;
        private bool _disposed;

        private static readonly string RuntimeExeDir = Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory, "..", "..", "..", "..", "BeeSync.AgentRuntime");

        private static readonly string RuntimeExePath = Path.Combine(RuntimeExeDir, "BeeSync.AgentRuntime.exe");

        // 按文档规范的目录
        private static readonly string ConfigDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Anbi", "BeeSync", "config");

        private static readonly string DataDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Anbi", "BeeSync");

        private static readonly string UserSkillsDir = Path.Combine(DataDir, "skills");
        private static readonly string UserMcpDir = Path.Combine(DataDir, "mcp-servers");

        /// <summary>Runtime 是否已就绪（健康检查通过 + api_version 兼容）</summary>
        public bool IsReady { get; private set; }

        /// <summary>动态分配的 BaseUrl</summary>
        public string BaseUrl => $"http://127.0.0.1:{_port}";

        /// <summary>当前会话 Token</summary>
        public string? SessionToken => _sessionToken;


        /// <summary>Runtime 进程意外退出时触发</summary>
        public event EventHandler? ProcessExited;

        public RuntimeManager()
        {
            // 解析 exe 实际路径：开发时从 Debug/bin 向上找，发布时在同级目录
            var debugExe = Path.GetFullPath(RuntimeExePath);
            if (File.Exists(debugExe))
            {
                LogHelper.Info($"[运行管理器] 找到 exe: {debugExe}");
            }
            else
            {
                // 回退：查找桌面和项目根目录
                var alt1 = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    "BeeSync.AgentRuntime", "BeeSync.AgentRuntime.exe");
                var alt2 = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    "AISmartConstructionProject", "BeeSync.AgentRuntime", "BeeSync.AgentRuntime.exe");

                if (File.Exists(alt1))
                    LogHelper.Info($"[运行管理器] 找到 exe (alt1): {alt1}");
                else if (File.Exists(alt2))
                    LogHelper.Info($"[运行管理器] 找到 exe (alt2): {alt2}");
            }
        }

        private static string GetExePath()
        {
            // 逐级尝试
            var paths = new[]
            {
                RuntimeExePath,
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    "BeeSync.AgentRuntime", "BeeSync.AgentRuntime.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    "AISmartConstructionProject", "BeeSync.AgentRuntime", "BeeSync.AgentRuntime.exe"),
            };

            foreach (var p in paths)
            {
                var full = Path.GetFullPath(p);
                if (File.Exists(full))
                    return full;
            }

            throw new FileNotFoundException("找不到 BeeSync.AgentRuntime.exe", RuntimeExePath);
        }

        /// <summary>动态分配空闲端口</summary>
        private static int FindAvailablePort()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            var port = ((IPEndPoint)listener.LocalEndpoint).Port;
            listener.Stop();
            return port;
        }

        /// <summary>生成随机 Token（32 字节 hex）</summary>
        private static string GenerateToken()
        {
            var bytes = new byte[32];
            using var rng = System.Security.Cryptography.RandomNumberGenerator.Create();
            rng.GetBytes(bytes);
            return Convert.ToHexString(bytes).ToLowerInvariant();
        }

        /// <summary>确保目标目录存在于 ProgramData</summary>
        private static void EnsureDir(string path, string label)
        {
            if (!Directory.Exists(path))
            {
                Directory.CreateDirectory(path);
                LogHelper.Info($"[运行管理器] 创建{label}: {path}");
            }
        }

        /// <summary>迁移 config.toml 和 mcp.json 到 ProgramData（如果不存在）</summary>
        private static void EnsureConfigFiles(string exeDir)
        {
            var configSrcDir = Path.Combine(exeDir, "_internal", "config");

            // config.toml
            MigrateConfigFile(configSrcDir, ConfigDir, "config.toml");

            // mcp.json（从 mcp.example.json 复制并重命名）
            MigrateConfigFile(configSrcDir, ConfigDir, "mcp.json", "mcp.example.json");
        }

        private static void MigrateConfigFile(string srcDir, string destDir, string fileName, string? srcFileName = null)
        {
            var dest = Path.Combine(destDir, fileName);
            if (File.Exists(dest)) return;

            var src = Path.Combine(srcDir, srcFileName ?? fileName);
            if (File.Exists(src))
            {
                File.Copy(src, dest);
                LogHelper.Info($"[运行管理器] 迁移 {fileName}: {src} → {dest}");
            }
            else
            {
                LogHelper.Warn($"[运行管理器] {fileName} 源文件不存在: {src}");
            }
        }

        /// <summary>启动 Runtime 进程（阻塞直到健康检查通过或超时）</summary>
        public async Task<bool> StartAsync(CancellationToken ct = default)
        {
            if (_disposed) return false;
            if (_process != null && !_process.HasExited)
            {
                LogHelper.Warn("[运行管理器] Runtime 已在运行中");
                return IsReady;
            }

            var stopwatch = Stopwatch.StartNew();

            try
            {
                // 0. 生成随机 Token
                _sessionToken = GenerateToken();
                ApiConfig.AuthToken = _sessionToken;
                LogHelper.Info($"[运行管理器] Token 已生成 (len={_sessionToken.Length})");

                // 1. 动态端口
                _port = FindAvailablePort();
                LogHelper.Info($"[运行管理器] 分配端口: {_port}");

                // 2. 获取 exe 路径和工作目录
                var exePath = GetExePath();
                var exeDir = Path.GetDirectoryName(exePath)!;
                LogHelper.Info($"[运行管理器] exe: {exePath}");
                LogHelper.Info($"[运行管理器] 工作目录: {exeDir}");

                // 3. 创建必需目录
                EnsureDir(ConfigDir, "配置目录");
                EnsureDir(DataDir, "数据目录");
                EnsureDir(UserSkillsDir, "用户技能目录");
                EnsureDir(UserMcpDir, "用户MCP目录");
                EnsureConfigFiles(exeDir);

                // 4. 确定 skills 目录
                var skillsDir = Path.Combine(exeDir, "skills");
                if (!Directory.Exists(skillsDir))
                {
                    var altSkills = Path.Combine(exeDir, "_internal", "skills");
                    if (Directory.Exists(altSkills))
                        skillsDir = altSkills;
                    else
                        Directory.CreateDirectory(skillsDir);
                }

                // 5. 构建启动命令行
                var args = new[]
                {
                    "--host", "127.0.0.1",
                    "--port", _port.ToString(),
                    "--config-dir", ConfigDir,
                    "--data-dir", DataDir,
                    "--skills-dir", skillsDir,
                    "--user-skills-dir", UserSkillsDir,
                    "--revit-api-base-url", $"http://localhost:{RevitProxyService.ProxyPort}/api/RevitApi",
                    "--runtime-version", "1.0.0",
                };

                LogHelper.Info($"[运行管理器] 启动命令: {exePath} {string.Join(" ", args)}");

                // 6. 构建 ProcessStartInfo
                var psi = new ProcessStartInfo
                {
                    FileName = exePath,
                    WorkingDirectory = exeDir,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };

                foreach (var a in args)
                    psi.ArgumentList.Add(a);

                // 注入环境变量（敏感信息，不写入命令行）
                psi.Environment["OPENMANUS_RUNTIME_TOKEN"] = _sessionToken;

                var llmApiKey = ApiConfig.LlmApiKey;
                if (!string.IsNullOrEmpty(llmApiKey))
                {
                    psi.Environment["BEESYNC_LLM_API_KEY"] = llmApiKey;
                    LogHelper.Info($"[运行管理器] LLM API Key 已注入 (len={llmApiKey.Length})");
                }
                else
                {
                    LogHelper.Warn("[运行管理器] LLM API Key 未配置");
                }

                // Revit API 地址（通过 WPF 代理自动路由到前台实例）
                psi.Environment["BEESYNC_REVIT_API_BASE_URL"] = $"http://localhost:{RevitProxyService.ProxyPort}/api/RevitApi";

                // 禁用浏览器功能（环境变量控制）
                psi.Environment["BEESYNC_ENABLE_BROWSER"] = "0";

                // 7. 启动进程
                _process = new Process { StartInfo = psi, EnableRaisingEvents = true };

                // 捕获输出到日志
                _process.OutputDataReceived += (_, e) =>
                {
                    if (!string.IsNullOrEmpty(e.Data))
                        LogHelper.Info($"[运行输出] {e.Data}");
                };
                _process.ErrorDataReceived += (_, e) =>
                {
                    if (!string.IsNullOrEmpty(e.Data))
                        LogHelper.Info($"[运行输出] {e.Data}");
                };

                // 进程退出事件
                _process.Exited += (_, _) =>
                {
                    LogHelper.Info($"[运行管理器] 进程已退出, 退出码: {_process?.ExitCode} (0x{unchecked((uint)(_process?.ExitCode ?? 0)):X8})");
                    IsReady = false;
                    ProcessExited?.Invoke(this, EventArgs.Empty);
                };

                _process.Start();
                _process.BeginOutputReadLine();
                _process.BeginErrorReadLine();

                LogHelper.Info($"[运行管理器] 进程已启动, PID: {_process.Id}");

                // 8. 健康检查循环
                var ready = await WaitForHealthCheckAsync(ct);
                if (ready)
                {
                    IsReady = true;
                    ApiConfig.BaseUrl = BaseUrl;
                    stopwatch.Stop();
                    LogHelper.Info($"[运行管理器] 启动成功 ({stopwatch.ElapsedMilliseconds}ms), 就绪地址: {BaseUrl}");
                }
                else
                {
                    LogHelper.Error($"[运行管理器] 健康检查失败, 启动耗时: {stopwatch.ElapsedMilliseconds}ms");
                }

                return ready;
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[运行管理器] 启动异常: {ex}");
                return false;
            }
        }

        /// <summary>轮询健康检查，最长 30 秒</summary>
        private async Task<bool> WaitForHealthCheckAsync(CancellationToken ct)
        {
            using var healthClient = new HttpClient
            {
                Timeout = TimeSpan.FromSeconds(15)
            };
            healthClient.DefaultRequestHeaders.Authorization =
                new AuthenticationHeaderValue("Bearer", _sessionToken);

            var healthUrl = $"{BaseUrl}/api/v1/health";
            var maxAttempts = 60; // 500ms * 60 = 30s
            var attempt = 0;

            while (attempt < maxAttempts && !ct.IsCancellationRequested)
            {
                attempt++;

                // 检查进程是否已退出
                if (_process?.HasExited == true)
                {
                    var exitCode = _process.ExitCode;
                    LogHelper.Error($"[运行管理器] 进程已退出 (第{attempt}次检查), 退出码: {exitCode} (0x{unchecked((uint)exitCode):X8})");
                    return false;
                }

                try
                {
                    var response = await healthClient.GetAsync(healthUrl, ct);
                    if (response.IsSuccessStatusCode)
                    {
                        var body = await response.Content.ReadAsStringAsync();

                        // 记录健康检查结果（截断 revit_plugin.message）
                        var summary = body.Length > 200 ? body[..200] + "..." : body;
                        LogHelper.Info($"[运行管理器] 健康检查通过 (第{attempt}次): {summary}");

                        // 验证 api_version
                        try
                        {
                            using var doc = JsonDocument.Parse(body);
                            if (doc.RootElement.TryGetProperty("api_version", out var apiVer))
                            {
                                var ver = apiVer.GetString();
                                LogHelper.Info($"[运行管理器] api_version: {ver}");
                            }

                            if (doc.RootElement.TryGetProperty("runtime_version", out var rtVer))
                            {
                                var rtv = rtVer.GetString();
                                if (!string.IsNullOrEmpty(rtv))
                                    LogHelper.Info($"[运行管理器] runtime_version: {rtv}");
                            }
                        }
                        catch { }

                        return true;
                    }

                    LogHelper.Warn($"[运行管理器] 健康检查返回 {(int)response.StatusCode} (第{attempt}次)");
                }
                catch (TaskCanceledException)
                {
                    // 超时 → 继续重试
                    LogHelper.Info($"[运行管理器] 健康检查超时 (第{attempt}次)，继续重试...");
                }
                catch (HttpRequestException ex)
                {
                    LogHelper.Info($"[运行管理器] 健康检查连接失败 (第{attempt}次): {ex.Message}");
                }

                await Task.Delay(500, ct);
            }

            LogHelper.Error($"[运行管理器] 健康检查超时 ({maxAttempts}次尝试后失败)");
            return false;
        }

        /// <summary>创建已配置好 BaseAddress 和 Token 的 ApiClient</summary>
        public HttpClient CreateApiClient()
        {
            var httpClient = new HttpClient
            {
                BaseAddress = new Uri(BaseUrl),
                // Revit 单次操作最长可运行两小时，SSE 由调用方的
                // CancellationToken 控制生命周期，不能使用较短的总请求超时。
                Timeout = System.Threading.Timeout.InfiniteTimeSpan
            };

            if (!string.IsNullOrEmpty(_sessionToken))
            {
                httpClient.DefaultRequestHeaders.Authorization =
                    new AuthenticationHeaderValue("Bearer", _sessionToken);
            }

            return httpClient;
        }

        /// <summary>停止 Runtime 进程及其子进程树</summary>
        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            if (_process == null || _process.HasExited)
            {
                _process?.Dispose();
                _process = null;
                return;
            }

            LogHelper.Info($"[运行管理器] 正在停止 Runtime (PID: {_process.Id})...");

            try
            {
                // Windows: 杀进程树
                if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                {
                    KillProcessTree(_process.Id);
                }
                else
                {
                    _process.Kill(entireProcessTree: true);
                }

                _process.WaitForExit(5000);
                LogHelper.Info("[运行管理器] Runtime 已停止");
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[运行管理器] 停止 Runtime 异常: {ex}");
            }
            finally
            {
                _process.Dispose();
                _process = null;
                IsReady = false;
            }
        }

        private static void KillProcessTree(int pid)
        {
            try
            {
                using var killer = new Process
                {
                    StartInfo = new ProcessStartInfo
                    {
                        FileName = "taskkill",
                        Arguments = $"/F /T /PID {pid}",
                        UseShellExecute = false,
                        CreateNoWindow = true,
                    }
                };
                killer.Start();
                killer.WaitForExit(5000);
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[运行管理器] KillProcessTree 异常: {ex}");
            }
        }
    }
}
