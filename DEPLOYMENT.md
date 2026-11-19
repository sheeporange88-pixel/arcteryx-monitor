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
5. 当前已按用户要求启用每10分钟的计划。首次部署先手动测试收件人和抓取结果。

停止时在 Actions 页面选择主监控 workflow 的 Disable workflow；单次 Telegram Setup Test 没有定时任务。
如果有多个用户给机器人发过消息，为避免发错人，自动配置会停止。
这时在 Secrets 中另设 `TELEGRAM_CHAT_ID`，值为自己的明确接收会话 ID。

## 检查内容

- https://arcteryx.com/de/de/c/mens
- https://arcteryx.com/de/de/c/mens/new-arrivals
- https://outlet.arcteryx.com/de/de/c/mens
- 官网和 Outlet 首页明确出现的 Black Friday / Black Week / Cyber Monday 文字。

每10分钟计划检查一次；GitHub 调度可能延迟，不承诺整点、严格间隔或活动公告前必定检出。
所有已列出的商品逐次读取SKU详情页的每个颜色尺码价格；总览页价格不变也检查。
报告保存详情价证据和总览价格范围，标记两者不一致；不把总览价格当作触发检查的条件。
公开详情页中的JSON价格可核验；另一个动态变体价格接口当前返回401，未绕过授权，
因此不声称能读到比公开详情页更早的未公开后台价格。
每轮保存中文报告、CSV和JSON。发现新品、价格或折扣变化时，该轮核实后立即推送；
无变化只在柏林每小时55分这一轮推送一次，不在其他检查轮次重复发消息。
首次基准建立、读取故障首次出现或恢复也会通知；持续相同故障留到小时报告说明。
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

## 七款独立 SKU 价格通知

`focused_skus.json` 精确记录用户指定的7个商品ID及规范链接（3款女士、4款男士）。
`.github/workflows/focused-skus.yml` 每10分钟独立运行，不依赖男士总览页是否更新，
也不等待全站抓取任务完成。每轮对7个SKU详情页的全部颜色尺码价格逐项读取。
有价格变化的款式在该轮单独推送；无变化的款式只在每小时55分这一轮各发一条。
每款消息列原价、当前详情价、较上次变化及核实时间。
颜色、尺码存在价格差异时明确分组，不把最低价当作所有颜色尺码的价格。
读取失败时单独报告该款读取失败，标注上次核实价格，不冒充实时价格。

独立基准、发送回执放在data-focused缓存；首次复用既有机器人私聊接收者。
发送间隔至少1.1秒，7款各一条，不合并为总览消息；API接收不等于设备推送送达。
每轮Actions的Artifacts包含focused-sku-prices报表，提供JSON、CSV及文字记录。
原全站任务与这7款任务分别调度；如需停止其一，在Actions中禁用对应workflow。


## 德国夜间暂停

所有商品监控在 Europe/Berlin 当地 00:00–07:00 暂停抓取和 Telegram 发送；
07:00 起到 23:59 按每10分钟计划运行。时区库自动处理德国冬令时/夏令时。
云端调度计划为柏林每小时05、15、25、35、45、55分，运行时再以实际柏林时间判断，
因此排队/延迟到夜间的运行也会跳过。Actions 中可能看到成功的时间检查和 skipped 的抓取任务，
这不代表抓取失败。入口和每次网站/Telegram请求还会再次判断，防止任务跨午夜继续发起请求。
手动测试也遵循相同暂停时间。历史基准和待发回执保留。


## 调度未触发的诊断

计划时段内没有新的Actions运行记录，与抓取失败或夜间跳过不同。
2026-11-19上午发现两个启用的workflow在07:00后没有定时触发；
具体GitHub内部原因无回执。已重新提交错峰计划：全站每小时03/13/23/33/43/53分，
独立SKU每小时07/17/27/37/47/57分（cron采用UTC，柏林运行时间门禁继续生效）。
修改各自workflow文件会触发一次补跑。补跑成功不等于后续cron持续触发已得到验证。
GitHub定时可能延迟或丢弃；严格时效需求应使用有可靠时钟的常开云服务调度，
不能把仅写入cron配置视作已保证每10分钟送达。


## 当前使用独立时钟

GitHub原生schedule未持续产生运行记录，错峰重登记后仍无自动运行。
因此已移除两个监控workflow的原生cron，改由ChatGPT云端定时任务通过已连接的GitHub工具，
每10分钟检查并重跑各自最近运行中的active_hours任务；GitHub会连同依赖的抓取发送任务重跑。
此调用方式已经实测，7款消息的发送成功。执行时仍重新读取Europe/Berlin实际时间。
每个目标在排队、执行中或本计划时隙已处理时，定时器跳过该目标，以免重复推送；
独立SKU目标优先处理，全站目标不阻塞SKU目标。
凭据继续只保留在GitHub Secrets，ChatGPT定时任务不读取token。
程序或workflow变更可触发一次部署测试；重复执行的报表文件名带run_attempt，保留每次记录。
两个任务的完整基准和消息回执继续保存在各自GitHub缓存，不混用价格基准。
停止持续运行时，需暂停名为“始祖鸟10分钟推送调度”的ChatGPT任务。
云端任务与GitHub执行仍有排队耗时，不保证秒级准点；不能仅凭任务创建就声称下一次已执行。

## 通知频率（2026-11-19更新）

检查始终每10分钟进行，未推送的检查也保存最新报表与价格基准。
通知门禁在GitHub runner内执行：有变化本轮立即入队；无变化仅55分轮入队，
相同小时和SKU的小时报告不会重复入队。以抓取开始时间所在的05/15/25/35/45/55分时隙判断，
例如55分开始、下小时完成仍是55分报告。云端或GitHub排队可能使实际接收晚于55分。
“立即”指每10分钟检查发现并核实后发送，不能保证网站刚改价的瞬间就发现。
每个数据目录的notification-state.json随缓存持久保存去重状态；发送失败的outbox继续重试。
不完整全站来源的完整基准仍保留；同一旧基准反复产生相同变化时，不重复即时提醒。

验证命令：
```bash
python -m unittest test_monitor_de.py test_monitor_focused.py test_monitoring_hours.py test_notification_policy.py
```

## 访问拒绝处理（2026-11-19）

全站抓取仅并发2个详情页，同一站点的请求启动间隔至少0.5秒，并使用明确的监控程序标识与页面/JSON接受类型。
出现HTTP 403、406或429时，不反复重试；暂停本轮对该站的后续请求，另一站点和Telegram仍可处理。
Retry-After要求的等待期限随各自监控缓存保存，下一轮继续遵守。下一轮没有冷却限制时可再尝试读取。
JSON报告的request_health保存响应状态、公开页面路径和Content-Type/Server/Retry-After这三个响应标头及少量拒绝类型标记，
不会保存完整拒绝页面、Cookie或Telegram密钥。请求失败仍保留完整基准，不把无数据解释为没有折扣。
零商品核验时中文报告明确写“价格与折扣无法确认”；首页读取失败时也不会声称没有检出黑五文字。
这些调整用于减少请求压力和诊断失败，不能保证网站始终允许自动访问。

公开商品抓取与Telegram验证/发送现在分开执行。抓取步骤不注入Telegram凭据；仅后续验证与发送步骤使用Secrets。
抓取日志打印经过筛选的request_health诊断信息；实际发送步骤另报API接收条数和剩余待发数，均不代表手机已显示。
