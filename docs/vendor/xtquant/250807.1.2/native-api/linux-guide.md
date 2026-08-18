# Linux版xtquant快速开始指南

> **Archive provenance**
>
> Source: [official Linux quick-start guide](https://dict.thinktrader.net/nativeApi/Linux%E7%89%88xtquant%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B%E6%8C%87%E5%8D%97.html)  
> Retrieved: `2026-08-09T17:22:27Z`  
> Transformation: the VuePress article body was converted to GFM; site navigation, scripts, and UI chrome were removed.  
> **Scope warning:** the upstream guide explicitly covers XTData only and says XTTrade is not supported on Linux. It also requires an eligible research-terminal edition. It is not evidence of Linux live-trading support.

## 所需环境

### 1.下载xtquant的linux版压缩包

> **提示**
>
> 1.  Linux版需要用户权限为`投研专业版`及以上
> 2.  Linux环境下仅支持数据获取功能(xtdata)，不支持交易(xttrade)

[用户中心](https://xuntou.net/#/userInfo)

若您的账号拥有所需权限

您将在 用户中心 -\> 下载中心 看到如下页面

![用户中心权限](https://dict.thinktrader.net/assets/用户中心权限-a6eff271.png)

### 2.解压并配置路径

压缩包内的文件结构基本如下图所示

![解压并配置路径](https://dict.thinktrader.net/assets/解压并配置路径-a7b54d6b.png)

linux下新建一xtquant文件夹，将压缩包内**全部文件**解压至xtquant中。

将xtquant文件夹加入python**搜索目录**。

### 3.Python版本选择

所支持的版本将于包名中显式列出

如：

![Python版本选择](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAZYAAAAYCAYAAADZNduKAAAJOElEQVR4nO2dwWvbShrAf0keu7DsYS97KhThPUx7KDiH3h4mB4MJ/QNm4e3BDfiUPyDosehgHs/kthfDgiHV4R2q64OuMfgQRHtYWraGFlJlt0YEclp2eYdSeEsT70GSLcnSyHKUvLTMD0Rjz8w338x8832aT0q68erVqxkajUaj0VTExmw204FFo9FoNJXxFcDp6ekvrYdGo9FovhA2f2kFNBqNRvNl8VVRhefnM56fw9v/bPDuvxcA/OF3m2z/Hr6+A1/f2bh2JTUajUbz+bAxm81mWamw8w/ww8klT0/g4wXMevf49Z89/nexeCTzmy3443340/1N7vz2JtXWaDQazW0lMxV2/gH+8o9Ljt4GQQXgr3//iZ+/E/xqa3FC+XgBR2+DuucfbkRfjUbzWeNiCQsXwLeRQmL7v7ROmiWuuDaZgeWHk0t+fJ/87m9eEDl+/k6wmcp+/fg+aPNF41oIIcIrPeEu1rws3DQsl8v0Kq0sM1Xm28jcdlVSMK6E/lnjrrZf35ax/haXVVnHivEm5nyVPleVtcr6rWlfKhuqtJ+iMk2CIlsqu68K69/82iwFlufnM56eLFd89m5xJLn4/t5ScHl6ErQth48tq3QM67CKDi5Wv8bI8/A8D28g6LWiBfSx5ZhmWDaQDp25sEC2EGOQZWV28MxRRpmL1eohBrF2BzbVm4xqXATGHNff69K45n6NthP2FV4jkzqSZiUdq8abnnOJ01Ft+DKyitZvXftS2VDV/eSVFWC0cTyHtlGy3WdNgS2V3Vcr1PftPk5ZNa+4NhmBZZH+guDZyqx3j4vv7yXqpYPLx4ug7ZdJg67TZj7HjSYSj6kPYNB2FovZaErwpqGjMGg7wWI3y8j0jxlOJPvRqjb2MOsOYxdwxzh1k715h3uYDDmuPLKoxuVj9z3Mw5j+N9JvEveohxhUFdCK+o0FsMT6l5RVev3WtC+VDVXZj7JMk02eLZXdVyvU920OhruYZYP+FVkKLK//nfy89e273MbpYJNuGxGkMBZR2bUEwrKwRIveBJyOQMjori1+HA+vqMy3kfGjXubnrCOmiyUktr04MgbHQjdHhwLcMQ6CWsZqumOH+u5OeYcbl3k2ZSKbMYdpUBPg5XqyCdMzpfDEnAZjj05qybK8k1tiXP4xQ3bhKC0z6ktiu4u1sFwSx/UyR/Lc+XQtOl7MQV9rvw32TI9+1N4d48j9le/mim2iaP3KyAopbUNr9nMVEvs3b4+m6yU/Z/sWVfohlR60rZTvKa9D9tDy9FLYknJfZXVSVN/HPugh9tvUlHqm/K3lpsZXzldARmB5/1PyWcnlLBlcPl3O2Pr2HRtmcKnaRhhth4F0gskMHcKo26XrjTDrIAcentPGwMUSHRgs0h2DlSOtj30Eh15eumJCb9qcp1DoHWD7jQwdirqxkR0HGb9TjgW0cdPDKXt+TMn0p15+3bs16pMeR3OLPaI3UQkP5nSeEvGS+jmdRfpjac7yxnU2ZTLpMW16qfmMGk7o9cO1GMggaI/jc3+kzhsXzqeP3XeQ++n1ut5+J71WsKk6DvXaXZWkfFml12+V+choorKhCvuplqw9qm6R7VvyzrBp/3IIw3SSqLwO6+iVaUuF+ypFQX3XajHcHZE7HaTSywMJ9fz5U/qKFEuB5dU3W3iPNxPXSXuT09NTTk9Pmf7rn5y0N5fqeI83efXNVu4AGt0BotdCdBRHt3SagPBIvhIG7W4bogjcSRtMHTMSbOywW19RbAzflojWkN2Rl1wso40TTnhzXOLkkyPTqIn8BkYbJ3KaQiD6NUxZJ9fPhXN6mOMkEgEynS5RjSu+TkabfTlhOM/n1Bdr3GgiE3NfQ8yP/jkPl4vmM0zzLD9buaZ+fZuDHpijyCEFthzcsZWUpVy/NecjA6UNVdhPtay3R7N9S8YYl/yLQXs/7V+u7ieUeiltiYJ9lUFe/TCY5e37JXwb2YGB4uZa6StSLP2C5OPHj1dTJIcnT55cqf3a+Day1QNzhOcZ4edpZeJdS9BhgOepM/qN7gAp+hz77cJUiVKmN8WnES6yz9QD0Yzy5V08rxtJwRKCZndZRJUkxnW3xhX2W1wqXc9DpXrWfLpHwTqv/2ylXL87x0Mmch9n8UCMpoT+1IfGGmPIXb/15iOXXBsyqu3nVpIxlyumG68TX2VLOyX3Ve4+PMPuOzCBlujFvm8hhiajpeDhYrWG7I6cip5XZpxYLi8v59eLFy8SV/RdXtnlZf4rx64VpGOC01rOnVCjiYynCcKUR5LFQ1P/eMg8i3A2ZRK7M0+UXRXfpu9IBllHRN9GxpKNvt3Hqe+yU7QZVTIbe5ik0iUkT3IRwbzu5RvE0py62LGztTOO635ALzoJqMZl7LBLj4NY/rnv1NktHPQKFM6ny7iqvlbs19jZpe70YykJl7EDIush20pjWFC4fuvaVwkbulI/N0bOvmdF3wIr+pf1dMgiSy+lLZXdV7n1G+ELFclHCnVzFKT7E7+jErw9yKD4DbBcX5HB0onl06dP858fPnwIwMuXLxNl0b/x8ni7NK4lwhyjgcEh5rBFy6rhdRvs7NbpdQROPYik3YFEdET4elwd05QwjCayzaE5pNUS9IC6lIuI3djD7LfmETpRpsRY0mFpfs+mTHDoiKQRyoFHt9HmsCYRohN9y8Bb4VmNUqZB+9BEtgRiSaaPLVvzvHzdHBXkwht0R2lZXQi3n2Sc0j087hqqcRm0nQFT0SK6IZIDr5o7W2W/gD/FQ9Cs2uGp+jXaOIMpIrQ7iNZpnTGUXL+i+chvqLChKvu5ART7XuVblknvhZR/WVOHLPL1UtlS2X119X3oWqEtzn0uIAd4e8t1c31FBkt/0kXKZM7x9evXAGxvb88/b29vz7+PiModp/Qb02qi97RXebCuKUHg4Kb7Cgep0XzpaP+yAuV9hfLE8ubNGwAePHiQWSf9vUaj0dxefGx5RG3++zouVsehbo50UKkYZWCJiALM/fv3E3VU6a/Pk+B1xPSZS5n2uDV8zrprvjxuoz0atA9rSCGIEjrFqeQibmKct3Eu1Sylwh49enQlgc+ePbuqThqNRqP5jMn9s/kajUaj0ayD/h8kNRqNRlMpG7PZrOyfJNZoNBqNJhd9YtFoNBpNpfwf04fuaXxDKD4AAAAASUVORK5CYII=)

支持python3.6，3.7，3.8，3.9，3.10，3.11，3.12

### 4.linux系统版本

\

## 常见问题

- **无法找到xtquant模块**

  ![无法找到xtquant模块](https://dict.thinktrader.net/assets/无法找到xtquant模块-71c67d0d.png)

  请检查python搜索目录是否包含xtquant文件夹

- **无法打开动态库**

  ![无法打开动态库](https://dict.thinktrader.net/assets/无法打开动态库-c69cf3c8.png)

  在确保符合**所需环境**的要求后，如仍出现该问题，也许与您所使用的复杂环境相关，可尝试以下方法解决：

  方法1.手动将**压缩包中.libs文件夹**下的文件拷贝至 **系统根目录**下的 /lib 或 /lib64 中（**推荐**） ![方法1](https://dict.thinktrader.net/assets/无法打开动态库_方法1-5fecc13b.png) 方法2.export LD_LIBRARY_PATH=\$LD_LIBRARY_PATH:/home/test/linux_pack/xtquan/.libs

- **数据接口无法返回数据**

  请检查系统时间是否校准，在确保时间正常后重新获取数据。
