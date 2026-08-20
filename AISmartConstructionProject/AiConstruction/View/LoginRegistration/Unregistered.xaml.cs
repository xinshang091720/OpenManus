using System;
using System.Collections.Generic;
using System.Linq;
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
    /// Unregistered.xaml 的交互逻辑
    /// </summary>
    public partial class Unregistered : Window
    {
        public Unregistered()
        {
            InitializeComponent();
        }

        private void Button_Click(object sender, RoutedEventArgs e)
        {
            this.Close();
        }

        private void Go_Register(object sender, RoutedEventArgs e)
        {
            this.Close();
            Register register= new Register();
            register.ShowDialog();
        }
    }
}
