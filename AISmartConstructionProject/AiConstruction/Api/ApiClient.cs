using AiConstruction.Model;
using AiConstruction.Services;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace AiConstruction.Api
{
    /// <summary>
    /// SSE 事件数据 — 从流中解析出的原始事件
    /// </summary>
    public class SseEventData
    {
        /// <summary>事件类型: assistant_message / tool_started / tool_completed / run_completed 等</summary>
        public string EventType { get; set; } = string.Empty;

        /// <summary>data 字段的原始 JSON 字符串</summary>
        public string Data { get; set; } = string.Empty;
    }

    /// <summary>
    /// OpenManus Agent Runtime API 客户端
    /// 封装两步式调用：① 创建 Run  ② SSE 长连接订阅事件
    /// 支持两种构造方式：
    /// 1) 传入 RuntimeManager 创建的 HttpClient（已配置动态端口 + Token）
    /// 2) 无参：使用 ApiConfig.BaseUrl 自动创建
    /// </summary>
    public class ApiClient
    {
        private readonly HttpClient _httpClient;

        /// <summary>使用 RuntimeManager 预配置的 HttpClient</summary>
        public ApiClient(HttpClient httpClient)
        {
            _httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
            LogHelper.Info($"[API] 客户端已创建 (Runtime), BaseAddress: {_httpClient.BaseAddress}");
        }

        /// <summary>使用 ApiConfig.BaseUrl 自动创建</summary>
        public ApiClient()
        {
            _httpClient = new HttpClient
            {
                BaseAddress = new Uri(ApiConfig.BaseUrl),
                Timeout = TimeSpan.FromSeconds(ApiConfig.ConnectTimeoutSeconds)
            };

            if (ApiConfig.IsConfigured)
            {
                _httpClient.DefaultRequestHeaders.Authorization =
                    new AuthenticationHeaderValue("Bearer", ApiConfig.AuthToken);
            }

            LogHelper.Info($"[API] 客户端已创建，BaseUrl: {ApiConfig.BaseUrl}");
        }

        /// <summary>
        /// 步骤一：创建 Agent Run
        /// POST /api/v1/runs
        /// </summary>
        public async Task<CreateRunResponse?> CreateRunAsync(CreateRunRequest request, CancellationToken ct = default)
        {
            var json = JsonSerializer.Serialize(request);
            var content = new StringContent(json, Encoding.UTF8, "application/json");

            var response = await _httpClient.PostAsync("/api/v1/runs", content, ct);

            if (!response.IsSuccessStatusCode)
            {
                var errorBody = await response.Content.ReadAsStringAsync(ct);
                LogHelper.Error($"[API] 创建 Run 失败 ({(int)response.StatusCode}): {errorBody}");
                throw new HttpRequestException(
                    $"创建 Run 失败 ({(int)response.StatusCode}): {errorBody}");
            }

            var responseJson = await response.Content.ReadAsStringAsync(ct);
            var result = JsonSerializer.Deserialize<CreateRunResponse>(responseJson);
            LogHelper.Info($"[API] 创建 Run 成功: run_id={result?.RunId}, status={result?.Status}");
            return result;
        }

        /// <summary>
        /// 步骤二：SSE 长连接订阅 Agent Run 事件
        /// GET /api/v1/runs/{run_id}/events
        /// 返回 IAsyncEnumerable，每个 yield 对应一个完整 SSE 事件
        /// </summary>
        public async IAsyncEnumerable<SseEventData> SubscribeToEventsAsync(
            string runId,
            [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken ct = default)
        {
            var request = new HttpRequestMessage(HttpMethod.Get, $"/api/v1/runs/{runId}/events");
            request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));

            using var response = await _httpClient.SendAsync(
                request,
                HttpCompletionOption.ResponseHeadersRead,
                ct);

            response.EnsureSuccessStatusCode();

            LogHelper.Info($"[API] SSE 连接已建立: run_id={runId}");

            using var stream = await response.Content.ReadAsStreamAsync(ct);
            using var reader = new StreamReader(stream, Encoding.UTF8);

            string? currentEventType = string.Empty;
            var dataBuilder = new StringBuilder();

            string? line;
            while ((line = await reader.ReadLineAsync(ct)) != null)
            {
                ct.ThrowIfCancellationRequested();

                // SSE 协议：空行表示一个事件结束
                if (string.IsNullOrEmpty(line))
                {
                    if (dataBuilder.Length > 0)
                    {
                        var eventType = !string.IsNullOrEmpty(currentEventType)
                            ? currentEventType
                            : "message"; // SSE 默认事件类型

                        yield return new SseEventData
                        {
                            EventType = eventType,
                            Data = dataBuilder.ToString().Trim()
                        };

                        // 重置缓冲区
                        dataBuilder.Clear();
                        currentEventType = string.Empty;
                    }
                    continue;
                }

                // 忽略注释行
                if (line.StartsWith(":"))
                    continue;

                // 解析 event 字段
                if (line.StartsWith("event:"))
                {
                    currentEventType = line.Substring(6).Trim();
                }
                // 解析 data 字段（可能多行）
                else if (line.StartsWith("data:"))
                {
                    var dataValue = line.Substring(5).Trim();
                    if (dataBuilder.Length > 0)
                        dataBuilder.Append('\n');
                    dataBuilder.Append(dataValue);
                }
                // 忽略其他字段 (id:, retry:)
            }
        }

        /// <summary>
        /// 取消一个正在执行或排队的 Run
        /// POST /api/v1/runs/{run_id}/cancel
        /// </summary>
        public async Task<CancelRunResponse?> CancelRunAsync(string runId, CancellationToken ct = default)
        {
            LogHelper.Info($"[API] 取消 Run: run_id={runId}");
            var response = await _httpClient.PostAsync($"/api/v1/runs/{runId}/cancel", null, ct);
            response.EnsureSuccessStatusCode();

            var responseJson = await response.Content.ReadAsStringAsync(ct);
            return JsonSerializer.Deserialize<CancelRunResponse>(responseJson);
        }
    }
}
