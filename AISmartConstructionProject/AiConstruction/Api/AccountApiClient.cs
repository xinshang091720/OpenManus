using AiConstruction.Model;
using AiConstruction.Services;
using System.Collections.ObjectModel;
using System.Net.Http;
using System.Reflection.Emit;
using System.Text;
using System.Text.Json;
using static System.Runtime.InteropServices.JavaScript.JSType;

namespace AiConstruction.Api
{
    /// <summary>
    /// 账号服务 API 客户端（独立 BaseUrl，与 Runtime API 分离）
    /// </summary>
    public class AccountApiClient
    {
        private readonly HttpClient _httpClient;

        public AccountApiClient()
        {
            _httpClient = new HttpClient
            {
                BaseAddress = new Uri("http://apichajian.swarm-bim.com"),

               // BaseAddress = new Uri("http://192.168.1.71:9001"),

              //  BaseAddress = new Uri("http://192.168.1.155:9001"),
                Timeout = TimeSpan.FromSeconds(10)
            };
        }

        #region 登录注册接口
        /// <summary>
        /// 发送短信验证码
        /// POST /api/account/verifyCode/sendCaptcha
        /// </summary>
        public async Task<SendCaptchaResponse?> SendCaptchaAsync(string phone, string codeType = "0", CancellationToken ct = default)
        {
            SendCaptchaResponse? result = new SendCaptchaResponse();
            try
            {
                var request = new
                {
                    phone = phone,
                    codeType = codeType
                };

                var json = JsonSerializer.Serialize(request);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                LogHelper.Info($"[AccountAPI] 请求验证码: phone={phone}, codeType={codeType}");

                var response = await _httpClient.PostAsync("/api/account/verifyCode/sendCaptcha", content, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);

                LogHelper.Info($"[AccountAPI] 响应: {responseJson}");

                result = JsonSerializer.Deserialize<SendCaptchaResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 请求验证码异常: {ex.Message}");
            }
            return result;
        }

        /// <summary>
        /// 账号密码登录
        /// POST /api/account/login
        /// </summary>
        public async Task<LoginResponse?> LoginAsync(string username, string password, CancellationToken ct = default)
        {
            LoginResponse? result = new LoginResponse();
            try
            {
                var request = new
                {
                    username = username,
                    password = password,

                    portFlag = "pluginDesktop"
                };

                var json = JsonSerializer.Serialize(request);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                LogHelper.Info($"[AccountAPI] 登录请求: username={username}");

                var response = await _httpClient.PostAsync("/api/account/login", content, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);

                LogHelper.Info($"[AccountAPI] 登录响应: {responseJson}");

                 result = JsonSerializer.Deserialize<LoginResponse>(responseJson);
                
            }
            catch(Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 登录响应: {ex.Message.ToString()}");
            }
            return result;
        }

        /// <summary>
        /// 短信验证码登录
        /// POST /api/account/verifyCode/captchaLogin
        /// </summary>
        public async Task<LoginResponse?> CaptchaLoginAsync(string phone, string code, string codeType = "0", CancellationToken ct = default)
        {
            LoginResponse? result = new LoginResponse();
            try
            {
                var request = new
                {
                    userName = phone,
                    code = code,
                    codeType = codeType,
                    portFlag= "pluginDesktop"
                };

                var json = JsonSerializer.Serialize(request);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                LogHelper.Info($"[AccountAPI] 验证码登录: phone={phone}");

                var response = await _httpClient.PostAsync("/api/account/verifyCode/captchaLogin", content, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);

                LogHelper.Info($"[AccountAPI] 验证码登录响应: {responseJson}");

                result = JsonSerializer.Deserialize<LoginResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 验证码登录异常: {ex.Message}");
            }
            return result;
        }


