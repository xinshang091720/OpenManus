using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace AiConstruction.Services
{
    /// <summary>
    /// Revit API HTTP 代理服务
    /// Runtime 始终连接此代理（固定端口），代理优先路由到上次成功通信的 Revit 实例（模型绑定），
    /// 不再依赖前台窗口检测来确定活跃实例
    /// </summary>
    public class RevitProxyService : IDisposable
    {
        /// <summary>代理端口号（首次访问时从操作系统动态分配空闲端口）</summary>
        private static int? _proxyPort;
        private static readonly object _portInitLock = new();

        /// <summary>获取代理端口（首次访问时自动分配，后续复用）</summary>
        public static int ProxyPort
        {
            get
            {
                if (!_proxyPort.HasValue)
                {
                    lock (_portInitLock)
                    {
                        if (!_proxyPort.HasValue)
                            _proxyPort = GetAvailablePort();
                    }
                }
                return _proxyPort.Value;
            }
        }

        /// <summary>让操作系统分配一个空闲的 TCP 端口</summary>
        private static int GetAvailablePort()
        {
            var listener = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
            try
            {
                listener.Start();
                int port = ((IPEndPoint)listener.LocalEndpoint).Port;
                listener.Stop();
                LogHelper.Info($"[代理服务] 系统分配空闲端口: {port}");
                return port;
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[代理服务] 获取空闲端口失败: {ex.Message}");
                throw;
            }
        }

        private HttpListener? _listener;
        private CancellationTokenSource? _cts;
        private Task? _listenTask;
        private bool _disposed;

        private readonly RevitPortDetector _detector;
        private readonly HttpClient _forwardClient;

        /// <summary>上次成功处理 Revit API 请求的端口（模型→端口绑定核心）</summary>
        private int? _lastSuccessfulPort;
        private readonly object _portLock = new();

        /// <summary>代理地址（Runtime 连接此地址）</summary>
        public string ProxyUrl => $"http://localhost:{ProxyPort}";

        public RevitProxyService(RevitPortDetector detector)
        {
            _detector = detector;
            _forwardClient = new HttpClient
            {
                // 最大合法允许值：int.MaxValue -1 毫秒 ≈24天，业务完全等价无限制
                Timeout = TimeSpan.FromMilliseconds(int.MaxValue - 1)
            };

            // 当所有 Revit 退出时，清除端口缓存
            _detector.ActivePortChanged += (_, newPort) =>
            {
                if (newPort == null)
                {
                    lock (_portLock)
                    {
                        _lastSuccessfulPort = null;
                        LogHelper.Info("[代理服务] Revit 已全部退出，清除端口缓存");
                    }
                }
            };
        }

        /// <summary>启动代理监听（动态端口，无冲突风险）</summary>
        public void Start()
        {
            int maxRetries = 3;
            for (int attempt = 1; attempt <= maxRetries; attempt++)
            {
                int port = ProxyPort;

                try
                {
                    _cts = new CancellationTokenSource();
                    _listener = new HttpListener();
                    _listener.Prefixes.Add($"http://localhost:{port}/api/");
                    _listener.Prefixes.Add($"http://127.0.0.1:{port}/api/");
                    _listener.Start();

                    _listenTask = Task.Run(() => ListenLoop(_cts.Token));

                    LogHelper.Info($"[代理服务] 启动成功，端口: {port} (动态分配)");
                    return;
                }
                catch (HttpListenerException ex)
                {
                    LogHelper.Warn($"[代理服务] 端口 {port} 启动失败 (错误码: {ex.ErrorCode})，第 {attempt} 次尝试");

                    // 释放当前端口，下次 GetAvailablePort 重新分配
                    lock (_portInitLock)
                    {
                        _proxyPort = null;
                    }

                    _listener?.Close();
                    _listener = null;
                    _cts?.Cancel();
                    _cts?.Dispose();
                    _cts = null;

                    if (attempt < maxRetries)
                    {
                        Thread.Sleep(100);
                        continue;
                    }

                    LogHelper.Error($"[代理服务] 启动失败，已尝试 {maxRetries} 次: {ex.Message}");
                }
                catch (Exception ex)
                {
                    LogHelper.Error($"[代理服务] 启动失败: {ex.Message}");
                    break;
                }
            }
        }

        private async Task ListenLoop(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested && _listener?.IsListening == true)
            {
                try
                {
                    var ctx = await _listener.GetContextAsync();
                    _ = ForwardRequest(ctx, ct);
                }
                catch (HttpListenerException) { break; }
                catch (OperationCanceledException) { break; }
                catch (ObjectDisposedException) { break; }
                catch (Exception ex)
                {
                    LogHelper.Error($"[代理服务] 监听循环异常: {ex.Message}");
                }
            }
        }

        /// <summary>
        /// 解析目标端口：
        /// - OpenRevitFile（打开模型）：不使用缓存，强制使用 ActivePort（让新 Revit 实例能被选中）
        /// - 其他 Revit API：优先使用模型绑定缓存端口
        /// - 非 Revit 请求：降级为前台窗口检测
        /// </summary>
        private int? ResolveTargetPort(string path)
        {
            // 打开模型操作：不使用缓存，强制走 ActivePort（确保新实例能被路由到）
            if (IsOpenModelPath(path))
            {
                return _detector.ActivePort;
            }

            // 其他 Revit API 请求：优先使用缓存端口
            if (IsRevitApiPath(path))
            {
                lock (_portLock)
                {
                    if (_lastSuccessfulPort.HasValue)
                    {
                        var stillAlive = _detector.ActiveInstances.Values.Contains(_lastSuccessfulPort.Value);
                        if (stillAlive)
                        {
                            return _lastSuccessfulPort.Value;
                        }
                        else
                        {
                            LogHelper.Warn("[代理服务] 模型绑定端口已失效，清除缓存");
                            _lastSuccessfulPort = null;
                        }
                    }
                }
            }

            // 降级：使用前台窗口检测
            return _detector.ActivePort;
        }

        /// <summary>转发单个请求到活跃 Revit</summary>
        private async Task ForwardRequest(HttpListenerContext ctx, CancellationToken ct)
        {
            var req = ctx.Request;
            var path = req.Url!.AbsolutePath;

            try
            {
                // 1. 先读取请求体（后续转发也需要）
                string? body = null;
                if (req.HasEntityBody)
                {
                    using var reader = new StreamReader(req.InputStream, req.ContentEncoding ?? Encoding.UTF8);
                    body = await reader.ReadToEndAsync();
                }

                // 2. 确定目标端口：优先使用模型绑定端口，降级为前台窗口检测
                var targetPort = ResolveTargetPort(path);
                if (!targetPort.HasValue)
                {
                    LogHelper.Warn($"[代理服务] 无可用 Revit 实例，拒绝请求: {path}");
                    await WriteError(ctx.Response, 503, "没有可用的 Revit 实例，请确认 Revit 已启动并加载插件");
                    return;
                }

                // 旧版 Revit 插件只接受 POST，因此其本体没有 GET /Health。
                // Runtime 的 health 轮询只需要确认本机代理已经找到一个可路由的
                // Revit 实例；真实工具调用仍会照常转发并返回插件的精确错误。
                if (IsHealthCheckPath(path) && string.Equals(req.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase))
                {
                    await WriteJson(ctx.Response, 200,
                        "{\"code\":200,\"msg\":\"Revit proxy routing is ready\",\"pluginVersion\":\"legacy-proxy\",\"revitConnected\":true,\"readyForRequests\":true}");
                    return;
                }

                var targetUrl = $"http://localhost:{targetPort.Value}{req.Url!.PathAndQuery}";
                LogHelper.Info($"[代理服务] 转发 {req.HttpMethod} {path} → 端口 {targetPort.Value}");

                // 3. 构建转发请求
                var method = new HttpMethod(req.HttpMethod);
                using var fwdReq = new HttpRequestMessage(method, targetUrl);

                // 复制关键 headers
                var auth = req.Headers["Authorization"];
                if (!string.IsNullOrEmpty(auth))
                    fwdReq.Headers.TryAddWithoutValidation("Authorization", auth);

                var contentType = req.ContentType;
                if (!string.IsNullOrEmpty(contentType))
                    fwdReq.Headers.TryAddWithoutValidation("Content-Type", contentType);

                // 复制 Body
                if (body != null)
                    fwdReq.Content = new StringContent(body, Encoding.UTF8, contentType ?? "application/json");

                // 4. 发送转发请求
                using var fwdResp = await _forwardClient.SendAsync(fwdReq, ct);
                var respBody = await fwdResp.Content.ReadAsStringAsync();

                // 5. 成功后缓存端口：建立模型→Revit 绑定
                if (fwdResp.IsSuccessStatusCode && IsRevitApiPath(path))
                {
                    lock (_portLock)
                    {
                        if (_lastSuccessfulPort != targetPort.Value)
                        {
                            _lastSuccessfulPort = targetPort.Value;
                            LogHelper.Info($"[代理服务] 建立模型绑定 → 端口 {targetPort.Value}");
                        }
                    }
                }

                // 6. 回写响应
                ctx.Response.StatusCode = (int)fwdResp.StatusCode;
                ctx.Response.ContentType = fwdResp.Content.Headers.ContentType?.ToString() ?? "application/json; charset=utf-8";

                var respBytes = Encoding.UTF8.GetBytes(respBody);
                ctx.Response.ContentLength64 = respBytes.Length;
                await ctx.Response.OutputStream.WriteAsync(respBytes, 0, respBytes.Length, ct);
            }
            catch (HttpRequestException ex)
            {
                LogHelper.Error($"[代理服务] 连接 Revit 失败 ({path}): {ex.Message}");
                await WriteError(ctx.Response, 502, $"连接 Revit 失败: {ex.Message}");
            }
            catch (TaskCanceledException)
            {
                LogHelper.Error($"[代理服务] 请求 Revit 超时 ({path})");
                await WriteError(ctx.Response, 504, "请求 Revit 超时");
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[代理服务] 转发异常 ({path}): {ex.Message}");
                await WriteError(ctx.Response, 500, $"代理转发异常: {ex.Message}");
            }
            finally
            {
                ctx.Response.OutputStream.Close();
                ctx.Response.Close();
            }
        }

        /// <summary>判断是否为 Revit API 请求路径</summary>
        private static bool IsRevitApiPath(string path)
        {
            return path.Contains("/RevitApi", StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>判断是否为打开模型请求（OpenRevitFile）</summary>
        private static bool IsOpenModelPath(string path)
        {
            return path.Contains("/RevitApi", StringComparison.OrdinalIgnoreCase) &&
                   path.Contains("OpenRevitFile", StringComparison.OrdinalIgnoreCase);
        }

        private static bool IsHealthCheckPath(string path)
        {
            return path.EndsWith("/RevitApi/Health", StringComparison.OrdinalIgnoreCase);
        }

        private static async Task WriteError(HttpListenerResponse resp, int code, string msg)
        {
            try
            {
                var json = $"{{\"code\":{code},\"msg\":\"{msg}\"}}";
                await WriteJson(resp, code, json);
            }
            catch { }
        }

        private static async Task WriteJson(HttpListenerResponse resp, int statusCode, string json)
        {
            resp.StatusCode = statusCode;
            var buf = Encoding.UTF8.GetBytes(json);
            resp.ContentType = "application/json; charset=utf-8";
            resp.ContentLength64 = buf.Length;
            await resp.OutputStream.WriteAsync(buf, 0, buf.Length);
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;

            try
            {
                _cts?.Cancel();
                _listener?.Stop();
                _listenTask?.Wait(3000);
                _listener?.Close();
                _forwardClient.Dispose();
                _cts?.Dispose();
            }
            catch { }
            LogHelper.Info("[代理服务] 已停止");
        }
    }
}
