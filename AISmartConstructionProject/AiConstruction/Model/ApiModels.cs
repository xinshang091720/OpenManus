using CommunityToolkit.Mvvm.ComponentModel;
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
        // 新增附件数组
        [JsonPropertyName("attachments")]
        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
        public List<object>? Attachments { get; set; }
    }
    // 附件实体
    public class AttachmentItem
    {
        [JsonPropertyName("path")]
        public string Path { get; set; } = string.Empty;
        [JsonPropertyName("type")]
        public string Type { get; set; } = string.Empty;
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
        [JsonPropertyName("topic")]
        public string Topic { get; set; } = string.Empty; // "queued"
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

    #region 账号服务 API 模型

    
    /// <summary>
    /// 通用响应基类
    /// </summary>
    public class BaseResponse
    {
        [JsonPropertyName("code")]
        public int Code { get; set; }

        [JsonPropertyName("msg")]
        public string Message { get; set; } = string.Empty;

        public bool IsSuccess => Code == 200;

      
    }

    /// <summary>
    /// 发送验证码响应
    /// </summary>
    public class SendCaptchaResponse : BaseResponse
    {
    }

    //授权码
    public class Authorizatio: BaseResponse
    {
        public int data { get; set; }
    }
  

    /// <summary>
    /// 登录响应数据
    /// </summary>
    public class LoginData
    {
        [JsonPropertyName("key")]
        public string KeyValue { get; set; } = string.Empty;

        /// <summary>授权级别</summary>
        [JsonPropertyName("authorise")]
        public int Authorise { get; set; }

        /// <summary>用户 ID</summary>
        [JsonPropertyName("userId")]
        public string UserId { get; set; } = string.Empty;

        /// <summary>JWT Token</summary>
        [JsonPropertyName("token")]
        public string Token { get; set; } = string.Empty;

        [JsonPropertyName("userName")]
        public string UserName { get; set; } = string.Empty;


    }

    /// <summary>
    /// 登录响应
    /// </summary>
    public class LoginResponse : BaseResponse
    {
        [JsonPropertyName("data")]
        public LoginData? Data { get; set; }
    }

    //版本信息
    public class VersionInformation 
    {
        [JsonPropertyName("releaseNumber")]
        public string ReleaseNumber { get; set; } = string.Empty;
        [JsonPropertyName("versionId")]
        public int VersionId { get; set; }
        [JsonPropertyName("versionName")]
        //插件名称

        public string VersionName { get; set; } = string.Empty;
      
        [JsonPropertyName("status")]
        public string State { get; set; } =string.Empty;
        [JsonPropertyName("remark")]
        public string Remark { get; set; } = string.Empty;

        [JsonPropertyName("applicationLinks")]
        //链接地址
        public string ApplicationLinks { get; set; } = string.Empty;
    }

    public class VersionInformationResponse: BaseResponse
    {
        [JsonPropertyName("data")]
        public List<VersionInformation>? Data { get; set; }
    }

    /// <summary>
    /// 本地保存的登录信息（加密后写入 login.dat）
    /// </summary>
    public class SavedLoginInfo
    {
        /// <summary>账号服务 Token</summary>
        [JsonPropertyName("accountToken")]
        public string AccountToken { get; set; } = string.Empty;

        /// <summary>用户 ID</summary>
        [JsonPropertyName("userId")]
        public string UserId { get; set; } = string.Empty;

        /// <summary>授权级别</summary>
        [JsonPropertyName("authorise")]
        public int Authorise { get; set; }

        /// <summary>手机号（账号）</summary>
        [JsonPropertyName("phone")]
        public string Phone { get; set; } = string.Empty;

        /// <summary>密码</summary>
        [JsonPropertyName("password")]
        public string Password { get; set; } = string.Empty;

        [JsonPropertyName("userName")]
        public string UserName { get; set; } = string.Empty;
    }

    #endregion


    #region 对话框模型跟历史记录

    // 基础返回模型，后端统一格式
    public class ApiBaseResult<T>
    {
        [JsonPropertyName("code")]
        public int Code { get; set; }
        [JsonPropertyName("msg")]
        public string? Msg { get; set; }
        [JsonPropertyName("total")]
        public long Total { get; set; }
        [JsonPropertyName("rows")]
        public List<T>? Rows { get; set; }
    }

    //对话组列表
    public class GroupHistory
    {
        [JsonPropertyName("userId")]
        public string UserId { get; set; } =string.Empty;
        //对话组ID
        [JsonPropertyName("sessionGroupId")]
        public string SessionGroupId { get; set; } = string.Empty;
        //标题
        [JsonPropertyName("title")]
        public string Title { get; set; } = string.Empty;
        [JsonPropertyName("pin")]
        public int Pin { get; set; }

        //功能模式（1-智能问答，2-方案智能生成）
        [JsonPropertyName("functions")]
        public int Functions { get; set; } 

        [JsonPropertyName("savePath")]
        public string SavePath { get; set; } = string.Empty;
    }

    /// <summary>
    /// 侧边栏树形节点（按 SavePath 分组）
    /// </summary>
    public partial class GroupHistoryNode : ObservableObject
    {
        /// <summary>叶子节点：无 SavePath 的单条对话</summary>
        public GroupHistory? Item { get; set; }

        /// <summary>分组名（SavePath 的文件夹名）</summary>
        public string GroupName { get; set; } = string.Empty;

        public string SavePath { get; set; } = string.Empty;

        /// <summary>分组下的子项列表（含该 SavePath 下所有对话）</summary>
        public List<GroupHistory> Children { get; set; } = new();

        /// <summary>是否为分组节点</summary>
        public bool IsGroup => !string.IsNullOrEmpty(GroupName);

        /// <summary>展开/折叠状态</summary>
        [ObservableProperty]
        private bool _isExpanded = true;

        /// <summary>分组头显示的路径名</summary>
        public string DisplayPath => GroupName;
    }

    #endregion


    #region 历史对话接口模型

    /// <summary>单条历史对话记录</summary>
    public class ChatHistoryItem
    {
        [JsonPropertyName("id")]
        public int Id { get; set; }

        [JsonPropertyName("userId")]
        public string UserId { get; set; } = string.Empty;

        [JsonPropertyName("chatSessionId")]
        public string ChatSessionId { get; set; } = string.Empty;

        [JsonPropertyName("sessionGroupId")]
        public string SessionGroupId { get; set; } = string.Empty;

        [JsonPropertyName("msgId")]
        public string MsgId { get; set; } = string.Empty;

        [JsonPropertyName("role")]
        public string Role { get; set; } = string.Empty;

        [JsonPropertyName("content")]
        public string Content { get; set; } = string.Empty;
        [JsonPropertyName("summarize")]
        public string Summarize { get; set; } = string.Empty;


        [JsonPropertyName("msgStatus")]
        public string MsgStatus { get; set; } = string.Empty;

        [JsonPropertyName("createTime")]
        public long CreateTime { get; set; }

        [JsonPropertyName("updateTime")]
        public long UpdateTime { get; set; }

        [JsonPropertyName("docs")]
        public string? Docs { get; set; }

        [JsonPropertyName("isVisible")]
        public int IsVisible { get; set; }

        [JsonPropertyName("files")]
        public string? Files { get; set; }

        [JsonPropertyName("functions")]
        public int Functions { get; set; }

        [JsonPropertyName("articles")]
        public string? Articles { get; set; }

        [JsonPropertyName("savePath")]
        public string? SavePath { get; set; }
    }

    /// <summary>历史对话列表响应</summary>
    public class ChatHistoryListResponse
    {
        [JsonPropertyName("msg")]
        public string Msg { get; set; } = string.Empty;

        [JsonPropertyName("code")]
        public int Code { get; set; }

        [JsonPropertyName("data")]
        public List<ChatHistoryItem>? Data { get; set; }
    }

    #endregion

}
