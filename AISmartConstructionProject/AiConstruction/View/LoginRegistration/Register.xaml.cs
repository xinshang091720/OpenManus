using System.Windows;
using System.Windows.Controls;
using System.Windows.Navigation;
using AiConstruction.ViewModel;

namespace AiConstruction.View
{
    public partial class Register : Window
    {
        private RegisterVM _vm = null!;

        public Register()
        {
            InitializeComponent();
            _vm = (RegisterVM)DataContext;

            // 注入获取密码的委托
            _vm.GetPasswordFunc = () =>
            {
                if (FindName("PwdBox") is HandyControl.Controls.PasswordBox pwdBox)
                    return pwdBox.Password;
                return string.Empty;
            };

            // 注入获取确认密码的委托
            _vm.GetConfirmPasswordFunc = () =>
            {
                if (FindName("ConfirmPwdBox") is HandyControl.Controls.PasswordBox pwdBox)
                    return pwdBox.Password;
                return string.Empty;
            };
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
