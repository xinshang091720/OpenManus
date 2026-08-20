using System.Windows;
using System.Windows.Controls;
using System.Windows.Navigation;
using AiConstruction.ViewModel;

namespace AiConstruction.View
{
    public partial class Login : Window
    {
        private LoginVM _vm = null!;

        public Login()
        {
            InitializeComponent();
            _vm = (LoginVM)DataContext;

            // 注入获取密码的委托（HandyControl PasswordBox 不支持直接绑定 Password）
            _vm.GetPasswordFunc = () =>
            {
                if (FindName("PwdBox") is HandyControl.Controls.PasswordBox pwdBox)
                    return pwdBox.Password;
                return string.Empty;
            };

            // 注入设置密码的委托（加载保存信息时回填到 PasswordBox）
            _vm.SetPasswordAction = (pwd) =>
            {
                if (FindName("PwdBox") is HandyControl.Controls.PasswordBox pwdBox)
                    pwdBox.Password = pwd;
            };

            // 注入遮罩层显示/隐藏委托（弹窗时底层变灰）
            _vm.SetMaskVisibilityAction = (visible) =>
            {
                if (FindName("MaskLayer") is Border mask)
                    mask.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
            };

            // 委托注入完成后再加载保存的登录信息
            _vm.LoadSavedLoginInfo();
        }

        /// <summary>
        /// TabControl 切换时同步 IsSmsTab 到 ViewModel
        /// </summary>
        private void TabControl_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (sender is TabControl tab)
            {
                _vm.IsSmsTab = tab.SelectedIndex == 0;
            }
        }

        private void Hyperlink_RequestNavigate(object sender, RequestNavigateEventArgs e)
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(e.Uri.AbsoluteUri)
            {
                UseShellExecute = true
            });
            e.Handled = true;
        }
    }
}