        //注册
        public async Task<SendCaptchaResponse?>  Register(object data, CancellationToken ct = default)
        {
            SendCaptchaResponse? result = new SendCaptchaResponse();
            try
            {
                var json = JsonSerializer.Serialize(data);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                var response = await _httpClient.PostAsync("/api/account/verifyCode/captchaRegister", content, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                LogHelper.Info($"[AccountAPI] 注册: {responseJson}");

                result = JsonSerializer.Deserialize<SendCaptchaResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 注册异常: {ex.Message}");
            }
            return result;
        }

        //查询用户详情
        /// <summary>
        /// 
        ////system/member/getInfo/
        /// </summary>
        public async Task<LoginResponse?> GetUserInfo( CancellationToken ct = default)
        {
            LoginResponse? result = new LoginResponse();
            try
            {
                var request = new HttpRequestMessage(HttpMethod.Get, $"/system/member/getInfo/{ApiConfig.UserId}");
                request.Headers.Add("type", "member");
                request.Headers.Add("portFlag", "pluginDesktop");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);

                LogHelper.Info($"[AccountAPI] 查询用户详情: userId={ApiConfig.UserId}");

                var response = await _httpClient.SendAsync(request, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                LogHelper.Info($"[AccountAPI] 查询用户详情: {responseJson}");
                result = JsonSerializer.Deserialize<LoginResponse?>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 查询用户详情异常: {ex.Message}");
            }
            return result;
        }

        /// <summary>
        /// 申请授权
        /// POST /fore/member/authorise/add
        /// </summary>
        public async Task<SendCaptchaResponse?> AddAuthorization(CancellationToken ct = default)
        {
            SendCaptchaResponse? result = new SendCaptchaResponse();
            try
            {
                var data = new
                {
                    userId = ApiConfig.UserId
                };
                var json = JsonSerializer.Serialize(data);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                var request = new HttpRequestMessage(HttpMethod.Post, "/fore/member/authorise/add")
                {
                    Content = content
                };
                request.Headers.Add("type", "member");
                request.Headers.Add("portFlag", "pluginDesktop");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);

                LogHelper.Info($"[AccountAPI] 申请授权: userId={ApiConfig.UserId}");

                var response = await _httpClient.SendAsync(request, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                LogHelper.Info($"[AccountAPI] 申请授权响应: {responseJson}");
                result = JsonSerializer.Deserialize<SendCaptchaResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 申请授权异常: {ex.Message}");
            }
            return result;
        }

        /// <summary>
        /// 查询授权
        /// GET /fore/member/authorise/user/{userId}
        /// </summary>
        public async Task<Authorizatio?> SelectAuthorization(CancellationToken ct = default)
        {
            Authorizatio? result = new Authorizatio();
            try
            {
                var request = new HttpRequestMessage(HttpMethod.Get, $"/fore/member/authorise/user/{ApiConfig.UserId}");
                request.Headers.Add("type", "member");
                request.Headers.Add("portFlag", "pluginDesktop");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);

                LogHelper.Info($"[AccountAPI] 查询授权: userId={ApiConfig.UserId}");

                var response = await _httpClient.SendAsync(request, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                LogHelper.Info($"[AccountAPI] 查询授权响应: {responseJson}");
                result = JsonSerializer.Deserialize<Authorizatio>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 查询授权异常: {ex.Message}");
            }
            return result;
        }
        /// <summary>
        /// 获取图片
        /// GET /system/config/pluginConfig
        /// </summary>
        public  string GetQRCode(CancellationToken ct = default)
        {
            string result = string.Empty;
            try
            {
                // 发送Get请求并获取响应
                HttpResponseMessage response = _httpClient.GetAsync("/system/config/pluginConfig").Result;
                result = response.Content.ReadAsStringAsync(ct).Result;
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 获取二维码异常: {ex.Message}");
            }
            return result;
        }

        ///api/account/user
        public  async Task<SendCaptchaResponse?> VerifyToken(CancellationToken ct = default)
        {
            SendCaptchaResponse? result = new SendCaptchaResponse();
            try
            {
                var request = new HttpRequestMessage(HttpMethod.Get, $"/api/account/user2");
                request.Headers.Add("type", "member");
                request.Headers.Add("portFlag", "pluginDesktop");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);
                var response = await _httpClient.SendAsync(request, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                LogHelper.Info($"[AccountAPI] 验证token: {responseJson}");
                result = JsonSerializer.Deserialize<SendCaptchaResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 验证token异常: {ex.Message}");
            }
            return result;
        }

        //退出登录 
        public  SendCaptchaResponse? SignOut(CancellationToken ct = default)
        {
            SendCaptchaResponse? result = new SendCaptchaResponse();
            try
            {
                var request = new HttpRequestMessage(HttpMethod.Get, $"/api/account/userLogout");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);
                LogHelper.Info($"[AccountAPI] 退出: userId={ApiConfig.UserId}");

                var response =  _httpClient.SendAsync(request, ct).Result;
                var responseJson =  response.Content.ReadAsStringAsync(ct).Result;
                LogHelper.Info($"[AccountAPI] 退出: {responseJson}");
                result = JsonSerializer.Deserialize<SendCaptchaResponse?>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 退出: {ex.Message}");
            }
            return result;
        }


