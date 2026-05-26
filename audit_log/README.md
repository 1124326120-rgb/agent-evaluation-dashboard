# Audit Log System

Agent 审计日志系统，基于 CC 审查报告修复了所有已识别问题。

## 修复的审查问题

| 问题 | 状态 | 说明 |
|------|------|------|
| trace_id/action 在 Python 层面后过滤 | ✅ 已修复 | 全部在 SQL WHERE 层面通过 LIKE 过滤，trace tracking 端点使用 dual-condition OR 查询 |
| `datetime.utcnow()` 废弃 | ✅ 已修复 | 全部替换为 `datetime.now(timezone.utc)` |
| 无认证/授权机制 | ✅ 已修复 | HTTP Basic Auth 支持，环境变量开关控制 |
| 跨工作区集成不完整 | ✅ 已修复 | 所有模块整合到 `audit_log/` 目录下 |
| lifetime_manager 未在 API 中集成 | ✅ 已修复 | `audit_api.py` 启动时自动初始化并启动 |

## 架构

```
audit_log/
├── __init__.py                 # 包入口
├── audit_api.py                # FastAPI 服务 (8 端点 + 4 Web 页面)
├── instrumentation.py          # 埋点采集层 (装饰器 + Context + AsyncWriter)
├── models/
│   ├── __init__.py
│   └── event.py                # AuditEvent 数据模型 (18 字段)
├── engine/
│   ├── __init__.py
│   ├── sqlite_engine.py        # SQLite 存储引擎 (按日分表, SQL 层面过滤)
│   └── lifetime_manager.py     # 生命周期管理 (热温冷三层)
├── templates/
│   ├── events.html             # 事件列表页
│   ├── session.html            # 会话时间线页
│   ├── trace.html              # 调用链瀑布图页
│   └── errors.html             # 错误聚合面板页
├── static/                     # 静态文件目录
└── tests/
    ├── __init__.py
    └── test_all.py             # 17 项测试
```

## 启动服务

```bash
cd agent-evaluation-dashboard
python -m audit_log.audit_api
```

服务默认运行在 `http://localhost:8399`。

### 认证

默认认证关闭。如需启用：

```bash
set AUTH_ENABLED=true
set AUTH_USERNAME=admin
set AUTH_PASSWORD=your_password
python -m audit_log.audit_api
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/events` | GET | 多维度查询 |
| `/api/events/{id}` | GET | 单条事件 |
| `/api/sessions/{id}/replay` | GET | 会话时间线 |
| `/api/traces/{id}` | GET | 调用链追踪 |
| `/api/stats/errors` | GET | 错误聚合 |
| `/api/stats/summary` | GET | 统计概览 |
| `/api/export/csv` | GET | CSV 导出 |
| `/api/export/json` | GET | JSON 导出 |
| `/` | GET | Web 界面 |
| `/session/{id}` | GET | 会话页面 |
| `/trace/{id}` | GET | 追踪页面 |
| `/errors` | GET | 错误页面 |

## Web 界面

4 个可视化页面：

- **事件列表** `/` — 多维筛选、分页、导出
- **会话时间线** `/session/{id}` — 完整操作回放
- **调用链瀑布图** `/trace/{id}` — 跨 Agent 追踪
- **错误聚合面板** `/errors` — 按类型/Agent 聚合

## 运行测试

```bash
python -m audit_log.tests.test_all
```
