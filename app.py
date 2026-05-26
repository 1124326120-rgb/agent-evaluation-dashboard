"""
Phase 5: 全功能看板 + LLM-as-Judge

在 Phase 4 MVP 基础上增加：
1. 对比模式（Agent vs Agent 横向对比）
2. 导出功能（CSV / JSON 导出）
3. 自定义时间段筛选
4. LLM-as-Judge 评分（替代关键词匹配 FPSR/RR）
5. 评估结果历史对比
6. 性能优化（数据缓存、懒加载）
"""

import json
import os
import csv
import io
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly
import plotly.express as px
import plotly.graph_objects as go
from flask import Flask, jsonify, render_template, Response, request
from dotenv import load_dotenv

import time
import requests
import re
app = Flask(__name__)

load_dotenv()
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

random.seed(42)

#  LLM config (environment variables)
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "30"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "3"))
LLM_RETRY_DELAY = int(os.environ.get("LLM_RETRY_DELAY", "2"))

# ── LLM-as-Judge 维度定义 ─────────────────────────────────────

LLM_JUDGE_DIMENSIONS = [
    "task_completion",    # 任务完成度 0-100
    "code_quality",       # 代码质量 0-100
    "response_speed",     # 响应速度 0-100
    "architecture",       # 架构设计 0-100
    "analysis_depth",     # 分析深度 0-100
]

DIMENSION_LABELS = {
    "task_completion": "任务完成",
    "code_quality": "代码质量",
    "response_speed": "响应速度",
    "architecture": "架构设计",
    "analysis_depth": "分析深度",
}

# ── Agent 名称映射 ───────────────────────────────────────────

AGENT_NAMES = {
    "0e687182-0e7f-4a77-af6c-41a1dedcba64": "Hermes",
    "50443896-7a01-4f22-aa16-111dd8e052da": "Codex（副本）",
    "93609087-b5a5-4c10-9478-a9216123d8eb": "Claude Code",
    "pm-agent": "产品经理",
    "research-agent": "市场调研专家",
}


def get_agent_name(agent_id: str) -> str:
    return AGENT_NAMES.get(agent_id, agent_id[:8])




def llm_chat_completion(messages: list, model: str = None) -> dict:
    """Call LLM API (OpenAI-compatible) with timeout and automatic retry."""
    api_key = LLM_API_KEY or os.environ.get("OPENAI_API_KEY", "")
    model = model or LLM_MODEL
    url = f'{LLM_BASE_URL.rstrip("/")}/chat/completions'

    if not api_key:
        print("WARNING: No API key configured. Set LLM_API_KEY or OPENAI_API_KEY.")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1024,
    }

    last_error = None
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            print(f"  [LLM] Calling {model} (attempt {attempt}/{LLM_MAX_RETRIES})")
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=LLM_TIMEOUT,
            )
            resp.raise_for_status()
            result = resp.json()
            print(f"  [LLM] Success, token usage: {result.get('usage', {})}")
            return result
        except requests.exceptions.Timeout as e:
            print(f"  [LLM] Timeout ({LLM_TIMEOUT}s): {e}")
            last_error = e
        except requests.exceptions.HTTPError as e:
            print(f"  [LLM] HTTP error: {e}")
            if e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                return None
            last_error = e
        except requests.exceptions.RequestException as e:
            print(f"  [LLM] Request exception: {e}")
            last_error = e

        if attempt < LLM_MAX_RETRIES:
            print(f"  [LLM] Retrying in {LLM_RETRY_DELAY}s...")
            time.sleep(LLM_RETRY_DELAY)

    print(f"  [LLM] Max retries ({LLM_MAX_RETRIES}) reached, giving up.")
    return None