        //获取版本
         public VersionInformationResponse? GetVersionInformation( int types,CancellationToken ct = default)
        {
            VersionInformationResponse? result = new VersionInformationResponse();
            try
            {
                var data = new
                {
                    types = types
                };
                var json = JsonSerializer.Serialize(data);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                var request = new HttpRequestMessage(HttpMethod.Post, "/system/version/listInfo1")
                {
                    Content = content
                };
                var response =  _httpClient.SendAsync(request, ct).Result;
                var responseJson =  response.Content.ReadAsStringAsync(ct).Result;
              
                result = JsonSerializer.Deserialize<VersionInformationResponse?>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 获取版本: {ex.Message}");
            }
            return result;
        
        }
        public LoginResponse? GetKey(CancellationToken ct = default)
        {
            LoginResponse? result = new();
            try
            {
                var request = new HttpRequestMessage(HttpMethod.Get, $"/system/config/aiConfig");
             
                var response = _httpClient.SendAsync(request, ct).Result;
                var responseJson = response.Content.ReadAsStringAsync(ct).Result;
            
                result = JsonSerializer.Deserialize<LoginResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 退出: {ex.Message}");
            }
            return result;
        }
        //获取key

        #endregion


        #region 对话框信息记录接口
        /// <summary>
        /// 新增组对话
        /// </summary>
        /// <param name="ct"></param>
        /// <returns></returns>
        public async Task<SendCaptchaResponse?> AddGrouprecord(string sessionGroupId,string title,string savePath, CancellationToken ct = default)
        {
            SendCaptchaResponse? result = new SendCaptchaResponse();
            try
            {
                var data = new
                {
                    userId = ApiConfig.UserId,
                    sessionGroupId= sessionGroupId,
                    title= title,
                    functions=1,
                    savePath= savePath
                };
                var json = JsonSerializer.Serialize(data);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                var request = new HttpRequestMessage(HttpMethod.Post, "/fore/ai/record/add")
                {
                    Content = content
                };
                request.Headers.Add("type", "member");
                request.Headers.Add("portFlag", "pluginDesktop");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);
                var response = await _httpClient.SendAsync(request, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                result = JsonSerializer.Deserialize<SendCaptchaResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 新增组对话异常: {ex.Message}");
            }
            return result;
        }
        /// <summary>
        /// 对话组历史列表
        /// </summary>
        /// <param name="ct"></param>
        /// <returns></returns>
        public async Task<ApiBaseResult<GroupHistory>?> SeleteGroupHistory(CancellationToken ct = default)
        {
            ApiBaseResult<GroupHistory>? result = new ApiBaseResult<GroupHistory>();
            try
            {
                var data = new
                {
                    userId = ApiConfig.UserId
                };
                var json = JsonSerializer.Serialize(data);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                var request = new HttpRequestMessage(HttpMethod.Post, "/fore/ai/record/list")
                {
                    Content = content
                };
                request.Headers.Add("type", "member");
                request.Headers.Add("portFlag", "pluginDesktop");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);
                var response = await _httpClient.SendAsync(request, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                result = JsonSerializer.Deserialize<ApiBaseResult<GroupHistory>?>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 对话组历史列表异常: {ex.Message}");
            }
            return result;
        }
        /// <summary>
        /// 新增对话历史
        /// </summary>
        /// <param name="chatSessionId">问答会话唯一标识</param>
        /// <param name="sessionGroupId">对话组ID（绑定同一轮多轮对话）</param>
        /// <param name="msgId">消息唯一标识（新增：用于精准删除）</param>
        /// <param name="role">消息角色（system/user/assistant）对应系统/用户/AI</param>
        /// <param name="content"> 消息内容</param>
         /// <param name="aicontent"> 消息内容</param>
        /// <param name="ct"></param>
        /// <returns></returns>

