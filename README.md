# GLaDOS / Railgun 多域名签到

本项目支持：

- `glados.rocks` 与 `railgun.info` 两个域名；
- 每个域名单独配置 Cookie；
- 多账号通过“账号名”区分；
- 每个账号单独设置兑换计划；
- `plan0` 表示关闭兑换；
- 积分不足时不调用兑换接口；
- 企业微信群机器人或企业微信自建应用推送；
- 仅使用 GitHub Actions 定时运行。

## 一、配置模型

本版本不再使用旧变量：

```text
GLADOS_COOKIES
GLADOS_EXCHANGE_PLAN
PUSHDEER_SENDKEY
```

原因是旧变量会把所有 Cookie 依次套用到所有域名，无法保证 Cookie、域名和兑换计划一一对应。

新版本使用四个核心变量：

| 域名 | Cookie 变量 | 兑换计划变量 |
|---|---|---|
| `glados.rocks` | `GLADOS_ROCKS_COOKIES` | `GLADOS_ROCKS_PLANS` |
| `railgun.info` | `RAILGUN_INFO_COOKIES` | `RAILGUN_INFO_PLANS` |

### Cookie 变量格式

每行一条：

```text
账号名|Cookie
```

示例：

```text
主账号|koa:sess=AAAA; koa:sess.sig=BBBB;
备用账号|koa:sess=CCCC; koa:sess.sig=DDDD;
```

### 兑换计划变量格式

每行一条：

```text
账号名|兑换计划
```

示例：

```text
主账号|plan0
备用账号|plan500
```

脚本通过账号名匹配 Cookie 和兑换计划，不依赖行号。因此两份变量中的账号顺序可以不同，但账号名必须完全一致。

### 兑换计划

| 计划 | 所需积分 | 行为 |
|---|---:|---|
| `plan0` | 0 | 不兑换，完全跳过兑换接口 |
| `plan100` | 100 | 积分达到 100 时兑换 |
| `plan200` | 200 | 积分达到 200 时兑换 |
| `plan500` | 500 | 积分达到 500 时兑换 |

安全规则：

1. Cookie 已配置、计划未配置：自动按 `plan0` 处理；
2. 计划无效：脚本直接报错，不会自动改成其他计划；
3. 积分查询失败：不兑换；
4. 当前积分低于目标积分：不兑换；
5. 只有积分达到目标值时，才调用兑换接口。

## 二、双域名、多账号完整示例

### `GLADOS_ROCKS_COOKIES`

```text
张三主号|koa:sess=AAAA; koa:sess.sig=BBBB;
张三备用|koa:sess=CCCC; koa:sess.sig=DDDD;
```

### `GLADOS_ROCKS_PLANS`

```text
张三主号|plan0
张三备用|plan500
```

### `RAILGUN_INFO_COOKIES`

```text
李四主号|koa:sess=EEEE; koa:sess.sig=FFFF;
李四备用|koa:sess=GGGG; koa:sess.sig=HHHH;
```

### `RAILGUN_INFO_PLANS`

```text
李四主号|plan200
李四备用|plan0
```

这会生成四个独立任务：

```text
张三主号 → glados.rocks → plan0
张三备用 → glados.rocks → plan500
李四主号 → railgun.info → plan200
李四备用 → railgun.info → plan0
```

不会再出现一个 Cookie 同时请求两个域名的问题。

签到请求的 token 已按两份原项目分别保留：

```text
glados.rocks → glados.one
railgun.info → railgun.info
```

域名、Cookie、签到 token 和兑换计划都会绑定在同一个独立任务中。

## 三、获取 Cookie

Cookie 必须在对应域名下单独获取。`glados.rocks` 的 Cookie 不应直接作为 `railgun.info` 的 Cookie 使用，反之亦然。

操作步骤：

1. 登录目标域名；
2. 打开签到页面；
3. 按 `F12` 打开开发者工具；
4. 切换到 `Network`；
5. 刷新页面或手动签到一次；
6. 找到 `checkin`、`status` 或其他 `/api/user/` 请求；
7. 在 `Request Headers` 中复制完整 `Cookie`；
8. 放入相应域名的 Cookie 变量。

Cookie 通常类似：

```text
koa:sess=xxxxxxxx; koa:sess.sig=xxxxxxxx;
```

不要把真实 Cookie 写入代码、README、Issue 或公开提交记录。

# GitHub Actions 部署

## 四、创建仓库

建议创建私有仓库：

```text
GitHub → New repository → Private
```

将本项目压缩包解压后，把以下内容上传到仓库根目录：

```text
.github/
checkin.py
notify.py
logging_config.py
requirements.txt
README.md
LICENSE
.gitignore
```

注意不要把外层目录再套一层。仓库根目录应当直接看到 `checkin.py`。

## 五、配置 GitHub Secrets

进入：

```text
仓库 → Settings
     → Secrets and variables
     → Actions
     → New repository secret
```

按实际使用情况添加：

```text
GLADOS_ROCKS_COOKIES
GLADOS_ROCKS_PLANS
RAILGUN_INFO_COOKIES
RAILGUN_INFO_PLANS
QYWX_KEY
```

只使用一个域名时，另一个域名的两个变量可以不创建。

### 多行 Secret 的输入方法

在 Secret 的值输入框中直接粘贴多行内容：

```text
账号1|Cookie1
账号2|Cookie2
```

也支持用 `&` 分隔，但更建议使用换行：

```text
账号1|Cookie1&账号2|Cookie2
```

## 六、配置企业微信推送

### 方案 A：企业微信群机器人，推荐

1. 打开目标企业微信群；
2. 群设置 → 群机器人 → 添加机器人；
3. 复制 Webhook 地址；
4. Webhook 通常类似：

```text
https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

5. 创建 GitHub Secret：

```text
名称：QYWX_KEY
值：xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

`QYWX_KEY` 既可以只填写 `key`，也可以填写完整 Webhook 地址。

### 方案 B：企业微信自建应用

不使用群机器人时，可创建：

```text
名称：QYWX_AM
值：corpid,corpsecret,touser,agentid
```

示例：

```text
ww1234567890abcdef,应用Secret,HanZineng,1000002
```

其中：

- `corpid`：企业 ID；
- `corpsecret`：自建应用 Secret；
- `touser`：接收人的企业微信账号，发送给全部成员可填 `@all`；
- `agentid`：应用 AgentId。

`QYWX_KEY` 与 `QYWX_AM` 同时配置时，两种渠道都会推送。

## 七、运行 GitHub Actions

进入：

```text
仓库 → Actions → GLaDOS Railgun Check-in → Run workflow
```

先手动运行一次，查看 `Run check-in` 日志。

工作流默认时间：

```text
17 0 * * *
```

GitHub Actions 使用 UTC，对应北京时间每天 `08:17`。修改 `.github/workflows/gladosCheck.yml` 中的 `cron` 即可调整时间。

GitHub 定时任务可能存在几分钟延迟，不适合要求秒级准点的任务。

## 八、GitHub Actions 日志判断

正常加载时会输出：

```text
任务：账号=张三主号，域名=glados.rocks，兑换计划=plan0
任务：账号=李四主号，域名=railgun.info，兑换计划=plan200
```

积分不足时：

```text
未兑换（积分不足：86/200）
```

关闭兑换时：

```text
未兑换（plan0）
```

配置错误时：

```text
兑换计划 'plan300' 无效；仅支持 plan0/plan100/plan200/plan500
```
