# n8n Task Console

**让 Python 脚本按计划运行，随时查看日志与结果。**

[English](README.md) · 简体中文

独立编写的自托管任务平台，使用 n8n 定时唤起调度。通过网页选择脚本、填写参数和时间，查看执行状态、日志和输出文件，无需操作 n8n 工作流编辑器。

> 当前为早期开发版本，适合可信的脚本作者使用，不是运行陌生人代码的安全沙箱。已完成和未完成的验证见[验证记录](docs/VERIFICATION.md)。

## 功能

- Python 单文件、ZIP 项目、参数表单、发布版本和固定版本任务。
- 手动、间隔、每天、工作日、每周、每月、五字段 Cron，明确任务时区和未来时间预览。
- 执行队列、同任务互斥、超时与取消、stdout/stderr、输出文件下载。
- 管理员和操作员、只写变量、独立账号。
- **首次进入始终是英文**；可主动切换简体中文，保存个人语言偏好，切换不改变任务时间。

## 快速开始

先安装 Docker Desktop，或 Docker Engine 与 Compose 插件。运行计划任务期间，需要电脑和 Docker 持续运行。

```bash
git clone https://github.com/haowenchen0811/n8n-task-console.git
cd n8n-task-console
docker compose up -d --build
docker compose exec web python -m taskconsole setup-token
```

开发版首次会本地构建应用镜像，并下载固定版本的 PostgreSQL 和 n8n 镜像；网络速度会影响耗时。发布工作流会为正式标签构建多架构应用镜像，尚未发布的镜像不可直接使用。

打开 **http://localhost:8080**，输入本机 setup token，设置管理员账号、至少 12 位密码和时区。点击 **简体中文** 切换界面。

1. 新建任务，选择 **Create an output file** 示例。
2. 设置每 1 分钟运行，查看预览后创建并启用。
3. 等待执行成功，在执行详情下载 `hello.txt`。

示例不需要邮件、云服务或业务数据库凭据；n8n 自动初始化，无需手动导入工作流或创建 API Key。保存任务不会立即运行脚本。

## 自定义脚本

单文件可以定义同步入口：

```python
def main(params):
    print(f"Hello, {params.get('name', 'world')}!")
```

也可以使用普通顶层 Python 脚本。`TASK_PARAMS_FILE` 是 JSON 参数文件，`TASK_OUTPUT_DIR` 是本次输出目录，`TASK_RUN_ID` 是执行标识。ZIP 使用 `main.py` 作为入口，依赖写在 `requirements.txt`，使用 `包名==版本` 固定版本。发布时准备依赖，运行时不重复安装。

参阅[脚本契约](docs/SCRIPTS.md)、[架构](docs/ARCHITECTURE.md)、[备份与恢复](docs/OPERATIONS.md)。

## 停止、更新与边界

```bash
docker compose ps
docker compose stop
docker compose start
```

数据存储在持久卷。`docker compose down -v` 会删除卷，不应作为常规重启命令。升级前备份。

- 单个可信工作区，不提供陌生用户之间的代码隔离。
- 电脑休眠、关机或 Docker 停止时，定时任务不会继续运行。
- 错过的计划不集中补跑，同一任务重叠触发会记录为跳过。
- 工作日为周一到周五，不处理法定节假日或调休。
- 收件人字段只向脚本传参，邮件由脚本自行发送。
- 高级共享环境编辑、平台级邮件通知不属于当前版本。

## 开发与许可证

Python 3.12+；安装 `requirements-dev.txt` 后运行 `python -m pytest -q`。完整部署使用 Docker Compose；本地 SQLite 模式供 API/UI 开发与测试，不带自动 n8n 调度。

本项目独立代码使用 [MIT](LICENSE) 许可证。n8n 使用其自己的许可证，不会因此变成 MIT；详见[第三方说明](THIRD_PARTY_NOTICES.md)。本项目与 n8n 官方无隶属关系。
