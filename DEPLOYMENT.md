# 电脑端：GitHub Actions + Telegram

项目参考：https://github.com/TongTong0828/arcteryx-monitor ，上游版本
`3ef453ad25b2c3dcda0f124658d0ffa8bbbda53a`。
`monitor_de.py` 是为德国官网、德国 Outlet 编写的适配，使用公开分页接口和商品详情 JSON。
无需安装第三方 Python 包；不运行上游浏览器或部署脚本。

## 第一次接入

1. 在 BotFather 撤销已经贴在聊天中的旧 token，取得新 token。
2. 给自己的机器人发送 `/start`。配置会自动识别唯一的私聊接收者。
3. 仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**：
   Name 为 `TELEGRAM_BOT_TOKEN`，Secret 填新 token。
   不要把 token 放在文件、Issue、聊天或日志里。
4. 打开 **Actions → Arc'teryx Germany Telegram Monitor → Run workflow**，选择 main 运行。
5. 确认 Telegram 收到首轮基准报告，再打开定时开关：
   **Settings → Secrets and variables → Actions → Variables → New repository variable**，
   Name 为 `MONITOR_ENABLED`，Value 为 `true`。

未设置 MONITOR_ENABLED=true 时，定时任务会跳过，不会自动发送；手动运行仍可测试。
停止时将 MONITOR_ENABLED 改为 false，或在 Actions 中 Disable workflow。
如果有多个用户给机器人发过消息，为避免发错人，自动配置会停止。
这时在 Secrets 中另设 `TELEGRAM_CHAT_ID`，值为自己的明确接收会话 ID。

## 检查内容

- https://arcteryx.com/de/de/c/mens
- https://arcteryx.com/de/de/c/mens/new-arrivals
- https://outlet.arcteryx.com/de/de/c/mens
- 官网和 Outlet 首页明确出现的 Black Friday / Black Week / Cyber Monday 文字。

每小时计划检查一次；GitHub 调度可能延迟，不承诺整点或严格间隔。
每轮发中文报告，即使没有变化；报告包括覆盖数量、价格变化和失败情况。
普通 Outlet 折扣不标成黑五专享；购物车优惠码、会员和结算价格未核验。
首次只建立基准，不把现有产品当新品。分别保存完整来源基准，失败来源不覆盖旧基准。
不会把抓取失败当作下架。Telegram API 接收成功不代表手机推送已显示。

## 历史和下载

在每轮 Actions 页面查看 Summary；Artifacts 下载 `arcteryx-de-report-*`，
含中文报告、每款每颜色尺码价格 CSV 和 JSON 抓取记录。
不在公开源码中保存 token、Telegram 接收者信息或通知回执。
缓存保存来源基准、私聊接收者和逐条发送回执；不把 token 写入缓存。
GitHub 缓存会被淘汰，因此不保证永久历史；缓存丢失会重新建立基准，不误报为新品。
需要永久保留的报告请从 Artifacts 下载，或另配置自己的持久存储。
公开仓库的 Actions 日志及价格报表可能公开可见；私人部署可将仓库设为 Private。

## 本地验证

```bash
python -m unittest test_monitor_de.py
python monitor_de.py
```

本地只生成报告；加 --notify 才发送。凭据来自运行环境，自动识别收件人时需要先执行
`python monitor_de.py --telegram-configure`。不要同时运行两个写入同一目录的实例。