        public async Task<SendCaptchaResponse?> AddDialogueHistory(string chatSessionId, string sessionGroupId, string msgId,string role, string content,string aicontent, CancellationToken ct = default)
        {
            SendCaptchaResponse? result = new SendCaptchaResponse();
            try
            {
                var data = new
                {
                    userId = ApiConfig.UserId,
                    chatSessionId= chatSessionId,
                    sessionGroupId = sessionGroupId,
                    msgId=msgId,
                    role=role,
                    content= content,
                    summarize= aicontent,
                    msgStatus = "NORMAL",
                    isVisible=1,
                    functions=1
                };
                var json = JsonSerializer.Serialize(data);
                var jsonContent = new StringContent(json, Encoding.UTF8, "application/json");

                var request = new HttpRequestMessage(HttpMethod.Post, "/ai/chatHistory/add")
                {
                    Content = jsonContent
                };
                request.Headers.Add("type", "member");
                request.Headers.Add("portFlag", "pluginDesktop");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);
                var response = await _httpClient.SendAsync(request, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                result = JsonSerializer.Deserialize<SendCaptchaResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 新增对话历史异常: {ex.Message}");
            }
            return result;
        }

        /// <summary>
        /// 查询历史对话
        /// POST /ai/chatHistory/list
        /// </summary>
        public async Task<ChatHistoryListResponse?> SelectChatHistory(string sessionGroupId, CancellationToken ct = default)
        {
            ChatHistoryListResponse? result = new ChatHistoryListResponse();
            try
            {
                var data = new
                {
                    userId = ApiConfig.UserId,
                    sessionGroupId = sessionGroupId
                };
                var json = JsonSerializer.Serialize(data);
                var content = new StringContent(json, Encoding.UTF8, "application/json");

                var request = new HttpRequestMessage(HttpMethod.Post, "/ai/chatHistory/list")
                {
                    Content = content
                };
                request.Headers.Add("type", "member");
                request.Headers.Add("portFlag", "pluginDesktop");
                request.Headers.Add("Authorization", ApiConfig.AccountToken);

                LogHelper.Info($"[AccountAPI] 查询历史对话: sessionGroupId={sessionGroupId}");

                var response = await _httpClient.SendAsync(request, ct);
                var responseJson = await response.Content.ReadAsStringAsync(ct);
                LogHelper.Info($"[AccountAPI] 查询历史对话响应: {responseJson}");
                result = JsonSerializer.Deserialize<ChatHistoryListResponse>(responseJson);
            }
            catch (Exception ex)
            {
                LogHelper.Info($"[AccountAPI] 查询历史对话异常: {ex.Message}");
            }
            return result;
        }
        #endregion
        //Add authorization
    }
}
