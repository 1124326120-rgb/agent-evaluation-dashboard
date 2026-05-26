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

```
cd dashboard
pip install flask pandas plotly requests
python app.py
```

打开 http://127.0.0.1:5000 查看看板。

## LLM 配置

LLM-as-Judge 评分引擎默认使用**模拟评分**（预设分数 + 微小随机波动），可直接运行无需配置。

如需接入真实 LLM API，可通过环境变量配置。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| LLM_API_KEY | (空) | LLM API Key（优先） |
| OPENAI_API_KEY | (空) | 备用的 API Key |
| LLM_MODEL | gpt-4o-mini | 模型名称 |
| LLM_BASE_URL | https://api.openai.com/v1 | 兼容 OpenAI 格式的 API 地址 |
| LLM_TIMEOUT | 30 | 请求超时（秒） |
| LLM_MAX_RETRIES | 3 | 最大重试次数 |
| LLM_RETRY_DELAY | 2 | 重试间隔（秒） |

### 快速配置

1. 复制配置模板：
   ```
   cp .env.example .env
   ```

2. 编辑 .env 文件，填入你的 API Key。

3. 启动时加载环境变量：
   ```
   # Linux / macOS
   export  && python app.py
   ```

   ```
   # Windows PowerShell
   Get-Content .env | Where-Object {  -match "^[^#]" } | ForEach-Object {
     ,  =  -split "=", 2
     Set-Item "env:" 
   }
   python app.py
   ```

4. 或直接在启动前设置：
   ```
   export LLM_API_KEY=sk-your-key-here
   export LLM_MODEL=gpt-4o
   python app.py
   ```

### 工作机制

- 当 LLM_API_KEY 或 OPENAI_API_KEY 有值时，引擎调用真实 LLM API，传入 Agent 客观指标，由 LLM 进行 5 维度语义评分。
- 当 API Key 未配置时，自动降级为模拟评分，确保功能始终可用。
- 支持 OpenAI 兼容格式的 API（包括 Azure OpenAI、Ollama、vLLM 等），只需修改 LLM_BASE_URL。
- 请求包含超时检测（默认 30s）和自动重试（默认最多 3 次，间隔 2s），非 429 类 4xx 错误不重试。

## 对比 Phase 4 MVP 的升级

| 功能 | Phase 4 MVP | Phase 5 全功能 |
|------|-------------|----------------|
| 评分方式 | 关键词匹配 FPSR/RR | LLM-as-Judge 多维度评分 |
| 对比模式 | ❌ | ✅ 选择 Agent 横向对比 |
| 数据导出 | ❌ | ✅ CSV / JSON |
| 时间段筛选 | ❌ | ✅ 自定义 days/start-end |
| 历史对比 | ❌ | ✅ LLM 评分历史追踪 |
| 排名算法 | 纯客观分 | 客观 50% + LLM 50% 综合 |
| LLM 配置 | ❌ 不可配置 | ✅ 环境变量 + .env.example |
| 超时重试 | ❌ | ✅ 可配置超时 + 自动重试 |

## 依赖安装

```
pip install flask pandas plotly requests
```

requests 用于 LLM API 调用；若未安装，评分引擎会自动降级为模拟评分。

