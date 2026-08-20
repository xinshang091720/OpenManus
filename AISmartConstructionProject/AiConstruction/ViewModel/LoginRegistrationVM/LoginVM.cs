using AiConstruction.Api;
using AiConstruction.Model;
using AiConstruction.Services;
using AiConstruction.View;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System.Text.Json;
using System.Windows;
using System.Windows.Threading;
using static System.Windows.Forms.VisualStyles.VisualStyleElement.StartPanel;

namespace AiConstruction.ViewModel
{
    public partial class LoginVM : ObservableObject
    {
        #region 属性

        /// <summary>当前是否为短信登录 Tab</summary>
        [ObservableProperty]
        private bool _isSmsTab = true;

        /// <summary>手机号（短信/密码登录共用）</summary>
        [ObservableProperty]
        private string _phone = string.Empty;

        /// <summary>短信验证码</summary>
        [ObservableProperty]
        private string _smsCode = string.Empty;

        /// <summary>密码（密码登录）</summary>
        [ObservableProperty]
        private string _password = string.Empty;

        /// <summary>邀请码</summary>
        [ObservableProperty]
        private string _inviteCode = string.Empty;

        /// <summary>是否同意用户协议</summary>
        [ObservableProperty]
        private bool _isAgreed;

        /// <summary>验证码按钮文字</summary>
        [ObservableProperty]
        private string _captchaButtonText = "获取验证码";

        /// <summary>验证码按钮是否可用</summary>
        [ObservableProperty]
        private bool _isCaptchaEnabled = true;

        /// <summary>登录按钮是否可用（防止重复提交）</summary>
        [ObservableProperty]
        private bool _isLoginEnabled = true;

        /// <summary>获取密码的委托（由 View 代码后置注入，解决 PasswordBox 无法直接绑定的问题）</summary>
        public Func<string>? GetPasswordFunc { get; set; }

        /// <summary>设置密码到 PasswordBox 的委托（加载保存信息时回填）</summary>
        public Action<string>? SetPasswordAction { get; set; }

        #endregion

        #region 服务

        private readonly AccountApiClient _accountApi = new();
        private DispatcherTimer? _captchaTimer;
        private int _captchaCountdown;

        #endregion

        #region 构造

        public LoginVM()
        {
        }

        /// <summary>
        /// 从程序同目录 login.dat 加载上次保存的登录信息
        /// </summary>
        public void LoadSavedLoginInfo()
        {
            try
            {
                var path = ApiConfig.LoginDataPath;
                if (!System.IO.File.Exists(path))
                    return;

                var encrypted = System.IO.File.ReadAllText(path);
                var json = ApiConfig.DecryptString(encrypted);
                if (string.IsNullOrEmpty(json))
                    return;

                var info = JsonSerializer.Deserialize<SavedLoginInfo>(json);
                if (info == null)
                    return;

                Phone = info.Phone ?? string.Empty;
                Password = info.Password ?? string.Empty;

                // 通过 View 注入的 Action 将密码回填到 PasswordBox（PasswordBox 不支持双向绑定）
                SetPasswordAction?.Invoke(info.Password ?? string.Empty);

                // 如果上次保存了 Token，也恢复到 ApiConfig（可选：下次可以直接用 Token 自动登录）
                if (!string.IsNullOrEmpty(info.AccountToken))
                {
                    ApiConfig.AccountToken = info.AccountToken;
                    ApiConfig.UserId = info.UserId ?? string.Empty;
                    ApiConfig.Authorise = info.Authorise;
                }
            }
            catch
            {
                // 读取失败静默忽略，不影响正常登录
            }
        }

        #endregion

        #region 命令

