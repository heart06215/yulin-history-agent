# 线上部署准备

项目已提供单机部署结构：Caddy 对外提供 HTTPS，Node 前端与 FastAPI 共用一个域名，`/api/*` 转发到后端。SQLite、扫描原页和识别成果放在后端持久卷；模型密钥只由后端运行时读取。当前没有发布到公网。

## 配置与密钥

保留现有 `.env` 用于本地开发；上线使用独立的 `.env.production`。`.env.production.example` 与 `deploy/.env.example` 只有配置模板，密钥均为空。真实环境文件、原始材料、数据库和本地测试数据已排除出 Git 与 Docker 构建上下文。Vite 的 `import.meta.env` 前缀已收紧为 `VITE_PUBLIC_*`；该前缀和框架的 `NEXT_PUBLIC_*` 都只能存放公共配置，不能放模型密钥和管理令牌。

在部署主机的项目根目录复制模板，并为生产环境生成新管理令牌。以下命令把令牌写入文件，不显示其内容：

```sh
cp .env.production.example .env.production
cp deploy/.env.example deploy/.env
python -c 'import secrets; from dotenv import set_key; set_key(".env.production", "ADMIN_TOKEN", secrets.token_urlsafe(48))'
```

Python 命令需要 `python-dotenv`，可使用安装后端依赖的 Python 环境。填写 `deploy/.env` 的 `SITE_DOMAIN`，并让 `.env.production` 的 `PUBLIC_ORIGIN` 与 `https://该域名` 一致。填写生产环境的模型密钥；使用独立配额方便控制调用开支。文本通用接口的 `TEXT_API_KEY / TEXT_BASE_URL / TEXT_MODEL` 要全部填写或全部留空。

密钥文件通过 [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) 挂载到 `/run/secrets/backend.env`，不会成为前端构建参数或容器环境变量。此方式依赖主机文件权限，不会加密主机上的源文件。后端使用 UID/GID `10001`；在 Linux 主机上允许该组读取，保持其他用户不可读：

```sh
sudo chgrp 10001 .env.production
chmod 640 .env.production
chmod 600 deploy/.env
```

首次上线必须使用新的 `ADMIN_TOKEN`，不要把本地开发令牌当作线上令牌。管理员在工作台输入令牌后只保存在页面内存中，刷新后重新输入。管理接口返回“已配置”，不会返回密钥片段。生产模式在启动时拒绝弱管理令牌、HTTP 站点来源、通配域名和包含凭据的模型 URL。

## 构建与启动

部署主机需要 Docker Engine 与 Compose。为域名设置 DNS 到服务器，开放 80/443 后，执行：

```sh
docker compose -f deploy/compose.yaml --env-file deploy/.env build
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm --no-deps edge caddy validate --config /etc/caddy/Caddyfile
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d
docker compose -f deploy/compose.yaml --env-file deploy/.env ps
```

Caddy 按配置申请 HTTPS 证书，API 与前端容器没有直接公开端口。API 的反向代理信任范围固定为本方案的 Caddy IP `172.30.0.2`；[Caddy 默认忽略客户端伪造的转发头](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#defaults)。如调整网络子网，需要同步修改 Compose 地址和后端 Dockerfile 的 `--forwarded-allow-ips`。默认结构直接面向客户端；添加 CDN 后，需要按 CDN 官方地址范围重新配置代理信任。

后端保持 **1 个 worker、1 个实例**。当前队列与限流为进程内状态，SQLite 使用单机持久卷；扩容前需要把队列与限流移到共享服务，并评估数据库迁移。`read_only` 容器只允许写入 `/data` 与临时目录。

前端另外保留通用 Node 构建命令：

```sh
cd apps/web
npm ci
npm run build:node
npm run start:node
```

这是 [Vinext standalone Node 输出](https://github.com/cloudflare/vinext)。单独启动 Node 只提供前端；完整站点仍需把 `/api/*` 代理到后端。原有本地 `.bat` 启动与 Cloudflare 构建配置仍可使用。

## 材料迁移与备份

首次空卷不会自动包含本机的方志库。迁移时先停止识别任务并等待结束，再暂停旧后端写入，使用 SQLite backup API 导出一致的 `catalog.sqlite3`，连同 `raw/` 与 `previews/` 导入 `yulin-history_yulin_data` 持久卷（实际名称以 `docker volume ls` 为准）。保持文件相对路径和 UID/GID `10001`。后端兼容旧 Windows 目录分隔符，新增入库路径统一保存为 `/`，可随整个数据目录迁移。不要直接复制仍在写入的 WAL 数据库文件。

只迁移数据库和私有材料，不把它们放入前端 `public/`。沿用已有的材料可见性与公开权利校验，管理员识别初稿和未授权扫描页不会因为部署而自动公开。卷与密钥文件要分别备份，并实际验证一次恢复。升级前备份卷；停止服务使用 `docker compose ... stop`，保留卷。

## 发布前验收

在有 Python 依赖的环境执行 `python scripts/check_release_secrets.py --env-file .env.production`。检查器会对照实际密钥扫描源代码、构建结果和编码后的字面量，只输出文件路径，不输出密钥。它不能撤销已经泄露的凭据；如将来发现泄露，应在模型供应商后台撤销旧密钥并替换运行文件。

已完成本地验收：138 项后端测试、TypeScript 类型检查、ESLint、Node 生产构建与服务冒烟检查（首页、文献库、城市图片均返回 200），源代码及构建产物未检测到当前配置的密钥。生产接口新增同源写入保护、Secure/HttpOnly/SameSite 会话 Cookie、按 IP 的请求限流、错误信息脱敏与 API 禁止缓存；API 文档在生产环境关闭。公网验收还应确认首页与文献库、未认证管理请求被拒绝、HTTPS 会话、流式问答，以及一页材料的识别/发布流程。涉及模型的验收会实际消耗配置的配额。

本机没有 Docker，尚未执行容器镜像构建与完整 HTTPS 代理验收；这两项需要在部署主机上完成。本次准备没有更改或撤销当前本地密钥。
