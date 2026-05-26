# Phase 5: Agent能力评估看板（全功能版 + LLM-as-Judge）

## 功能清单

1. **Agent 排名展示** — LLM-Judge 综合评分 + 客观指标（TCR/FPSR/ART/TCD/RR）加权排名
2. **LLM 评分雷达图** — 5 维度（任务完成、代码质量、响应速度、架构设计、分析深度）
3. **指标趋势图** — TCR/FPSR/ART/TCD/RR + LLM 评分时间序列，支持自定义时间段
4. **对比模式** — 选择两个 Agent 进行雷达图横向对比
5. **历史对比** — LLM-Judge 评分历史变化追踪
6. **CSV / JSON 导出** — 一键导出评估报告
7. **LLM-as-Judge 评分引擎** — 替代 Phase 1-3 的关键词匹配，基于多维语义评分

## 启动方式

```bash
cd dashboard
pip install flask pandas plotly
python app.py
```

打开 http://127.0.0.1:5000 查看看板。

## 鉴权配置

默认情况下，看板**无鉴权**，适合内网直接使用。

### 启用 HTTP Basic Auth

通过环境变量控制鉴权开关，默认不启用，向后兼容：

```bash
# Linux / macOS
export AUTH_ENABLED=true
export AUTH_USERNAME=admin
export AUTH_PASSWORD=your-password
python app.py
```

```powershell
# Windows PowerShell
$env:AUTH_ENABLED='true'
$env:AUTH_USERNAME='admin'
$env:AUTH_PASSWORD='your-password'
python app.py
```

### 环境变量说明

| 变量 | 默认值 | 说明 |
|------|--------|------|
| AUTH_ENABLED | (空) | 设为 `true` / `1` / `yes` 启用 Basic Auth |
| AUTH_USERNAME | admin | 登录用户名 |
| AUTH_PASSWORD | admin123 | 登录密码（建议修改为强密码） |

启用后访问看板任一页面会弹出浏览器 Basic Auth 登录框，验证通过后方可查看。

## 使用场景

- **内部团队能力评估** — 用于研发团队内部跟踪 Agent 能力变化
- **Agent 选型对比** — 横向对比不同 Agent 的评分表现
- **持续跟踪改进** — 通过历史对比观察 Agent 能力提升趋势

## 部署建议

- **内网直接使用** — 无需鉴权，运行 app.py 即可
- **外网访问** — 建议启用 AUTH_ENABLED=true，并设置复杂的 AUTH_PASSWORD
- **Nginx 反向代理** — 可在 Nginx 层面增加额外的身份验证或 HTTPS
- **HTTPS** — 生产环境建议使用反向代理提供 HTTPS 加密传输
- **强密码** — admin123 为演示默认密码，生产环境务必修改

## 对比 Phase 4 MVP 的升级

| 功能 | Phase 4 MVP | Phase 5 全功能 |
|------|-------------|----------------|
| 评分方式 | 关键词匹配 FPSR/RR | LLM-as-Judge 多维度评分 |
| 对比模式 | ❌ | ✅ 选择 Agent 横向对比 |
| 数据导出 | ❌ | ✅ CSV / JSON |
| 时间段筛选 | ❌ | ✅ 自定义 days/start-end |
| 历史对比 | ❌ | ✅ LLM 评分历史追踪 |
| 排名算法 | 纯客观分 | 客观 50% + LLM 50% 综合 |
