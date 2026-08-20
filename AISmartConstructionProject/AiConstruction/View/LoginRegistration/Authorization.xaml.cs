using AiConstruction.Api;
using AiConstruction.ViewModel;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Policy;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;

namespace AiConstruction.View
{
    /// <summary>
    /// Authorization.xaml 的交互逻辑
    /// </summary>
    public partial class Authorization : Window
    {  
        public Authorization()
        {
            this.DataContext = new AuthorizationVM();
            InitializeComponent();
        }

        private void Button_Click(object sender, RoutedEventArgs e)
        {
            this.Close(); 
        }
    }
}
