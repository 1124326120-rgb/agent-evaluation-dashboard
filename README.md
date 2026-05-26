# Phase 5: Agent能力评估看板（全功能版 + LLM-as-Judge）

## 功能清单

1. **Agent 排名展示** — LLM-Judge 综合评分 + 客观指标（TCR/FPSR/ART/TCD/RR）加权排名
2. **LLM 评分雷达图** — 5 维度（任务完成、代码质量、响应速度、架构设计、��析深度）
3. **指标趋势图** — TCR/FPSR/ART/TCD/RR + LLM 评分时间序列，支持自定义时间段
4. **对比模式** — 选择两个 Agent 进行雷达图横向对比
5. **历史对比** — LLM-Judge 评分历史变化追踪
6. **CSV / JSON 导出** — 一键导出评估报告
7. **LLM-as-Judge 评分引擎** — 替代 Phase 1-3 的关键词匹配，基于多维语义评分

## 启动方式

`ash
cd dashboard
pip install flask pandas plotly
python app.py
`

打开 http://127.0.0.1:5000 查看看板。

## 对比 Phase 4 MVP 的升级

| 功能 | Phase 4 MVP | Phase 5 全功能 |
|------|-------------|----------------|
| 评分方式 | 关键词匹配 FPSR/RR | LLM-as-Judge 多维度评分 |
| 对比模式 | ❌ | ✅ 选择 Agent 横向对比 |
| 数据导出 | ❌ | ✅ CSV / JSON |
| 时间段筛选 | ❌ | ✅ 自定义 days/start-end |
| 历史对比 | ❌ | ✅ LLM 评分历史追踪 |
| 排名算法 | 纯客观分 | 客观 50% + LLM 50% 综合 |
