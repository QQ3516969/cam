# NAS 服务入口与端口转发说明（重要）

更新时间：2026-04-29

这份文档记录本次摄像头控制台 `http://c6666.com/cam` 调试中确认的链路事实和坑点。后续任何 AI 或维护者接手新服务时，请先读这里，避免重复踩坑、乱建容器、乱改原项目。

## 当前结论

- 已验证稳定入口：`http://c6666.com:18880`
- 期望短入口：`http://c6666.com/cam`
- 当前实现方式：NAS 系统层把公网 HTTP 80 端口转发到本机 `18880`，再由 `cam-web` 的 Nginx 处理 `/cam`。
- 本次明确要求：不要再新建独立网关容器处理 `/cam`，除非主人明确要求。
- HTTPS 目前仍未彻底解决，是遗留坑；暂时使用 HTTP。

## 关键发现

### 1. `:18880` 是真正稳定的应用入口

摄像头控制台容器 `cam-web` 暴露：

```text
0.0.0.0:18880 -> cam-web:80
```

手机浏览器、微信、电脑访问 `http://c6666.com:18880` 都曾验证可用。

所以排查问题时，先保证 `:18880` 正常，不要为了修 `/cam` 改坏原始服务。

### 2. NAS 原生 Nginx 不是普通可持久修改的 Nginx

NAS 原生 Nginx 路径：

```text
/usr/trim/nginx/sbin/nginx
/usr/trim/nginx/conf/nginx.conf
/usr/trim/nginx/conf/conf.d/
```

但实测发现：

- 直接改 `/usr/trim/nginx/conf/nginx.conf`，reload/test 后可能被系统重新生成并覆盖。
- 直接往 `/usr/trim/nginx/conf/conf.d/` 放自定义 `cam_redirect.conf`，也会被系统机制清掉。
- 因此不要把“改 NAS 原生 Nginx 文件”当成可靠持久方案。

### 3. NAS 本机并没有进程监听 80/443

当时确认：

```text
LISTEN 0.0.0.0:5666  -> NAS 原生 HTTP
LISTEN 0.0.0.0:5667  -> NAS 原生 HTTPS
LISTEN 0.0.0.0:18880 -> cam-web
LISTEN 0.0.0.0:8889  -> MediaMTX WebRTC
```

没有本机进程监听：

```text
0.0.0.0:80
0.0.0.0:443
```

所以 `http://c6666.com/cam` 不通时，根因不是页面代码，而是公网 80 入口没有稳定落到正确服务。

### 4. 不能把 `/cam` 反代给 MediaMTX 页面

MediaMTX 的 WebRTC 地址类似：

```text
http://c6666.com:8889/cam/
```

这个是视频流/reader 页面，不是控制台 UI。

摄像头控制台 HTML 自己会加载 WebRTC reader，并使用固定流地址。`/cam` 短入口应该返回控制台页面，而不是反代到 MediaMTX 页面。

本次已调整 `cam-web` 配置：

- `location = /cam` 返回控制台 `index.html`
- `location /cam/` 返回控制台 `index.html`
- `location /cam/api/` 继续代理到 `cam-api`

文件位置：

```text
/vol2/1000/docker/cam-control/web/default.conf
```

### 5. 目前 80 -> 18880 使用 systemd + iptables 实现

为了不新建容器，当前用 NAS 系统层 NAT 转发：

```text
HTTP 80 -> 18880
```

systemd 服务：

```text
/etc/systemd/system/cam-port80-redirect.service
```

服务作用：

- 开机自动添加 iptables NAT 规则
- 把访问 NAS 80 端口的 HTTP 请求转到 `18880`

检查命令：

```bash
sudo systemctl status cam-port80-redirect.service
sudo iptables -t nat -S PREROUTING | head
```

预期能看到：

```text
-A PREROUTING -p tcp -m tcp --dport 80 -j REDIRECT --to-ports 18880
```

如果将来公网 IP 变化，本服务里的 OUTPUT 本机自测规则可能要更新，但外部访问主要依赖 PREROUTING 规则。

## 当前项目文件

项目目录：

```text
/vol2/1000/docker/cam-control
```

关键文件：

```text
/vol2/1000/docker/cam-control/web/index.html
/vol2/1000/docker/cam-control/web/default.conf
/vol2/1000/docker/cam-control/docker-compose.yml
```

当前 `docker-compose.yml` 只应保留：

```text
cam-api
cam-web
```

以前的 `cam-gateway` / Caddy 网关服务已经删除，不要自动恢复。

## 标准验证流程

接手后先按这个顺序测，不要只看电脑 200 就下结论：

```bash
curl --noproxy '*' -I http://c6666.com:18880/
curl --noproxy '*' -I http://c6666.com/cam?v=test
curl --noproxy '*' -L http://c6666.com/cam?v=test -o /tmp/cam.html
wc -c /tmp/cam.html
grep -o '视角控制\|预置控制\|镜头控制\|MediaMTXWebRTCReader' /tmp/cam.html | sort | uniq -c
curl --noproxy '*' -sS -X POST http://c6666.com/cam/api/control -H 'Content-Type: application/json' --data '{"key":"home"}'
```

预期：

- `:18880` 返回控制台 HTML
- `/cam` 返回同一套控制台 HTML，不应返回 MediaMTX 独立页面
- `/cam/api/control` 返回 `ok:true`
- 手机端仍需真人确认，因为之前电脑端正常但手机端白屏反复出现过

## HTTPS 遗留坑

HTTPS 到目前没有彻底解决。

已知问题：

- 很多浏览器会提示 HTTP 不安全，影响观感。
- Cloudflare / Zero Trust / 证书 / NAS 原生 443 之间关系没有完全跑通。
- NAS 原生 HTTPS 监听在 `5667`，不是公网标准 `443`。
- 本次没有建立稳定的 `https://c6666.com/cam` 方案。

后续处理 HTTPS 时请单独开任务，不要和摄像头控制台功能混在一起改。优先目标应该是：

```text
https://c6666.com/cam -> 控制台
```

但在没有清楚证书、443 入口、Cloudflare 模式之前，不要盲目改动现有可用 HTTP 链路。

## 不要再踩的坑

- 不要为了 `/cam` 白屏先去改 `index.html` 里的 UI 和按钮逻辑。
- 不要把 `/cam` 直接代理到 `:8889/cam/`，那是 MediaMTX，不是控制台。
- 不要只在电脑端验证，手机微信/手机浏览器才是关键验收环境。
- 不要依赖 `/usr/trim/nginx/conf/nginx.conf` 的手改内容长期存在。
- 不要自动新建 Caddy / gateway 容器，除非主人明确要求。
- 不要把 `.env`、Cloudflare token、NAS 密码写进文档或提交。

## 一句话给后续 AI

这个 NAS 上的新服务如果想做 `http://c6666.com/xxx`，先确认 80/443 入口到底落到哪里；当前摄像头项目的可用策略是“80 端口系统层转发到已验证稳定的服务端口”，而不是随手改生成型 NAS Nginx 或新建网关容器。