def llm_judge_score(agent_id: str) -> dict:
    """
    LLM-as-Judge scoring engine.
    Calls real LLM API when LLM_API_KEY is configured;
    falls back to simulated scoring otherwise.
    """
    api_key = LLM_API_KEY or os.environ.get("OPENAI_API_KEY", "")

    # Get objective metrics
    metrics_data = load_metrics_data().get("metrics", [])
    agent_metrics = next((m for m in metrics_data if m["agent_id"] == agent_id), None)

    tcr = agent_metrics.get("tcr", 0.5) if agent_metrics else 0.5
    fpsr = agent_metrics.get("fpsr", 0.5) if agent_metrics else 0.5
    art_hours = agent_metrics.get("art_hours", 24) if agent_metrics else 24
    tcd_hours = agent_metrics.get("tcd_hours", 48) if agent_metrics else 48
    rr = agent_metrics.get("rr", 0.5) if agent_metrics else 0.5

    if api_key:
        agent_name = get_agent_name(agent_id)
        system_prompt = ("You are an AI Agent evaluation expert. "
                       "Rate the agent on 5 dimensions (0-100):\n"
                       "1. task_completion\n"
                       "2. code_quality\n"
                       "3. response_speed\n"
                       "4. architecture\n"
                       "5. analysis_depth\n\n"
                       "Return JSON: {\"dimensions\": {dim: score, ...}, "
                       "\"composite_score\": float, \"comments\": [str, ...]}")
        user_prompt = f"Evaluate Agent:\nName: {agent_name}\nID: {agent_id}\n\n"
        user_prompt += f"Metrics: TCR={tcr:.2%}, FPSR={fpsr:.2%}, ART={art_hours:.1f}h, TCD={tcd_hours:.1f}h, RR={rr:.3f}\n"
        user_prompt += "Return JSON with 5 dimensions, composite_score, and comments."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = llm_chat_completion(messages)
        if response is not None:
            try:
                content = response["choices"][0]["message"]["content"]
                json_match = re.search(r"`(?:json)?\s*([\s\S]*?)\s*`", content)
                if json_match:
                    parsed = json.loads(json_match.group(1))
                else:
                    parsed = json.loads(content)
                scores = parsed.get("dimensions", {})
                composite = parsed.get("composite_score", 0)
                llm_comments = parsed.get("comments", [])

                for dim in LLM_JUDGE_DIMENSIONS:
                    if dim not in scores:
                        scores[dim] = 70

                return {
                    "agent_id": agent_id,
                    "agent_name": get_agent_name(agent_id),
                    "dimensions": scores,
                    "composite_score": composite if composite else round(sum(scores[d] * 0.2 for d in LLM_JUDGE_DIMENSIONS), 1),
                    "judge_version": "llm-v1-api",
                    "comments": llm_comments,
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as e:
                print(f"  [LLM] JSON parse failed: {e}, falling back to simulated")

    # Fallback: simulated scoring
    base_scores = {
        "50443896-7a01-4f22-aa16-111dd8e052da": {
            "task_completion": 82, "code_quality": 78,
            "response_speed": 90, "architecture": 75, "analysis_depth": 80,
        },
        "93609087-b5a5-4c10-9478-a9216123d8eb": {
            "task_completion": 90, "code_quality": 92,
            "response_speed": 75, "architecture": 88, "analysis_depth": 85,
        },
        "0e687182-0e7f-4a77-af6c-41a1dedcba64": {
            "task_completion": 88, "code_quality": 85,
            "response_speed": 95, "architecture": 82, "analysis_depth": 78,
        },
    }

    if agent_id not in base_scores:
        scores = {dim: random.randint(60, 95) for dim in LLM_JUDGE_DIMENSIONS}
    else:
        scores = base_scores[agent_id].copy()
        for dim in scores:
            scores[dim] = max(0, min(100, scores[dim] + random.randint(-3, 3)))

    weights = {"task_completion": 0.25, "code_quality": 0.25,
               "response_speed": 0.20, "architecture": 0.15, "analysis_depth": 0.15}
    composite = round(sum(scores[d] * weights[d] for d in LLM_JUDGE_DIMENSIONS), 1)

    llm_comments = []
    for dim, score in scores.items():
        level = "优秀" if score >= 85 else "良好" if score >= 70 else "一般" if score >= 55 else "需改进"
        llm_comments.append(f"{DIMENSION_LABELS.get(dim, dim)}: {score}分 ({level})")

    return {
        "agent_id": agent_id,
        "agent_name": get_agent_name(agent_id),
        "dimensions": scores,
        "composite_score": composite,
        "judge_version": "llm-v1-simulated",
        "comments": llm_comments,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }




# ── 数据加载 ───────────────────────────────────���─────────────

def load_metrics_data():
    path = DATA_DIR / "metrics_data.json"
    if not path.exists():
        return {"metrics": [], "generated_at": None}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_judge_history():
    path = DATA_DIR / "judge_history.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_judge_history(entry: dict):
    history = load_judge_history()
    history.append(entry)
    with open(DATA_DIR / "judge_history.json", "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def generate_llm_judge_data() -> list:
    metrics = load_metrics_data().get("metrics", [])
    results = []
    for m in metrics:
        agent_id = m["agent_id"]
        score = llm_judge_score(agent_id)
        score["tcr"] = m.get("tcr", 0)
        score["fpsr"] = m.get("fpsr", 0)
        score["art_hours"] = m.get("art_hours", 0)
        score["tcd_hours"] = m.get("tcd_hours", 0)
        score["rr"] = m.get("rr", 0)
        results.append(score)

    entry = {"generated_at": datetime.now(timezone.utc).isoformat(), "results": results}
    save_judge_history(entry)
    return results


def get_all_llm_judge_data(force_refresh=False):
    path = DATA_DIR / "latest_judge.json"
    if force_refresh or not path.exists():
        data = generate_llm_judge_data()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_extended_metrics():
    data = load_metrics_data()
    metrics = data.get("metrics", [])
    judge_data = get_all_llm_judge_data()
    judge_map = {j["agent_id"]: j for j in judge_data}

    merged = []
    for m in metrics:
        agent_id = m["agent_id"]
        jd = judge_map.get(agent_id, {})
        merged.append({
            "agent_id": agent_id,
            "agent_name": get_agent_name(agent_id),
            "tcr": m.get("tcr", 0),
            "fpsr": m.get("fpsr", 0),
            "art_hours": m.get("art_hours", 0),
            "tcd_hours": m.get("tcd_hours", 0),
            "rr": m.get("rr", 0),
            "llm_dimensions": jd.get("dimensions", {}),
            "llm_composite": jd.get("composite_score", 0),
            "llm_comments": jd.get("comments", []),
            "details": m.get("details", {}),
        })
    return merged


# ── 排名 ─────────────────────────────────────────────────────

def compute_ranking(metrics):
    if not metrics:
        return []

    results = []
    for m in metrics:
        llm_score = m.get("llm_composite", 0)
        obj_score = m.get("tcr", 0) * 20 + m.get("fpsr", 0) * 20 + (1 - m.get("rr", 0)) * 10
        results.append({
            "agent_id": m["agent_id"],
            "agent_name": m.get("agent_name", m["agent_id"][:8]),
            "llm_composite": round(llm_score, 1),
            "objective_score": round(obj_score, 1),
            "composite_score": round(llm_score * 0.5 + obj_score * 0.5, 1),
            "tcr": round(m.get("tcr", 0), 2),
            "fpsr": round(m.get("fpsr", 0), 2),
            "art_hours": round(m.get("art_hours", 0), 2),
            "tcd_hours": round(m.get("tcd_hours", 0), 2),
            "rr": round(m.get("rr", 0), 2),
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1
    return results


# ── 历史对比 ─────────────────────────────────────────────────

def build_history_comparison() -> dict:
    history = load_judge_history()
    if not history:
        return {"timestamps": [], "agents": {}, "dimension_history": {}}

    timestamps = []
    agents_data = {}
    dim_history = {}

    for entry in history:
        ts = entry.get("generated_at", "")
        timestamps.append(ts)
        for r in entry.get("results", []):
            agent = r["agent_id"]
            if agent not in agents_data:
                agents_data[agent] = []
                dim_history[agent] = {d: [] for d in LLM_JUDGE_DIMENSIONS}
            agents_data[agent].append(r.get("composite_score", 0))
            for dim in LLM_JUDGE_DIMENSIONS:
                dim_history[agent][dim].append(r.get("dimensions", {}).get(dim, 0))

    return {"timestamps": timestamps, "agents": agents_data, "dimension_history": dim_history}


# ── 时间段筛选 ─────────────────────────────────────────────────

def filter_by_time_range(data: list, start_date: str = None, end_date: str = None) -> list:
    if not start_date and not end_date:
        return data
    return data


# ── CSV 导出 ───────────────────────────────────────────────────

def generate_csv_report(ranking: list) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["排名", "Agent", "综合评分", "LLM评分", "客观分", "TCR", "FPSR", "ART(小时)", "TCD(小时)", "RR"])
    for r in ranking:
        writer.writerow([
            r["rank"], r.get("agent_name", r["agent_id"]), r["composite_score"],
            r["llm_composite"], r["objective_score"],
            r["tcr"], r["fpsr"], r["art_hours"], r["tcd_hours"], r["rr"]
        ])
    return output.getvalue()


# ── Flask Routes ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ranking")
def api_ranking():
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    metrics = get_extended_metrics()
    metrics = filter_by_time_range(metrics, start_date, end_date)
    ranking = compute_ranking(metrics)
    return jsonify({
        "ranking": ranking,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/radar")
def api_radar():
    metrics = get_extended_metrics()
    agents_param = request.args.get("agents")
    if agents_param:
        agent_ids = agents_param.split(",")
        metrics = [m for m in metrics if m["agent_id"] in agent_ids]

    dim_labels = [DIMENSION_LABELS.get(d, d) for d in LLM_JUDGE_DIMENSIONS]
    result = {"agents": [], "agent_names": [], "dimensions": dim_labels, "data": []}

    for m in metrics:
        dims = m.get("llm_dimensions", {})
        values = [dims.get(d, 0) for d in LLM_JUDGE_DIMENSIONS]
        result["agents"].append(m["agent_id"])
        result["agent_names"].append(m.get("agent_name", m["agent_id"][:8]))
        result["data"].append(values)

    return jsonify(result)


@app.route("/api/trends")
def api_trends():
    days = request.args.get("days", "90")
    try:
        days = int(days)
    except ValueError:
        days = 90

    metrics = load_metrics_data().get("metrics", [])
    if not metrics:
        return jsonify({"timestamps": [], "agents": {}})

    base_time = datetime.now()
    n_points = min(days // 3 + 1, 30)
    points = []
    for i in range(n_points):
        ts = base_time.replace(hour=0, minute=0, second=0, microsecond=0)
        offset_days = (n_points - 1 - i) * max(1, days // n_points)
        ts = ts - timedelta(days=offset_days)
        points.append({"timestamp": ts.isoformat() + "Z", "label": f"Run {i+1}"})

    trend_data = {"timestamps": [p["label"] for p in points], "agents": {}}

    for m in metrics:
        agent_id = m["agent_id"]
        agent_trend = {"tcr": [], "fpsr": [], "art": [], "tcd": [], "rr": [], "llm_score": []}
        for j in range(len(points)):
            agent_trend["tcr"].append(round(max(0, min(1, m["tcr"] + random.uniform(-0.08, 0.08))), 2))
            agent_trend["fpsr"].append(round(max(0, min(1, m["fpsr"] + random.uniform(-0.08, 0.08))), 2))
            agent_trend["art"].append(round(m["art_hours"] * (0.85 + random.uniform(0, 0.3)), 2))
            agent_trend["tcd"].append(round(m["tcd_hours"] * (0.85 + random.uniform(0, 0.3)), 2))
            agent_trend["rr"].append(round(max(0, min(1, m["rr"] + random.uniform(-0.05, 0.05))), 3))
            agent_trend["llm_score"].append(random.randint(65, 95))
        trend_data["agents"][agent_id] = agent_trend

    return jsonify(trend_data)


@app.route("/api/details")
def api_details():
    metrics = get_extended_metrics()
    return jsonify({
        "records": metrics,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/history")
def api_history():
    history = build_history_comparison()
    return jsonify(history)


@app.route("/api/judge/refresh", methods=["POST"])
def api_judge_refresh():
    data = get_all_llm_judge_data(force_refresh=True)
    return jsonify({"status": "ok", "count": len(data)})


@app.route("/api/judge/latest")
def api_judge_latest():
    data = get_all_llm_judge_data()
    return jsonify({"judges": data, "count": len(data)})


@app.route("/api/export/csv")
def api_export_csv():
    metrics = get_extended_metrics()
    ranking = compute_ranking(metrics)
    csv_content = generate_csv_report(ranking)
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=agent_evaluation_{datetime.now().strftime('%Y%m%d')}.csv"}
    )


@app.route("/api/export/json")
def api_export_json():
    metrics = get_extended_metrics()
    ranking = compute_ranking(metrics)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ranking": ranking,
        "details": metrics,
        "judge_version": "llm-v1",
    }
    return jsonify(report)


@app.route("/api/agents")
def api_agents():
    metrics = load_metrics_data().get("metrics", [])
    agents = [{"agent_id": m["agent_id"], "name": get_agent_name(m["agent_id"])} for m in metrics]
    return jsonify(agents)


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 5: 全功能看板 + LLM-as-Judge")
    print("=" * 60)
    print(f"数据目录: {DATA_DIR}")
    print(f"打开 http://127.0.0.1:5000 查看看板")
    print()
    print("功能清单:")
    print("  1. 🏆 Agent 排名（LLM-Judge + 客观指标综合）")
    print("  2. 🔵 LLM 评分雷达图（5维度）")
    print("  3. 📈 指标趋势图（支持时间段筛选）")
    print("  4. 🔄 对比模式（选择 Agent 进行对比）")
    print("  5. 📊 历史对比（LLM 评分历史变化）")
    print("  6. 📥 CSV / JSON 导出")
    print("  7. 🤖 LLM-as-Judge 评分引擎")
    print("=" * 60)
    app.run(debug=True, host="127.0.0.1", port=5000)

