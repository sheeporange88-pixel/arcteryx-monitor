# 常开服务器部署准备（尚未部署）

需要一台已有的Linux服务器，Python 3.9以上，支持systemd。
独立服务器也可能被站点拒绝，必须先在目标机器实测；不承诺换IP即可解决。
未配置代理、验证码绕过或登录接口，也不自动购买资源。

## 先测试，不发送消息

在目标服务器克隆仓库后执行：

```bash
python3 server_runner.py --preflight
```

这会顺序核验7款SKU的详情价格、3个目录的全部分页及每目录1款详情样本。
不读取Telegram凭据、不发送消息、不写价格基准。它只是访问预检，不能冒充完整全站扫描。
至少在不同时间重复通过后再部署；完整监控仍以每轮报告complete和覆盖数量为准。

## 凭据与价格基准

代码放在`/opt/arcteryx-monitor`；创建同名系统用户和组`arcteryx-monitor`。
仅在目标服务器的`/etc/arcteryx-monitor.env`中保存自己的配置，权限0600、root所有：

```dotenv
TELEGRAM_BOT_TOKEN=在服务器本地填入有效值
# TELEGRAM_CHAT_ID=可选；唯一私聊也可以通过机器人/start自动识别
```

不要把实际token贴进聊天、源码或日志。GitHub Secrets不能直接读取用于迁移。
运行数据持久保存在`/var/lib/arcteryx-monitor/data-focused/`及`data-de/`。
迁移前保留GitHub原缓存，分别转移focused-state.json、state.json以及待发outbox和通知去重状态；
接收者文件属于私有数据，不放进公开源码。若无法转移基准，新服务器首次仅报告建立基准，
不能将原有商品都报成新品，也无法声称覆盖迁移期间的价格变化。

## 启用时钟（预检通过、完成迁移后）

将本目录service/timer安装到`/etc/systemd/system/`，执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now arcteryx-monitor.timer
systemctl list-timers arcteryx-monitor.timer
sudo journalctl -u arcteryx-monitor.service -n 80
```

柏林07:05至23:55，每小时05、15、25、35、45、55分运行；脚本对每次网络请求仍执行夜间门禁。
7款优先检查，随后全站；变化核实后推送，无变化仅55分轮推送，7款分别发送。
运行重叠时跳过重复启动。Telegram发送失败保留outbox，后续重试；API接收不等于手机显示。

目标服务器完整实跑和Telegram验证完成后，暂停ChatGPT旧调度，避免两处重复推送。
没有目标服务器、访问预检和实际运行证据之前，不称服务器监控已启用或问题已解决。
