# DeepTutor-SIWC：个人学习修改版

本仓库基于 [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor) v1.7.2，是个人维护的 Fork。
保留原项目源码、贡献记录、[Apache-2.0 许可证](LICENSE)及第三方说明。

## 本修改版包含什么

- 独立的 **ChatGPT (SIWC)** 提供商，通过 OpenAI 的 Sign in with ChatGPT 流程授权本机应用。
- 本机 Docker 部署配置 `compose.siwc.yaml`，网页和授权回调仅发布到回环地址。
- 设置页返回目标校验，避免异常地址被误记为“返回应用”的目标。
- 模型选择器显示可读的 ChatGPT 模型名称，保留各账户对应的内部模型绑定。

原项目的 API Key 和 Codex OAuth 配置仍保留。上游的安装包和预构建镜像对应上游版本；使用本修改版时请从本仓库源码构建。

## 用 Docker 启动

先启动 Docker Desktop，使用 Linux 容器模式。下载本仓库后，在仓库目录执行：

```powershell
New-Item -ItemType Directory -Force -Path data/user/workspace/outputs,data/cli-apps | Out-Null
docker compose -f compose.siwc.yaml up -d --build
docker compose -f compose.siwc.yaml ps
```

首次构建需要联网下载依赖，并会占用数 GB 的 Docker 磁盘空间。构建期间保持 Docker 运行。可以先在 Docker Desktop 的 Resources 设置中选择空间充足的数据盘。

浏览器打开 <http://127.0.0.1:3782>，进入“设置 → 服务提供商 → ChatGPT (SIWC)”，点击 **Continue with ChatGPT**，在 OpenAI 页面完成登录与授权，然后选择账户可用的模型。

端口：网页 `127.0.0.1:3782`，授权回调 `127.0.0.1:1468`。后端与 sandbox runner 不发布宿主机端口。后端工作进程保持默认值 `1`。

后续启动和停止：

```powershell
docker compose -f compose.siwc.yaml up -d --no-build
docker compose -f compose.siwc.yaml down
```

Linux/macOS 可用 `mkdir -p data/user/workspace/outputs data/cli-apps` 准备目录，其余 Compose 命令相同。

## 数据放到其他磁盘

应用数据默认保存在本机 `data/` 中。需要放到 D 盘时，在仓库目录的本机 `.env` 中设置：

```dotenv
DEEPTUTOR_DATA_HOST=D:/DeepTutor/data
DEEPTUTOR_WORKSPACE_HOST=D:/DeepTutor/data/user/workspace
```

首次使用这些目录前先创建工作区和 `cli-apps` 子目录。已有数据时先停止服务，复制并核对文件后再切换路径。Docker 自身的镜像和数据卷位置在 Docker Desktop 中另行设置。

账户凭据、知识库、学习资料和实际 `.env` 属于各自的本机数据；仓库只提供源码和公开配置模板。Docker 中的 Linux 凭据文件使用相应文件权限保护，不与 Windows 原生版的 DPAPI 凭据文件混用。

## 使用范围与验证

账户资格、可用模型、额度及区域或组织限制以 OpenAI 实际授权结果为准。当前实现支持本机单进程运行。SIWC 提供的推理能力与 embeddings、图像/视频生成等服务分别配置；需要这些功能时设置对应提供商。

相关验证覆盖模拟 OAuth 回调与身份校验、模型目录和账户绑定、设置页返回、模型选择器、生产构建及 Docker 健康检查；不等同于对所有账户与学习功能的完整验证。

实现细节及测试命令见 [CHATGPT_SIWC.md](docs-for-dev/CHATGPT_SIWC.md)。原项目介绍与完整功能说明见 [README.md](README.md)。
