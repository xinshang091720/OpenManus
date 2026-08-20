using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;

namespace AiConstruction.ViewModel
{
    public class AddCreateSpaceVM: ObservableObject
    {
        private string sapceName=string.Empty;
        public string SapceName
        {
            get => sapceName;
            set => SetProperty(ref sapceName, value);
        }

        public bool IsConfirm { get; set; } = false;
        public RelayCommand<Window> ConfirmCommand => new((window) =>
        {
            IsConfirm = true;
            window.Close();
        });
        public RelayCommand<Window> CloseCommand => new((window) =>
        {
            window.Close();
        }); 
    }
}
