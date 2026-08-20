using AiConstruction.Api;
using CommunityToolkit.Mvvm.ComponentModel;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace AiConstruction.ViewModel
{
    public partial class AuthorizationVM : ObservableObject
    {
        /// <summary>图片</summary>
        [ObservableProperty]
        private string _imgUrl = string.Empty;
        private readonly AccountApiClient _accountApi = new();
        public AuthorizationVM()
        {
            try
            {
                var responseBody = _accountApi.GetQRCode();
                JObject jsonObject = JObject.Parse(responseBody);
                string code = jsonObject["code"].ToString();
                var msg = jsonObject["msg"].ToString();
                if (code == "200")
                {
                    JToken data = jsonObject["data"];
                    //url1 = data["url1"]?.ToString() ?? "";
                    ImgUrl = data["url2"]?.ToString() ?? "";

                }

            }
            catch
            {

            }
        }


    }
}
