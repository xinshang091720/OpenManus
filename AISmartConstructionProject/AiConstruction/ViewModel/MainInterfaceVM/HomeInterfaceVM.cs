using AiConstruction.Api;
using AiConstruction.Model;
using AiConstruction.Services;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System.Collections.ObjectModel;
using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace AiConstruction.ViewModel
{
    public class HomeInterfaceVM : ObservableObject
    {
        #region API 客户端 & 对话上下文

        /// <summary>API 客户端（延迟创建，确保 Runtime 就绪后使用正确端口和 Token）</summary>
        private ApiClient? _apiClient;

        /// <summary>获取或创建 ApiClient（线程安全）</summary>
        private ApiClient ApiClient
        {
            get
            {
                if (_apiClient == null)
                {
                    var httpClient = App.Runtime.CreateApiClient();
                    _apiClient = new ApiClient(httpClient);
                }
                return _apiClient;
            }
        }

        private readonly List<HistoryMessage> _history = new();
        private string _sessionGroupId = CreateSessionGroupId();
        private string? _currentRunId;

        private static string CreateSessionGroupId() => $"conv-{Guid.NewGuid():N}";

        /// <summary>当前业务会话组 ID；Runtime 仅用它串行化同会话 Run。</summary>
        public string SessionGroupId => _sessionGroupId;

        /// <summary>新建聊天时调用，确保新会话拥有新的串行键。</summary>
        public void StartNewConversation()
        {
            CancelCurrentRun();
            _sessionGroupId = CreateSessionGroupId();
            _history.Clear();
            Messages.Clear();
        }

        /// <summary>切换历史聊天时使用业务库保存的 SessionGroupId 和消息。</summary>
        public void RestoreConversation(
            string sessionGroupId,
            IEnumerable<HistoryMessage> history)
        {
            if (string.IsNullOrWhiteSpace(sessionGroupId))
                throw new ArgumentException("历史会话缺少 SessionGroupId", nameof(sessionGroupId));
            CancelCurrentRun();
            _sessionGroupId = sessionGroupId;
            _history.Clear();
            _history.AddRange(history.Where(item =>
                (item.Role is "user" or "assistant") && !string.IsNullOrWhiteSpace(item.Content)));
        }

        #endregion

        #region 消息集合（气泡聊天核心）

        /// <summary>聊天消息列表</summary>
        public ObservableCollection<ChatMessage> Messages { get; } = new();

        #endregion

        #region UI 绑定属性

        private Visibility hideSidebar = Visibility.Visible;
        public Visibility HideSidebar
        {
            get => hideSidebar;
            set => SetProperty(ref hideSidebar, value);
        }

        private string sendContent = string.Empty;
        public string SendContent
        {
            get => sendContent;
            set => SetProperty(ref sendContent, value);
        }

        public ScrollViewer? ScrollViewer { get; set; }

        private Visibility initializeTitie = Visibility.Visible;
        public Visibility InitializeTitie
        {
            get => initializeTitie;
            set => SetProperty(ref initializeTitie, value);
        }

        /// <summary>是否有对话内容（控制对话区域可见性）</summary>
        private Visibility dialogueContent = Visibility.Collapsed;
        public Visibility DialogueContent
        {
            get => dialogueContent;
            set => SetProperty(ref dialogueContent, value);
        }

        private int sidebarColumnWidth = 268;
        public int SidebarColumnWidth
        {
            get => sidebarColumnWidth;
            set => SetProperty(ref sidebarColumnWidth, value);
        }

        private bool isLoading;
        public bool IsLoading
        {
            get => isLoading;
            set => SetProperty(ref isLoading, value);
        }

        #endregion

        #region 流控 & 取消

        private CancellationTokenSource? _runCts;

        #endregion

        #region 窗口命令

        public RelayCommand<Window> MinimizeCommand => new(MinimizeWindow);
        public RelayCommand<Window> CloseCommand => new(MinimizeWindow);  // 关闭实际是最小化到托盘

        private static void MinimizeWindow(Window? window)
        {
            if (window != null) window.WindowState = WindowState.Minimized;
        }

        #endregion

        #region 侧边栏命令

        public RelayCommand HideSidebarCommand => new(() =>
        {
            if (HideSidebar == Visibility.Visible)
            {
                HideSidebar = Visibility.Collapsed;
                SidebarColumnWidth = 0;
            }
            else
            {
                HideSidebar = Visibility.Visible;
                SidebarColumnWidth = 268;
            }
        });

        #endregion

        #region 发送 & 停止命令

        public RelayCommand<TextBox> SendContentCommand => new(async (textbox) =>
        {
            if (string.IsNullOrWhiteSpace(SendContent)) return;

            var userMessage = SendContent.Trim();
            SendContent = string.Empty;

            LogHelper.Info($"[对话] 用户发送消息 (len={userMessage.Length}): {userMessage[..Math.Min(userMessage.Length, 80)]}");

            // 切换到对话视图
            DialogueContent = Visibility.Visible;
            InitializeTitie = Visibility.Collapsed;

            // Runtime 未就绪 → 提示等待
            if (!App.Runtime.IsReady)
            {
                // 先显示用户消息
                Messages.Add(new ChatMessage { IsUser = true, Content = userMessage });

                Messages.Add(new ChatMessage
                {
                    IsUser = false,
                    Content = "⏳ AI 引擎正在启动中，请稍候再试...",
                    IsThinking = false
                });
                ScrollToEnd();
                return;
            }

            // 取消上一轮任务
            CancelCurrentRun();

            _runCts = new CancellationTokenSource();
            var ct = _runCts.Token;

            IsLoading = true;

            // ====== 添加用户气泡（右侧） ======
            var userBubble = new ChatMessage { IsUser = true, Content = userMessage };
            Messages.Add(userBubble);

            // ====== 添加 AI 初始思考气泡（左侧） ======
            var aiBubble = new ChatMessage
            {
                IsUser = false,
                Content = string.Empty,
                IsThinking = true,
                ThinkingText = "AI 正在思考"
            };
            Messages.Add(aiBubble);
          

            // 当前活跃的 AI 文本气泡（用于接收 assistant_message）
            ChatMessage? currentAiTextBubble = aiBubble;

            // 记录 AI 回复内容（用于写入历史）
            var aiFullContent = string.Empty;
            // 仅持久化最终答复。计划/推理事件用于 UI 展示，不能作为下一轮模型上下文。
            var finalAssistantContent = string.Empty;

            // Runtime 进程退出标记（用于区分用户取消 vs 进程崩溃）
            var runtimeExited = false;
            EventHandler? onRuntimeExited = null;

            try
            {
                // 1. 创建 Agent Run
                var request = new CreateRunRequest
                {
                    RequestId = $"req-{Guid.NewGuid():N}",
                    ConversationId = _sessionGroupId,
                    UserMessage = userMessage,
                    History = _history.Count > 0 ? new List<HistoryMessage>(_history) : null
                };

                LogRunRequestContext(request);

                var createResponse = await ApiClient.CreateRunAsync(request, ct);

                if (createResponse == null)
                {
                    LogHelper.Error("[对话] CreateRunAsync 返回 null");
                    currentAiTextBubble.IsThinking = false;
                    currentAiTextBubble.Content = "> ⚠️ 创建任务失败，请检查后端服务是否正常运行。";
                    ScrollToEnd();
                    return;
                }

                // API 成功 → 记录用户消息到历史
                _history.Add(new HistoryMessage { Role = "user", Content = userMessage });

                _currentRunId = createResponse.RunId;
                LogHelper.Info($"[对话] Run 已创建: {_currentRunId}, status: {createResponse.Status}");

                // 订阅 Runtime 进程退出事件，进程崩溃时主动取消 SSE 读取
                onRuntimeExited = (_, _) =>
                {
                    runtimeExited = true;
                    LogHelper.Warn("[对话] Runtime 进程已退出，取消当前 SSE 读取");
                    _runCts?.Cancel();
                };
                App.Runtime.ProcessExited += onRuntimeExited;

                try
                {
                    // 2. SSE 长连接订阅事件
                    await foreach (var sseEvent in ApiClient.SubscribeToEventsAsync(_currentRunId, ct))
                {
                    LogHelper.Info(
                        $"[SSE] event={sseEvent.EventType}, data_len={sseEvent.Data.Length}");

                    switch (sseEvent.EventType)
                    {
                        case "assistant_message":
                            // 确保有活跃的 AI 文本气泡
                            if (currentAiTextBubble == null)
                            {
                                currentAiTextBubble = new ChatMessage
                                {
                                    IsUser = false,
                                    Content = string.Empty,
                                    IsThinking = false
                                };
                                Messages.Add(currentAiTextBubble);
                            }

                            currentAiTextBubble.IsThinking = false;
                            var assistantEvent = ParseAssistantMessage(sseEvent.Data);
                            var chunk = assistantEvent?.Content ?? string.Empty;
                            if (!string.IsNullOrEmpty(chunk))
                            {
                                aiFullContent += chunk;
                                if (string.Equals(assistantEvent?.Phase, "final", StringComparison.OrdinalIgnoreCase))
                                    finalAssistantContent = chunk;
                                await AppendChunkToBubbleAsync(currentAiTextBubble, chunk, ct);
                            }
                            break;

                        case "run_completed":
                            HandleRunCompleted(currentAiTextBubble, sseEvent.Data);
                            finalAssistantContent = ParseRunCompletedAnswer(sseEvent.Data) ?? finalAssistantContent;
                            break;

                        case "run_failed":
                            if (currentAiTextBubble != null)
                            {
                                currentAiTextBubble.IsThinking = false;
                                HandleRunFailed(currentAiTextBubble, sseEvent.Data);
                            }
                            break;

                        case "run_cancelled":
                            if (currentAiTextBubble != null)
                            {
                                currentAiTextBubble.IsThinking = false;
                                currentAiTextBubble.Content += "\n\n> ⚠️ 任务已被取消。";
                                ScrollToEnd();
                            }
                            break;

                        case "run_queued":
                            if (currentAiTextBubble != null)
                                currentAiTextBubble.ThinkingText = "⏳ 任务已加入队列...";
                            break;

                        case "run_started":
                            if (currentAiTextBubble != null)
                                currentAiTextBubble.ThinkingText = "AI 正在思考";
                            break;

                        case "skill_activated":
                        case "workflow_stage_started":
                            if (currentAiTextBubble != null)
                            {
                                var summary = ParseLifecycleSummary(sseEvent.Data, "Preparing domain capability");
                                currentAiTextBubble.Steps.Add(new ToolStep
                                {
                                    Title = summary,
                                    Content = summary,
                                    Icon = "🔧",
                                    IsSuccess = true,
                                    IsCompleted = true,
                                    StepNumber = currentAiTextBubble.Steps.Count + 1
                                });
                                ScrollToEnd();
                            }
                            break;

                        case "workflow_stage_completed":
                        case "workflow_stage_failed":
                            if (currentAiTextBubble != null)
                            {
                                var summary = ParseLifecycleSummary(sseEvent.Data, "Workflow stage finished");
                                currentAiTextBubble.Steps.Add(new ToolStep
                                {
                                    Title = summary,
                                    Content = summary,
                                    Icon = sseEvent.EventType == "workflow_stage_failed" ? "❌" : "✅",
                                    IsSuccess = sseEvent.EventType != "workflow_stage_failed",
                                    IsCompleted = true,
                                    StepNumber = currentAiTextBubble.Steps.Count + 1
                                });
                                ScrollToEnd();
                            }
                            break;

                        case "tool_started":
                            // 创建新的可折叠步骤并加入列表
                            if (currentAiTextBubble != null)
                            {
                                var toolInfo = ParseToolEvent(sseEvent.Data);
                                if (toolInfo != null)
                                {
                                    var desc = !string.IsNullOrEmpty(toolInfo.Summary)
                                        ? toolInfo.Summary
                                        : $"正在执行 {toolInfo.Tool}";

                                    var step = new ToolStep
                                    {
                                        Tool = toolInfo.Tool,
                                        Title = desc,
                                        Icon = "🔧",
                                        IsSuccess = false,
                                        IsCompleted = false,
                                        StepNumber = currentAiTextBubble.Steps.Count + 1
                                    };
                                    currentAiTextBubble.Steps.Add(step);

                                    // 首次工具调用时记录开始时间
                                    if (currentAiTextBubble.StepsStartTime == null)
                                        currentAiTextBubble.StepsStartTime = DateTime.Now;
                                }
                                // 工具执行期间显示"AI 正在思考..."
                                currentAiTextBubble.IsThinking = true;
                                currentAiTextBubble.ThinkingText = "AI 正在思考";
                                ScrollToEnd();
                            }
                            break;

                        case "tool_progress":
                            // 只更新现有工具步骤，不创建聊天气泡，也不写入 history。
                            if (currentAiTextBubble != null)
                            {
                                var toolInfo = ParseToolEvent(sseEvent.Data);
                                if (toolInfo != null)
                                {
                                    var step = currentAiTextBubble.Steps.LastOrDefault(item =>
                                        !item.IsCompleted && item.Tool == toolInfo.Tool);
                                    if (step != null)
                                    {
                                        var elapsed = FormatElapsedSeconds(toolInfo.ElapsedSeconds ?? 0);
                                        var summary = string.IsNullOrWhiteSpace(toolInfo.Summary)
                                            ? $"正在执行 {toolInfo.Tool}"
                                            : toolInfo.Summary.TrimEnd('。');
                                        step.Title = $"{summary}，已运行 {elapsed}";
                                        step.Content = step.Title;
                                        currentAiTextBubble.ThinkingText = step.Title;
                                        currentAiTextBubble.StepsHeaderText = $"正在处理，已运行 {elapsed}";
                                        ScrollToEnd();
                                    }
                                }
                            }
                            break;

                        case "tool_completed":
                            // 更新最后一步的状态和详细描述
                            if (currentAiTextBubble != null)
                            {
                                var toolInfo = ParseToolEvent(sseEvent.Data);
                                if (toolInfo != null && currentAiTextBubble.Steps.Count > 0)
                                {
                                    var lastStep = currentAiTextBubble.Steps.LastOrDefault(item =>
                                        !item.IsCompleted && item.Tool == toolInfo.Tool)
                                        ?? currentAiTextBubble.Steps[currentAiTextBubble.Steps.Count - 1];
                                    lastStep.IsSuccess = toolInfo.Success == true;
                                    lastStep.IsCompleted = true;
                                    lastStep.Icon = toolInfo.Success == true ? "✅" : "❌";
                                    lastStep.Content = !string.IsNullOrEmpty(toolInfo.Summary)
                                        ? toolInfo.Summary
                                        : $"{toolInfo.Tool} 执行{(toolInfo.Success == true ? "成功" : "失败")}";

                                    // 更新折叠头为已完成+耗时
                                    currentAiTextBubble.StepsHeaderText =
                                        $"已完成 {FormatElapsed(currentAiTextBubble.StepsStartTime)}";
                                    ScrollToEnd();
                                }
                            }
                            break;
                    }
                }

                    // 记录 AI 回复到历史
                    if (!string.IsNullOrWhiteSpace(finalAssistantContent))
                    {
                        _history.Add(new HistoryMessage { Role = "assistant", Content = finalAssistantContent });
                    }
                }
                finally
                {
                    App.Runtime.ProcessExited -= onRuntimeExited;
                }
            }
            catch (OperationCanceledException)
            {
                if (runtimeExited)
                {
                    LogHelper.Warn("[对话] 任务因 Runtime 进程退出而中断");
                    if (currentAiTextBubble != null)
                    {
                        currentAiTextBubble.IsThinking = false;
                        if (string.IsNullOrEmpty(currentAiTextBubble.Content))
                            currentAiTextBubble.Content = "> ⚠️ AI 引擎意外停止，请重启应用后重试。";
                    }
                }
                else
                {
                    LogHelper.Info("[对话] 任务被用户取消");
                    if (currentAiTextBubble != null)
                    {
                        currentAiTextBubble.IsThinking = false;
                        if (string.IsNullOrEmpty(currentAiTextBubble.Content))
                            currentAiTextBubble.Content = "> ⏸️ 已手动停止。";
                    }
                }
            }
            catch (HttpRequestException ex)
            {
                LogHelper.Error($"[对话] 网络请求失败: {ex.Message}");
                if (currentAiTextBubble != null)
                {
                    currentAiTextBubble.IsThinking = false;
                    currentAiTextBubble.Content = $"> ❌ 网络请求失败: {ex.Message}";
                }
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[对话] 未处理异常: {ex}");
                if (currentAiTextBubble != null)
                {
                    currentAiTextBubble.IsThinking = false;
                    currentAiTextBubble.Content = $"> ❌ 发生错误: {ex.Message}";
                }
            }
            finally
            {
                LogHelper.Info($"[对话] Run 结束: {_currentRunId}, 回复长度={aiFullContent.Length}");
                if (currentAiTextBubble != null)
                {
                    // 最终清理正文中的步骤描述
                    currentAiTextBubble.Content = CleanContentSteps(currentAiTextBubble.Content);
                    currentAiTextBubble.IsThinking = false;

                    if (currentAiTextBubble.Steps.Count > 0)
                    {
                        currentAiTextBubble.StepsHeaderText =
                            $"已完成 {FormatElapsed(currentAiTextBubble.StepsStartTime)}";
                    }
                }
                IsLoading = false;
                _currentRunId = null;
            }
        });

        public RelayCommand StopCommand => new(async () =>
        {
            if (_currentRunId != null)
            {
                try
                {
                    if (_apiClient != null)
                        await _apiClient.CancelRunAsync(_currentRunId);
                }
                catch (Exception ex)
                {
                    LogHelper.Error($"[对话] 取消 Run 失败: {ex.Message}");
                }
            }
            CancelCurrentRun();
        });

        #endregion

        #region SSE 事件处理

        /// <summary>
        /// 将新内容追加到 AI 气泡已有内容之后，仅对新部分做逐字打字动画
        /// </summary>
        /// <summary>匹配步骤描述前缀，如 "1. 正在执行 str_replace_editor。"</summary>
        private static readonly Regex StepDescPattern = new(
            @"\d+\.\s*正在执行\s+\w+\s*[。:：]\s*",
            RegexOptions.Compiled);

        /// <summary>匹配完整的步骤描述行</summary>
        private static readonly Regex StepDescLinePattern = new(
            @"^\s*\d+\.\s*正在执行\s+\w+\s*[。:：][^\n]*\n?",
            RegexOptions.Compiled | RegexOptions.Multiline);

        /// <summary>匹配行内代码，转换为加粗文本以便当做正文复制</summary>
        private static readonly Regex InlineCodePattern = new(
            @"(?<![`\\])`([^`\n]+)`(?![`\\])",
            RegexOptions.Compiled);

        /// <summary>过滤 chunk 中的步骤描述前缀</summary>
        private static string CleanStepChunk(string text)
        {
            if (string.IsNullOrEmpty(text)) return text;
            return StepDescPattern.Replace(text, "");
        }

        /// <summary>最终清理 Content 中的步骤描述行</summary>
        private static string CleanContentSteps(string content)
        {
            if (string.IsNullOrEmpty(content)) return content;
            var cleaned = StepDescLinePattern.Replace(content, "");
            cleaned = InlineCodePattern.Replace(cleaned, "**$1**");
            cleaned = Regex.Replace(cleaned, @"\n{3,}", "\n\n");
            return cleaned.Trim();
        }

        /// <summary>将行内代码替换为加粗文本，使其可像正文一样复制</summary>
        private static string InlineCodeToBold(string text)
        {
            if (string.IsNullOrEmpty(text)) return text;
            return InlineCodePattern.Replace(text, "**$1**");
        }

        private async Task AppendChunkToBubbleAsync(ChatMessage bubble, string chunk, CancellationToken ct)
        {
            // 实时过滤步骤描述，避免思考过程混入正文
            chunk = CleanStepChunk(chunk);
            if (string.IsNullOrEmpty(chunk)) return;

            var existing = bubble.Content ?? string.Empty;
            var totalLen = chunk.Length;
            var batchSize = totalLen > 2000 ? 10 : 3;
            var delayMs = totalLen > 2000 ? 8 : 20;

            var typed = string.Empty;
            for (int i = 0; i < totalLen; i += batchSize)
            {
                ct.ThrowIfCancellationRequested();

                var count = Math.Min(batchSize, totalLen - i);
                typed += chunk.Substring(i, count);

                var display = InlineCodeToBold(existing + typed);
                await Application.Current.Dispatcher.InvokeAsync(() =>
                {
                    bubble.Content = display;
                    ScrollToEnd();
                }, DispatcherPriority.Normal, ct);

                await Task.Delay(delayMs, ct);
            }
        }

        private static AssistantMessageEvent? ParseAssistantMessage(string data)
        {
            try
            {
                var msg = JsonSerializer.Deserialize<AssistantMessageEvent>(data);
                if (!string.IsNullOrEmpty(msg?.Content))
                    return msg;
            }
            catch (JsonException) { }
            return null;
        }

        private static string? ParseRunCompletedAnswer(string data)
        {
            try
            {
                return JsonSerializer.Deserialize<RunCompletedEvent>(data)?.FinalAnswer;
            }
            catch (JsonException)
            {
                return null;
            }
        }

        /// <summary>
        /// 解析 tool_started / tool_completed 事件中的工具信息
        /// </summary>
        private static ToolEvent? ParseToolEvent(string data)
        {
            try
            {
                return JsonSerializer.Deserialize<ToolEvent>(data);
            }
            catch
            {
                return null;
            }
        }

        /// <summary>格式化已用时间（如 "3s"、"1m35s"）</summary>
        private static string ParseLifecycleSummary(string data, string fallback)
        {
            try
            {
                using var document = JsonDocument.Parse(data);
                if (document.RootElement.TryGetProperty("summary", out var summary) &&
                    !string.IsNullOrWhiteSpace(summary.GetString()))
                    return summary.GetString()!;
                if (document.RootElement.TryGetProperty("skill_id", out var skillId))
                    return $"Activated {skillId.GetString()}";
                if (document.RootElement.TryGetProperty("stage", out var stage))
                    return stage.GetString() ?? fallback;
            }
            catch (JsonException) { }
            return fallback;
        }

        private static void LogRunRequestContext(CreateRunRequest request)
        {
            var history = request.History ?? new List<HistoryMessage>();
            var roles = string.Join(",", history.Select(item => item.Role));
            var lastAssistant = history.LastOrDefault(item => item.Role == "assistant")?.Content ?? string.Empty;
            var assistantPreview = lastAssistant[..Math.Min(lastAssistant.Length, 160)]
                .Replace("\r", " ").Replace("\n", " ");
            var userPreview = request.UserMessage[..Math.Min(request.UserMessage.Length, 160)]
                .Replace("\r", " ").Replace("\n", " ");
            LogHelper.Info(
                $"[对话上下文] conversation_id={request.ConversationId}, " +
                $"history_count={history.Count}, roles=[{roles}], " +
                $"last_assistant_preview={assistantPreview}, user_message={userPreview}");
        }

        private static string FormatElapsedSeconds(int elapsedSeconds)
        {
            var elapsed = TimeSpan.FromSeconds(Math.Max(0, elapsedSeconds));
            if (elapsed.TotalHours >= 1)
                return $"{(int)elapsed.TotalHours} 小时 {elapsed.Minutes} 分钟";
            if (elapsed.TotalMinutes >= 1)
                return $"{(int)elapsed.TotalMinutes} 分 {elapsed.Seconds} 秒";
            return $"{elapsed.Seconds} 秒";
        }

        private static string FormatElapsed(DateTime? startTime)
        {
            if (startTime == null) return "0s";
            var elapsed = DateTime.Now - startTime.Value;
            if (elapsed.TotalMinutes >= 1)
                return $"{(int)elapsed.TotalMinutes}m{elapsed.Seconds}s";
            return $"{elapsed.Seconds}s";
        }

        private void HandleRunCompleted(ChatMessage? bubble, string data)
        {
            if (bubble != null)
            {
                bubble.IsThinking = false;
                if (bubble.Steps.Count > 0)
                {
                    bubble.StepsHeaderText =
                        $"已完成 {FormatElapsed(bubble.StepsStartTime)}";
                }
            }

            try
            {
                var completed = JsonSerializer.Deserialize<RunCompletedEvent>(data);

                // 如果 assistant_message 没推送内容，兜底用 final_answer
                if (completed != null && !string.IsNullOrEmpty(completed.FinalAnswer))
                {
                    if (bubble != null && string.IsNullOrEmpty(bubble.Content))
                    {
                        Application.Current.Dispatcher.Invoke(() =>
                        {
                            bubble.Content = completed.FinalAnswer;
                            ScrollToEnd();
                        });
                    }
                }
            }
            catch { }
        }

        private void HandleRunFailed(ChatMessage bubble, string data)
        {
            try
            {
                var failed = JsonSerializer.Deserialize<StatusEvent>(data);
                var msg = failed?.Message ?? "未知错误";
                LogHelper.Error($"[对话] Run 执行失败: {msg}");
                Application.Current.Dispatcher.Invoke(() =>
                {
                    bubble.Content += $"\n\n> ❌ 任务执行失败: {msg}";
                    ScrollToEnd();
                });
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[对话] 解析 run_failed 事件失败: {ex.Message}, raw: {data}");
                Application.Current.Dispatcher.Invoke(() =>
                {
                    bubble.Content += "\n\n> ❌ 任务执行失败。";
                    ScrollToEnd();
                });
            }
        }

        #endregion

        #region UI 辅助

        /// <summary>滚动到聊天底部</summary>
        private void ScrollToEnd()
        {
            Application.Current.Dispatcher.BeginInvoke(() =>
            {
                if (ScrollViewer == null) return;
                ScrollViewer.UpdateLayout();
                ScrollViewer.ScrollToEnd();
            }, DispatcherPriority.Background);
        }

        #endregion

        #region 清理

        private void CancelCurrentRun()
        {
            if (_runCts != null)
            {
                _runCts.Cancel();
                _runCts.Dispose();
                _runCts = null;
            }
        }

        #endregion
    }
}
