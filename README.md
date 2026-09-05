# LLM 会话临时黑名单

一个 AstrBot 插件。LLM 可以根据当前对话自行判断是否拉黑当前用户，并自由决定拉黑时长；黑名单按对话隔离，不会因为一个群里的记录误伤其他群或私聊。

## 功能

- 向 LLM 注册 `add_dialog_temporary_blacklist` 工具，拉黑分钟数由 LLM 自主决定。
- 被拉黑用户在对应对话中的 LLM 请求会被拦截，其他对话不受影响。
- `/拉黑`：查看当前对话仍在生效的拉黑用户、剩余时长和解禁时间。
- `/全局拉黑`：查看全部对话的拉黑记录，仅 AstrBot 管理员可用。
- `/出狱 <@用户>`：解除被艾特用户在当前对话中的拉黑，仅 AstrBot 管理员可用。
- 可配置被拉黑用户再次唤醒 LLM 时是否发送提示，并可自定义提示内容。
- 使用 AstrBot 插件 KV 存储保存记录，插件重载或 AstrBot 重启后仍可继续计时。
- AstrBot 管理员不会被 LLM 拉黑，也不会受到已有记录的拦截。

## 安装

在 AstrBot 插件管理页选择“从链接安装”，填写：

```text
https://github.com/yun474/astrbot_plugin_llm_blacklist
```

插件需要 AstrBot `4.9.2` 或更高版本，不需要安装第三方 Python 依赖。

## 使用

插件安装后会自动向当前 Agent 提供工具。可以在人格或系统提示词中补充触发规则，例如：

```text
当你认为当前用户的行为需要暂时禁止其继续对话时，调用
add_dialog_temporary_blacklist，并根据情节自行决定 duration_minutes。
```

查询当前对话：

```text
/拉黑
```

AstrBot 管理员查询全部对话：

```text
/全局拉黑
```

AstrBot 管理员解除当前对话中指定用户的拉黑：

```text
/出狱 @用户
```

`/出狱` 会优先读取消息中的真实艾特组件，并使用其中的用户 ID 或 openid 精确匹配当前对话的拉黑记录。对于未生成艾特组件的官方机器人消息，同时兼容 `<@openid>` 和 `<@!openid>` 文本格式；只输入裸 ID 不会解除拉黑。

## 配置

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `show_blocked_message` | 被拉黑用户再次唤醒 LLM 时是否发送提示；关闭后仍会拦截请求 | `true` |
| `blocked_message_template` | 自定义提示内容，支持 `{remaining_time}` 剩余时间占位符 | `你在当前对话中仍处于临时拉黑状态，剩余 {remaining_time}。发送 /拉黑 可查看详情。` |

重复拉黑同一用户时，新记录会覆盖旧记录，并从工具调用时重新计时。所有解禁时间均按 AstrBot 所在机器的本地时区显示。

## 设计说明

本插件受 [astrbot_plugin_LLMTempBan](https://github.com/zgojin/astrbot_plugin_LLMTempBan) 启发，代码为独立实现。与其主要区别是：

- LLM 必须明确给出时长，不使用固定默认时长；
- 黑名单以 AstrBot 统一会话标识隔离；
- 提供当前对话和管理员全局查询命令；
- 使用 AstrBot 插件存储持久化未过期记录；
- 管理员权限直接沿用 AstrBot 自带权限系统。

## 许可证

[MIT](LICENSE)
