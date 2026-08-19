using System.Text.Json.Serialization;

namespace AiConstruction.Model
{
    #region 请求模型

    /// <summary>
    /// 创建 Agent Run 请求 (POST /api/v1/runs)
    /// </summary>
    public class CreateRunRequest
    {
        /// <summary>业务层生成的本次请求唯一 ID</summary>
        [JsonPropertyName("request_id")]
        public string RequestId { get; set; } = string.Empty;

        /// <summary>业务会话 ID，相同 ID 的 Run 严格串行</summary>
        [JsonPropertyName("conversation_id")]
        public string ConversationId { get; set; } = string.Empty;

        /// <summary>本次新用户消息，不应重复放入 history</summary>
        [JsonPropertyName("user_message")]
        public string UserMessage { get; set; } = string.Empty;

        /// <summary>业务层筛选出的历史 user/assistant 消息</summary>
        [JsonPropertyName("history")]
        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
        public List<HistoryMessage>? History { get; set; }

    }

    /// <summary>
    /// 历史消息（仅允许 user/assistant 角色）
    /// </summary>
    public class HistoryMessage
    {
        [JsonPropertyName("role")]
        public string Role { get; set; } = string.Empty; // "user" | "assistant"

        [JsonPropertyName("content")]
        public string Content { get; set; } = string.Empty;
    }

    #endregion

    #region 响应模型

    /// <summary>
    /// 创建 Run 响应 (POST /api/v1/runs → 202)
    /// </summary>
    public class CreateRunResponse
    {
        [JsonPropertyName("run_id")]
        public string RunId { get; set; } = string.Empty;

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty; // "queued"
    }

    /// <summary>
    /// 取消 Run 响应
    /// </summary>
    public class CancelRunResponse
    {
        [JsonPropertyName("run_id")]
        public string RunId { get; set; } = string.Empty;

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;
    }

    #endregion

    #region SSE 事件模型

    /// <summary>
    /// 通用 SSE 事件基类
    /// </summary>
    public class SseEvent
    {
        [JsonPropertyName("event_type")]
        public string EventType { get; set; } = string.Empty;
    }

    /// <summary>
    /// assistant_message 事件 — AI 回复文本内容
    /// </summary>
    public class AssistantMessageEvent : SseEvent
    {
        /// <summary>消息用途：plan / reasoning / final / input_required 等</summary>
        [JsonPropertyName("phase")]
        public string Phase { get; set; } = string.Empty;

        /// <summary>AI 回复的文本片段</summary>
        [JsonPropertyName("content")]
        public string Content { get; set; } = string.Empty;

        /// <summary>该消息关联的 run_id</summary>
        [JsonPropertyName("run_id")]
        public string RunId { get; set; } = string.Empty;
    }

    /// <summary>
    /// run_completed 事件 — 任务完成
    /// </summary>
    public class RunCompletedEvent : SseEvent
    {
        [JsonPropertyName("run_id")]
        public string RunId { get; set; } = string.Empty;

        [JsonPropertyName("final_answer")]
        public string FinalAnswer { get; set; } = string.Empty;

        [JsonPropertyName("artifacts")]
        public List<object>? Artifacts { get; set; }
    }

    /// <summary>
    /// tool_started / tool_completed 事件 — 工具调用状态
    /// </summary>
    public class ToolEvent : SseEvent
    {
        [JsonPropertyName("tool")]
        public string Tool { get; set; } = string.Empty;

        [JsonPropertyName("summary")]
        public string Summary { get; set; } = string.Empty;

        [JsonPropertyName("success")]
        public bool? Success { get; set; }

        /// <summary>tool_progress 事件中的累计运行秒数</summary>
        [JsonPropertyName("elapsed_seconds")]
        public int? ElapsedSeconds { get; set; }
    }

    /// <summary>
    /// 通用状态事件 (run_queued / run_started / run_failed / run_cancelled)
    /// </summary>
    public class StatusEvent : SseEvent
    {
        [JsonPropertyName("run_id")]
        public string RunId { get; set; } = string.Empty;

        [JsonPropertyName("message")]
        public string? Message { get; set; }
    }

    #endregion
}
