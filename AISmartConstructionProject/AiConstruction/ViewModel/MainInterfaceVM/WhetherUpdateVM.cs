using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Text;
using System.Threading.Tasks;
using System.Windows;

namespace AiConstruction.ViewModel
{
    public class WhetherUpdateVM: ObservableObject
    {
        public bool IsUpdate { get; set; } = false;

        public WhetherUpdateVM(bool isConstruction)
        {
            Content = content;
            if (isConstruction)
            {
                Content = "当前程序与SwarmBIM报建不是最新版本，是否进行更新？";
            }
            else
            {
                Content = "当前程序AI智建不是最新版本,是否进行更新？";
            }
            IsConstruction=isConstruction;
        }
        public bool IsConstruction { get; set; }
        private string content = string.Empty;
        public string Content
        {
            get => content;
            set => SetProperty(ref content, value);
        }
        public RelayCommand<Window> ConfirmCommand => new((window) =>
        {
            if (!IsInternetAvailable())
            {
                System.Windows.MessageBox.Show("网络连接已断开，请重连！", "提示", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }
            
            IsUpdate = true;
            window.Close();
        });
        public RelayCommand<Window> CloseCommand => new((window) =>
        {
            IsUpdate = false;
            window.Close();
        });
        public static bool IsInternetAvailable()
        {
            try
            {
                using var ping = new Ping();
                // 用稳定的公网 IP，DNS 不挂也行；建议多备几个
                var reply = ping.Send("223.5.5.5", 1500); // 阿里 DNS，超时 1.5s
                return reply.Status == IPStatus.Success;
            }
            catch
            {
                return false;
            }
        }

       
    }
}
