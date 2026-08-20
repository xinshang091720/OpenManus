using AiConstruction.Api;
using AiConstruction.Services;
using AiConstruction.View;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Threading;
using static System.Windows.Forms.VisualStyles.VisualStyleElement.StartPanel;

namespace AiConstruction.ViewModel
{
    public partial class RegisterVM : ObservableObject
    {
        #region 属性

        /// <summary>用户昵称</summary>
        [ObservableProperty]
        private string _nickName = string.Empty;

        /// <summary>联系电话</summary>
        [ObservableProperty]
        private string _phone = string.Empty;

        /// <summary>短信验证码</summary>
        [ObservableProperty]
        private string _smsCode = string.Empty;

        /// <summary>邮箱（可选）</summary>
        [ObservableProperty]
        private string _email = string.Empty;

        /// <summary>所属公司</summary>
        [ObservableProperty]
        private string _company = string.Empty;

        /// <summary>职位</summary>
        [ObservableProperty]
        private string _position = string.Empty;

        /// <summary>是否同意用户协议</summary>
        [ObservableProperty]
        private bool _isAgreed;

        /// <summary>验证码按钮文字</summary>
        [ObservableProperty]
        private string _captchaButtonText = "获取验证码";

        /// <summary>验证码按钮是否可用</summary>
        [ObservableProperty]
        private bool _isCaptchaEnabled = true;

        /// <summary>注册按钮是否可用（防止重复提交）</summary>
        [ObservableProperty]
        private bool _isRegisterEnabled = true;

        /// <summary>密码提示</summary>
        [ObservableProperty]
        private string _errorMessage = string.Empty;

        /// <summary>确定密码提示</summary>
        [ObservableProperty]
        private string _errorMessageConfirm = string.Empty;

        #region PasswordBox 委托（由 View 注入，解决 PasswordBox 不支持双向绑定）

        /// <summary>获取密码的委托</summary>
        public Func<string>? GetPasswordFunc { get; set; }

        /// <summary>获取确认密码的委托</summary>
        public Func<string>? GetConfirmPasswordFunc { get; set; }

        #endregion

        #endregion

        #region 服务
        private readonly AccountApiClient _accountApi = new();
        private DispatcherTimer? _captchaTimer;
        private int _captchaCountdown;

        #endregion

        #region 命令

        /// <summary>
        /// 获取短信验证码
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
        /// 注册
        /// </summary>
        [RelayCommand]
        private async Task RegisterAsync(Window? window)
        {
            // 网络预检：无法上网则弹窗提示并中止（避免无效请求）
            if (!await NetworkHelper.CheckNetworkAndNotifyAsync()) return;

            // 校验昵称
            if (string.IsNullOrWhiteSpace(NickName))
            {
                MessageBox.Show("请输入用户昵称", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }
            //验证手机号是否规范
            bool isValid = System.Text.RegularExpressions.Regex.IsMatch(Phone, @"^1[3456789]\d{9}$");

            // 校验手机号
            if (string.IsNullOrWhiteSpace(Phone) || Phone.Length != 11|| !isValid)
            {
                MessageBox.Show("请输入正确的11位手机号", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            // 校验验证码
            if (string.IsNullOrWhiteSpace(SmsCode))
            {
                MessageBox.Show("请输入短信验证码", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            // 校验密码
            var pwd = GetPasswordFunc?.Invoke() ?? string.Empty;
            if (string.IsNullOrWhiteSpace(pwd))
            {
                MessageBox.Show("请输入密码", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            // 校验密码强度：不低于8位，需包含大小写字母、特殊字符和数字
            if (pwd.Length < 8 ||
                !pwd.Any(char.IsUpper) ||
                !pwd.Any(char.IsLower) ||
                !pwd.Any(char.IsDigit) ||
                !pwd.Any(c => !char.IsLetterOrDigit(c)))
            {
                ErrorMessage = "密码长度不低于8位，需包含大小写字母、特殊字符和数字";
                return;
            }
            // 校验确认密码
            var confirmPwd = GetConfirmPasswordFunc?.Invoke() ?? string.Empty;
            if (string.IsNullOrWhiteSpace(confirmPwd))
            {
                MessageBox.Show("请输入确认密码", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            // 校验确认密码强度：不低于8位，需包含大小写字母、特殊字符和数字
            if (confirmPwd.Length < 8 ||
                !confirmPwd.Any(char.IsUpper) ||
                !confirmPwd.Any(char.IsLower) ||
                !confirmPwd.Any(char.IsDigit) ||
                !confirmPwd.Any(c => !char.IsLetterOrDigit(c)))
            {
                ErrorMessageConfirm = "密码长度不低于8位，需包含大小写字母、特殊字符和数字";
                return;
            }

            // 校验两次密码一致
            if (pwd != confirmPwd)
            {
                MessageBox.Show("两次输入的密码不一致", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }
        
            // 校验所属公司
            if (string.IsNullOrWhiteSpace(Company))
            {
                MessageBox.Show("请输入所属公司", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            // 校验职位
            if (string.IsNullOrWhiteSpace(Position))
            {
                MessageBox.Show("请选择职位", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            // 校验协议
            if (!IsAgreed)
            {
                MessageBox.Show("请先阅读并同意用户协议和隐私政策", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            IsRegisterEnabled = false;
            try
            {
                var data = new
                {
                    nickName = NickName,
                    userName = Phone,
                    code = SmsCode,
                    codeType = 0,
                    email = Email,
                    newPassword = pwd,
                    reNewPassword = confirmPwd,
                    sourceCompany = Company,
                    post = Position
                };
                var response = await _accountApi.Register(data);
                if (response == null)
                {
                    MessageBox.Show("请求失败，请稍后重试", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                    IsCaptchaEnabled = true;
                    return;
                }

                if (!response.IsSuccess)
                {
                    var message = response.Message ?? "注册失败";
                    MessageBox.Show(message, "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                    IsCaptchaEnabled = true;
                    return;
                }
                 
                await _accountApi.AddAuthorization();
                window?.Close();
                RegisteredSuccessfully registeredSuccessfully = new RegisteredSuccessfully();
                registeredSuccessfully.ShowDialog();
            }
            catch (Exception ex)
            {
                MessageBox.Show($"注册异常: {ex.Message}", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                IsRegisterEnabled = true;
            }
        }

        /// <summary>
        /// 关闭窗口
        /// </summary>
        [RelayCommand]
        private void CloseWindow(Window? window)
        {
            window?.Close();
        }

        #endregion

        #region 私有方法

        /// <summary>
        /// 启动验证码倒计时（60秒）
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

        #endregion
    }
}