        /// <summary>
        /// 获取验证码
        /// </summary>
        [RelayCommand]
        private async Task GetCaptchaAsync()
        {
            // 网络预检：无法上网则弹窗提示并中止（避免无效请求）
            if (!await NetworkHelper.CheckNetworkAndNotifyAsync()) return;

            //验证手机号是否规范
            bool isValid = System.Text.RegularExpressions.Regex.IsMatch(Phone, @"^1[3456789]\d{9}$");

            // 校验手机号
            if (string.IsNullOrWhiteSpace(Phone) || Phone.Length != 11 || !isValid)
            {
                MessageBox.Show("请输入正确的11位手机号", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            try
            {
                IsCaptchaEnabled = false;
                var response = await _accountApi.SendCaptchaAsync(Phone, "0");

                if (response == null)
                {
                    MessageBox.Show("请求失败，请稍后重试", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                    IsCaptchaEnabled = true;
                    return;
                }

                if (!response.IsSuccess)
                {
                    var message = response.Message ?? "验证码发送失败";
                    MessageBox.Show(message, "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                    IsCaptchaEnabled = true;
                    return;
                }

                // 开始倒计时
                StartCaptchaCountdown();
            }
            catch (Exception ex)
            {
                MessageBox.Show($"请求异常: {ex.Message}", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                IsCaptchaEnabled = true;
            }
        }

        /// <summary>
        /// 控制父窗口遮罩层显示/隐藏（由 View 代码后置注入）
        /// </summary>
        public Action<bool>? SetMaskVisibilityAction { get; set; }

        /// <summary>
        /// 登录
        /// </summary>
        [RelayCommand]
        private async Task LoginAsync(Window? window)
        {
            // 网络预检：无法上网则弹窗提示并中止（避免无效请求）
            if (!await NetworkHelper.CheckNetworkAndNotifyAsync()) return;

            //验证手机号是否规范
            bool isValid = System.Text.RegularExpressions.Regex.IsMatch(Phone, @"^1[3456789]\d{9}$");

            // 校验手机号
            if (string.IsNullOrWhiteSpace(Phone) || Phone.Length != 11 || !isValid)
            {
                MessageBox.Show("请输入正确的11位手机号", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }
            // 校验协议
            if (!IsAgreed)
            {
                MessageBox.Show("请先阅读并同意用户协议和隐私政策", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            IsLoginEnabled = false;
            try
            {
               
                LoginResponse? response;

                if (IsSmsTab)
                {
                    // 短信登录校验
                    if (string.IsNullOrWhiteSpace(SmsCode))
                    {
                        MessageBox.Show("请输入短信验证码", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                        IsLoginEnabled = true;
                        return;
                    }

                    response = await _accountApi.CaptchaLoginAsync(Phone, SmsCode);
                }
                else
                {
                    // 密码登录校验：从 View 注入的委托获取密码
                    var pwd = GetPasswordFunc?.Invoke() ?? string.Empty;
                    if (string.IsNullOrWhiteSpace(pwd))
                    {
                        MessageBox.Show("请输入密码", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                        IsLoginEnabled = true;
                        return;
                    }

                    Password = pwd;
                    response = await _accountApi.LoginAsync(Phone, pwd);
                }

                // 统一处理登录响应
                HandleLoginResponse(response, window);
            }
            catch (Exception ex)
            {
                MessageBox.Show($"登录异常: {ex.Message}", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                IsLoginEnabled = true;
            }
        }

        /// <summary>
        /// 统一处理登录响应
        /// </summary>
        private async void HandleLoginResponse(LoginResponse? response, Window? window)
        {
        
            if (response == null)
            {
                MessageBox.Show("登录请求失败，请稍后重试", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                IsLoginEnabled = true;
                return;
            }

            if (!response.IsSuccess)
            {
                var message = response.Message ?? "登录失败";
                MessageBox.Show(message, "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                IsLoginEnabled = true;
                return;
            }
            if (response.Message == "请先注册账号")
            {
                SetMaskVisibilityAction?.Invoke(true);
                var unregistered = new Unregistered { Owner = window };
                unregistered.ShowDialog();
                SetMaskVisibilityAction?.Invoke(false);
                IsLoginEnabled = true;
                return;
            }
            ApiConfig.AccountToken = response.Data.Token;
            ApiConfig.UserId = response.Data.UserId;
            var userInfor=  await _accountApi.GetUserInfo();
            // 保存登录信息到内存
            ApiConfig.Authorise = response.Data.Authorise;
            ApiConfig.UserName = userInfor.Data.UserName;
            ApiConfig.Phone = Phone;
            ApiConfig.Password = Password;
            // 加密保存到程序同目录 login.dat
            SaveLoginInfo();
            if (ApiConfig.Authorise==2|| ApiConfig.Authorise == 3)
            {
                if (MessageBox.Show("该账号未申请授权,是否申请授权？", "提示", MessageBoxButton.OKCancel, MessageBoxImage.Information) == MessageBoxResult.OK)
                {
                    await _accountApi.AddAuthorization();
                }
            }
            else
            {
                MessageBox.Show("登录成功", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
            }
            window.Close();
        }

        /// <summary>
        /// 关闭窗口
        /// </summary>
        [RelayCommand]
        private void CloseWindow(Window? window)
        {
            window?.Close();
        }

        /// <summary>
        /// 跳转到注册页
        /// </summary>
        [RelayCommand]
        private void GoToRegister(Window? window)
        {
            var register = new View.Register();
            register.ShowDialog();
           //window?.Close();
        }

        //联系我们
        [RelayCommand]
        private void ContactUs(Window window)
        {
            SetMaskVisibilityAction?.Invoke(true);
            Authorization authorization = new Authorization { Owner = window };
            authorization.ShowDialog();
            SetMaskVisibilityAction?.Invoke(false);
        }

        #endregion

        #region 私有方法

        /// <summary>
        /// 启动验证码倒计时
        /// </summary>
        private void StartCaptchaCountdown()
        {
            _captchaCountdown = 60;
            CaptchaButtonText = $"{_captchaCountdown}s";

            _captchaTimer?.Stop();
            _captchaTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromSeconds(1)
            };

            _captchaTimer.Tick += (s, e) =>
            {
                _captchaCountdown--;
                if (_captchaCountdown <= 0)
                {
                    _captchaTimer?.Stop();
                    CaptchaButtonText = "获取验证码";
                    IsCaptchaEnabled = true;
                }
                else
                {
                    CaptchaButtonText = $"{_captchaCountdown}s";
                }
            };

            _captchaTimer.Start();
        }

        /// <summary>
        /// 将登录信息加密后保存为 JSON 文件（程序同目录 login.dat）
        /// </summary>
        private void SaveLoginInfo()
        {
            try
            {
                var info = new SavedLoginInfo
                {
                    AccountToken = ApiConfig.AccountToken,
                    UserId = ApiConfig.UserId,
                    Authorise = ApiConfig.Authorise,
                    Phone = Phone,
                    Password = Password,
                    UserName= ApiConfig.UserName,
                };
                // 序列化为 JSON 明文
                var json = JsonSerializer.Serialize(info);

                // 加密整个 JSON 字符串
                var encrypted = ApiConfig.EncryptString(json);

                // 写入程序同目录
                System.IO.File.WriteAllText(ApiConfig.LoginDataPath, encrypted);
            }
            catch (Exception ex)
            {
                // 保存失败不影响正常登录流程，仅写日志
                System.Diagnostics.Debug.WriteLine($"[LoginVM] 保存登录信息失败: {ex.Message}");
            }
        }

        #endregion
    }
}
