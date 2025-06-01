# Pixiv 图片搜索 API

这是一个基于 FastAPI 和 pixivpy 构建的 API 服务，用于从 Pixiv 上搜索指定标签的图片，并支持多标签搜索和 R18 内容过滤。

## 特性

-   通过关键词搜索 Pixiv 图片。
-   支持多个标签同时搜索（使用逗号分隔）。
-   支持 R18 内容筛选。
-   随机返回一张符合条件的图片。
-   API 直接以图片流的形式返回图片，可直接在浏览器中显示或被客户端用作图片源。
-   使用 Docker 容器化部署。

## API 端点

### `GET /pixiv/direct`

根据关键词搜索 Pixiv 图片，并随机返回一张符合条件的图片。

**请求参数:**

-   `keyword` (str, 必需): 搜索关键词（标签）。多个标签请用英文逗号 `,` 分隔。例如: `tag1,tag2`。
-   `r18` (int, 可选, 默认: `0`): 是否搜索 R18 内容。
    -   `0`: 非 R18 内容。
    -   `1`: R18 内容。

示例：http://localhost:2494/pixiv/direct?r18=0&keyword=

**错误响应:**

-   `400 Bad Request`: 如果 `keyword` 参数为空。
-   `404 Not Found`: 如果没有找到符合搜索条件和R18设置的图片，或者选中的图片没有可用的图片URL。
-   `500 Internal ServerError`: 如果在与 Pixiv API 通信或处理过程中发生内部错误。
-   `502 Bad Gateway`: 如果API服务器在尝试从Pixiv获取图片时遇到问题。

## 前提条件

-   一个有效的 Pixiv `refresh_token`。

## 如何获取 `PIXIV_REFRESH_TOKEN`

`pixivpy` 库需要一个 `refresh_token` 来进行认证。获取此 token 的方法如下：

**使用`pixiv_auth.py`获取`refresh_token`**:
-   在项目根目录空白处按住 Shift 并右键鼠标，从“在此处打开终端/在此处打开 Powershell 窗口/在此处打开 Linux shell”中任选其一，在弹出的命令窗口中输入：`python pixiv_auth.py login`。这将会打开一个带有 Pixiv 登录界面的浏览器。
-   通过F12打开浏览器的开发控制台并跳转至“网络（Network）”选项。
-   记录网络日志。大多数情况下打开就是默认启动的，但是还是要检查一下。
-   在筛选器中输入：`callback?` 。
-   登录你的 Pixiv 账号
-   登录后会跳转到一个空白页面，但是在开发控制台里会出现你筛选的带有 `callback?` 的访问请求，点击这条请求，将“`https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback?state=...&code=...`”中的 `code` 复制到命令窗口中。
-   这样就会获取到 `Refresh Token`。

**重要提示: 如果最后按照这个步骤没有获取到 `Refresh Token`，那么重新操作一遍，并尽可能的提高速度，`code` 会很快过期。**

教程来源：https://mwm.pw/87/

## 本地运行

1.  **克隆仓库**:
    ```bash
    # git clone https://github.com/Sor85/pixiv_search_api.git
    # cd pixiv_search_api
    ```

2.  **创建并激活虚拟环境**:
    ```bash
    python -m venv venv
    source venv/bin/activate  # Linux/macOS
    # venv\Scripts\activate    # Windows
    ```

3.  **安装依赖**: 
    ```bash
    pip install -r requirements.txt
    ```

4.  **设置环境变量**: 
    将你的 Pixiv `refresh_token` 设置为环境变量 `PIXIV_REFRESH_TOKEN`。
    ```bash
    export PIXIV_REFRESH_TOKEN="你的_refresh_token"
    ```
    在 Windows上，可以使用 `set PIXIV_REFRESH_TOKEN="你的_refresh_token"` (cmd) 或 `$env:PIXIV_REFRESH_TOKEN="你的_refresh_token"` (PowerShell)。

5.  **运行 FastAPI 应用**: 
    ```bash
    uvicorn main:app --host 0.0.0.0 --port 2494 --reload
    ```

    API 将在 `http://localhost:2494` 上可用。

## 使用 Docker 运行

1.  **构建 Docker 镜像**: 
    在项目根目录下运行以下命令：
    ```bash
    docker build -t pixiv-search-api .
    ```

2.  **运行 Docker 容器**: 
    ```bash
    docker run -d -p 2494:2494 -e PIXIV_REFRESH_TOKEN="你的_refresh_token" --name pixiv_api pixiv-search-api
    ```

    API 将在 `http://localhost:2494` 上可用。