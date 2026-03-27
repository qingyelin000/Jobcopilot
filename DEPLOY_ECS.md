# 阿里云 ECS 部署指南

这份指南对应当前项目的生产部署方式：

- 前端：`frontend`
- 后端：`backend`
- 数据库：`mysql`
- 编排方式：`docker compose -f docker-compose.prod.yml`
- 当前线上默认只开放简历优化功能，不开放聊天功能

## 1. 服务器准备

建议：

- 系统：`Ubuntu 22.04`
- 配置：`2核4G` 起步
- 安全组开放：
  - `22`：SSH
  - `80`：网页访问

如果你暂时没有域名，先用公网 IP 访问即可，不需要先配 HTTPS。

## 2. 登录服务器

在你自己的电脑上执行：

```bash
ssh root@你的ECS公网IP
```

如果你用的是阿里云控制台重置的密码，就输入密码登录。

## 3. 安装 Docker 和 Docker Compose

在服务器里执行：

```bash
apt update
apt install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable docker
systemctl start docker
docker --version
docker compose version
```

## 4. 上传项目到服务器

有两种常用方式。

### 方式 A：服务器直接拉代码

如果你的代码在 Git 仓库里：

```bash
cd /opt
git clone 你的仓库地址 Jobcopilot
cd /opt/Jobcopilot
```

### 方式 B：从本地上传

在你自己的电脑上执行：

```bash
scp -r "D:\study resources\Jobcopilot" root@你的ECS公网IP:/opt/
```

上传完成后，回到服务器执行：

```bash
cd /opt/Jobcopilot
```

## 5. 创建生产环境变量

在服务器里执行：

```bash
cp .env.prod.example .env.prod
```

然后编辑：

```bash
nano .env.prod
```

至少改这些值：

```env
DEEPSEEK_API_KEY=你的_deepseek_key
OPENAI_BASE_URL=https://api.deepseek.com

MYSQL_DATABASE=jobcopilot
MYSQL_USER=jobcopilot
MYSQL_PASSWORD=你自己的数据库密码
MYSQL_ROOT_PASSWORD=你自己的root密码

JWT_SECRET=一段足够长的随机字符串
JWT_EXPIRE_MINUTES=1440

GEO_AMAP_KEY=
GEO_TENCENT_KEY=

FRONTEND_ORIGINS=http://你的ECS公网IP
FRONTEND_PORT=80

ENABLE_CHAT=false
VITE_ENABLE_JOBS=false
NPM_REGISTRY=https://registry.npmjs.org/
NPM_REGISTRY_FALLBACK=https://registry.npmmirror.com/
```

说明：

- `ENABLE_CHAT=false`：后端关闭聊天接口
- `VITE_ENABLE_JOBS=false`：前端隐藏聊天入口
- `NPM_REGISTRY`：前端构建主 npm 源
- `NPM_REGISTRY_FALLBACK`：主源失败时自动切换的备用源
- `FRONTEND_ORIGINS` 先填你的公网 IP

## 6. 启动项目

在服务器项目目录执行：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

首次启动会花几分钟，因为要拉镜像和构建镜像。

## 7. 检查是否启动成功

查看容器状态：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

查看后端日志：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend
```

查看前端日志：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f frontend
```

查看数据库日志：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f mysql
```

如果三个服务都是 `Up`，浏览器访问：

```text
http://你的ECS公网IP
```

## 8. 常用维护命令

停止服务：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml down
```

重新启动：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
```

重新构建并启动：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

查看运行中的容器：

```bash
docker ps
```

## 9. 常见问题

### 1. 页面打不开

先检查：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

再检查阿里云安全组是否放行了 `80` 端口。

### 2. 后端启动失败

看日志：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend
```

常见原因：

- `DEEPSEEK_API_KEY` 没填对
- `JWT_SECRET` 没填
- `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD` 没填

### 3. MySQL 起不来

看日志：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f mysql
```

常见原因：

- MySQL 密码为空
- 服务器内存太小

### 4. 更新代码后怎么上线

进入项目目录后执行：

```bash
git pull
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

### 5. 前端构建时报 npm `ECONNRESET`

如果你在 ECS 上构建前端遇到 npm 网络错误，可把 `.env.prod` 里的主源改成镜像源：

```env
NPM_REGISTRY=https://registry.npmmirror.com/
NPM_REGISTRY_FALLBACK=https://registry.npmmirror.com/
```

然后用明细日志重新构建（便于看到是否在下载或重试）：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml build --no-cache --progress=plain frontend
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d frontend
```

## 10. 下一步建议

你现在这套部署适合：

- 小范围试用
- 先给少量用户使用
- 只开放简历优化

后面如果你准备正式对外，再继续补：

- 域名
- HTTPS
- 数据库备份
- 持久任务系统
- 监控与限流
