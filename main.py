"""
小工具生成器 — FastAPI 后端入口

核心职责：
  1. POST /api/generate  接收 prompt，返回一段可直接在 iframe 中渲染的单文件 HTML
  2. GET  /              返回 static/index.html 前端页面

设计原则：
  - generate_app(prompt) -> str 独立封装，便于替换为真实大模型调用
  - 所有密钥 / 配置从环境变量读取，代码不含任何真实敏感信息
  - Mock 模式：无 API Key 或调用失败时，自动返回 3 个预置示例 HTML
"""

import os
import re
import json
import random
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# 加载 .env（若存在）
load_dotenv()

# ---------------------------------------------------------------------------
# 配置读取 —— 全部从环境变量取，代码不含硬编码密钥
# ---------------------------------------------------------------------------
PORT = int(os.environ.get("PORT", 8000))


def _looks_like_placeholder(value: str) -> bool:
    """把 .env.example 里的占位符字符串也视为"未配置"，避免误触发真实 API 调用。"""
    if not value:
        return True
    lowered = value.lower()
    # 典型占位特征：含 your / placeholder / example / xxx-here / sk-your 等
    tokens = ("your", "placeholder", "example", "xxx", "sk-your", "-here", "todo", "change-me")
    return any(tok in lowered for tok in tokens)


_raw_api_key = os.environ.get("LLM_API_KEY", "").strip()
LLM_API_KEY = "" if _looks_like_placeholder(_raw_api_key) else _raw_api_key

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini").strip()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()

# 静态资源目录
STATIC_DIR = Path(__file__).resolve().parent / "static"

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("mini-tool-gen")


# ===========================================================================
# 预置 Mock HTML 示例
# ===========================================================================
# 严格的单文件 HTML，每个都包含 <!DOCTYPE html> + <html> + <head> + <body>
# 完全自包含，可直接 iframe 渲染，不依赖外部 CDN（纯原生 JS/CSS）。

# ---------- Mock 1：房贷计算器（必须真正可用，含提前还款对比） ----------
MOCK_MORTGAGE_CALCULATOR = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<title>房贷计算器 · 月供 + 提前还款对比</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px;
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
    min-height: 100vh; color: #1a1a2e;
  }
  h1 { margin: 0 0 6px 0; font-size: 22px; }
  .sub { color: #666; font-size: 13px; margin-bottom: 20px; }
  .card {
    background: #fff; border-radius: 12px; padding: 20px;
    box-shadow: 0 4px 20px rgba(0,0,0,.08); margin-bottom: 16px;
  }
  .row { display: flex; gap: 12px; flex-wrap: wrap; }
  .field { flex: 1; min-width: 140px; }
  label { display: block; font-size: 13px; color: #555; margin-bottom: 6px; font-weight: 500; }
  input, select {
    width: 100%; padding: 10px 12px; font-size: 14px;
    border: 1px solid #ddd; border-radius: 8px; outline: none;
    transition: border-color .2s; background: #fafafa;
  }
  input:focus, select:focus { border-color: #4361ee; background: #fff; }
  button {
    padding: 12px 24px; font-size: 14px; font-weight: 600;
    background: #4361ee; color: #fff; border: none; border-radius: 8px;
    cursor: pointer; transition: transform .1s, background .2s; margin-top: 8px;
  }
  button:hover { background: #3a56d4; }
  button:active { transform: scale(.98); }
  .result-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; margin-top: 12px;
  }
  .result-item {
    background: #f0f4ff; border-left: 4px solid #4361ee;
    padding: 12px; border-radius: 6px;
  }
  .result-item .label { font-size: 12px; color: #666; }
  .result-item .value { font-size: 20px; font-weight: 700; color: #4361ee; margin-top: 4px; }
  .compare { border-color: #f72585; background: #fff0f6; }
  .compare .value { color: #f72585; }
  .diff-badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 12px; font-weight: 600; margin-left: 6px;
  }
  .diff-save { background: #d4edda; color: #155724; }
  .diff-more { background: #f8d7da; color: #721c24; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  th, td { padding: 8px 10px; text-align: right; border-bottom: 1px solid #eee; }
  th:first-child, td:first-child { text-align: left; }
  th { background: #f8f9fa; font-weight: 600; color: #555; }
  .hidden { display: none; }
  .toast {
    position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
    background: #333; color: #fff; padding: 10px 20px; border-radius: 6px;
    font-size: 13px; z-index: 999; opacity: 0; transition: opacity .3s;
  }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<div class="toast" id="toast"></div>

<h1>🏠 房贷计算器</h1>
<div class="sub">月供计算 · 总利息统计 · 提前还款对比（缩短期限 / 减少月供 两种方案）</div>

<!-- ========= 基本信息 ========= -->
<div class="card">
  <h3 style="margin:0 0 14px 0;font-size:15px;">① 基本贷款信息</h3>
  <div class="row">
    <div class="field">
      <label>贷款总额（万元）</label>
      <input type="number" id="amount" value="100" min="1" step="1" />
    </div>
    <div class="field">
      <label>年利率（%）</label>
      <input type="number" id="rate" value="3.45" min="0.01" step="0.01" />
    </div>
    <div class="field">
      <label>贷款期限（年）</label>
      <input type="number" id="years" value="30" min="1" max="30" step="1" />
    </div>
    <div class="field">
      <label>还款方式</label>
      <select id="method">
        <option value="equal_principal_interest">等额本息</option>
        <option value="equal_principal">等额本金</option>
      </select>
    </div>
  </div>
  <button onclick="calcBase()">立即计算</button>
</div>

<!-- ========= 基础结果 ========= -->
<div class="card hidden" id="baseResult">
  <h3 style="margin:0 0 14px 0;font-size:15px;">② 基础还款方案</h3>
  <div class="result-grid" id="baseGrid"></div>
</div>

<!-- ========= 提前还款 ========= -->
<div class="card hidden" id="prepaySection">
  <h3 style="margin:0 0 14px 0;font-size:15px;">③ 提前还款对比</h3>
  <div class="row">
    <div class="field">
      <label>已还期数（月）</label>
      <input type="number" id="paidMonths" value="12" min="0" step="1" />
    </div>
    <div class="field">
      <label>提前还款金额（万元）</label>
      <input type="number" id="prepayAmount" value="20" min="1" step="1" />
    </div>
    <div class="field">
      <label>优化方式</label>
      <select id="prepayType">
        <option value="reduce_months">缩短期限（月供不变）</option>
        <option value="reduce_payment">减少月供（期限不变）</option>
      </select>
    </div>
  </div>
  <button onclick="calcPrepay()">计算提前还款方案</button>

  <div id="prepayResult" class="hidden" style="margin-top:14px;"></div>
</div>

<script>
/* ============================================================
   核心计算函数（纯 JS，无外部依赖）
   ============================================================ */

/**
 * 等额本息月供
 * 公式: M = P * r * (1+r)^n / ((1+r)^n - 1)
 */
function equalPrincipalInterest(principal, monthlyRate, months) {
  if (monthlyRate === 0) return principal / months;
  const r = monthlyRate;
  const n = months;
  const M = principal * r * Math.pow(1 + r, n) / (Math.pow(1 + r, n) - 1);
  return M;
}

/**
 * 等额本金：首月月供（后续每月递减 M/months）
 */
function equalPrincipalFirstMonth(principal, monthlyRate, months) {
  const principalPart = principal / months;
  const firstInterest = principal * monthlyRate;
  return principalPart + firstInterest;
}

/**
 * 生成完整还款计划（用于提前还款计算剩余本金）
 * 返回: { schedule: [{month, payment, principal, interest, remaining}], totalPayment, totalInterest }
 */
function buildSchedule(principal, annualRate, years, method) {
  const monthlyRate = annualRate / 100 / 12;
  const months = years * 12;
  const schedule = [];
  let remaining = principal;
  let totalPayment = 0;

  if (method === 'equal_principal_interest') {
    const M = equalPrincipalInterest(principal, monthlyRate, months);
    for (let i = 1; i <= months; i++) {
      const interest = remaining * monthlyRate;
      const princ = M - interest;
      remaining -= princ;
      if (remaining < 0) remaining = 0;
      totalPayment += M;
      schedule.push({ month: i, payment: M, principal: princ, interest, remaining });
    }
  } else {
    const monthlyPrincipal = principal / months;
    for (let i = 1; i <= months; i++) {
      const interest = remaining * monthlyRate;
      const payment = monthlyPrincipal + interest;
      remaining -= monthlyPrincipal;
      if (remaining < 0) remaining = 0;
      totalPayment += payment;
      schedule.push({ month: i, payment, principal: monthlyPrincipal, interest, remaining });
    }
  }

  return {
    schedule,
    totalPayment,
    totalInterest: totalPayment - principal,
    monthlyRate,
    months
  };
}

/* ============================================================
   页面交互
   ============================================================ */
let baseData = null;

function fmtMoney(n) {
  return (Math.round(n * 100) / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}

function parseInputs() {
  const amountWan = parseFloat(document.getElementById('amount').value);
  const annualRate = parseFloat(document.getElementById('rate').value);
  const years = parseInt(document.getElementById('years').value);
  const method = document.getElementById('method').value;
  if (!amountWan || amountWan <= 0) { showToast('请输入有效的贷款金额'); return null; }
  if (!annualRate || annualRate <= 0) { showToast('请输入有效的年利率'); return null; }
  if (!years || years <= 0) { showToast('请输入有效的贷款期限'); return null; }
  return { principal: amountWan * 10000, annualRate, years, method };
}

function calcBase() {
  const input = parseInputs();
  if (!input) return;
  const data = buildSchedule(input.principal, input.annualRate, input.years, input.method);
  baseData = data;

  const firstPayment = data.schedule[0].payment;
  const lastPayment = data.schedule[data.schedule.length - 1].payment;
  const isEqualPI = input.method === 'equal_principal_interest';

  document.getElementById('baseResult').classList.remove('hidden');
  document.getElementById('prepaySection').classList.remove('hidden');

  document.getElementById('baseGrid').innerHTML = `
    <div class="result-item">
      <div class="label">${isEqualPI ? '每月固定月供' : '首月月供'}</div>
      <div class="value">¥ ${fmtMoney(firstPayment)}</div>
    </div>
    ${!isEqualPI ? `<div class="result-item">
      <div class="label">末月月供</div>
      <div class="value">¥ ${fmtMoney(lastPayment)}</div>
    </div>` : ''}
    <div class="result-item">
      <div class="label">还款总额</div>
      <div class="value">¥ ${fmtMoney(data.totalPayment)}</div>
    </div>
    <div class="result-item">
      <div class="label">总利息</div>
      <div class="value">¥ ${fmtMoney(data.totalInterest)}</div>
    </div>
    <div class="result-item">
      <div class="label">还款期数</div>
      <div class="value">${data.months} 期</div>
    </div>
  `;
}

function calcPrepay() {
  if (!baseData) { showToast('请先计算基础方案'); return; }

  const paidMonths = parseInt(document.getElementById('paidMonths').value);
  const prepayWan = parseFloat(document.getElementById('prepayAmount').value);
  const prepayType = document.getElementById('prepayType').value;

  if (paidMonths < 0 || paidMonths >= baseData.months) {
    showToast('已还期数需在 0 ~ ' + (baseData.months - 1) + ' 之间'); return;
  }
  if (!prepayWan || prepayWan <= 0) { showToast('请输入有效的提前还款金额'); return; }

  const prepay = prepayWan * 10000;
  const remainPrincipal = baseData.schedule[paidMonths].remaining;
  const remainingMonths = baseData.months - paidMonths;

  if (prepay > remainPrincipal) {
    showToast(`剩余本金仅 ¥${fmtMoney(remainPrincipal)}，请减少提前还款金额`); return;
  }

  const newPrincipal = remainPrincipal - prepay;
  const monthlyRate = baseData.monthlyRate;

  // 方案 A: 缩短期限（月供不变）—— 求解新期数 n' 使 M 不变
  // M = P' * r * (1+r)^n' / ((1+r)^n' - 1)
  // => (1+r)^n' = M / (M - P'*r)
  let newMonthsA = remainingMonths;
  let paymentA = baseData.schedule[paidMonths].payment; // 按原剩余期间的月供
  if (baseData.method === 'equal_principal_interest') {
    const M = equalPrincipalInterest(baseData.schedule[paidMonths].remaining, monthlyRate, remainingMonths);
    paymentA = M;
    if (monthlyRate > 0) {
      const ratio = M / (M - newPrincipal * monthlyRate);
      if (ratio > 1) {
        newMonthsA = Math.ceil(Math.log(ratio) / Math.log(1 + monthlyRate));
      } else {
        newMonthsA = 1;
      }
    } else {
      newMonthsA = Math.ceil(newPrincipal / M);
    }
  } else {
    // 等额本金缩短期限：每月本金变为 newPrincipal / newMonthsA，interest = remaining * rate
    // 这里简化：按"每月还款额 = prepay 前的最后一期还款额"来倒推 n'
    const lastPayment = baseData.schedule[paidMonths].payment;
    // 近似：newMonthsA ≈ newPrincipal / (lastPayment - avg_interest)
    // 直接尝试直到累计月供 >= newPrincipal + 总利息
    let m = 1;
    while (m < remainingMonths) {
      let bal = newPrincipal, cum = 0;
      for (let i = 0; i < m; i++) {
        const interest = bal * monthlyRate;
        const princ = lastPayment - interest;
        if (princ <= 0) { m++; break; }
        bal -= princ;
        cum += lastPayment;
      }
      if (bal <= 0) { newMonthsA = m; paymentA = lastPayment; break; }
      m++;
      newMonthsA = m;
    }
    paymentA = lastPayment;
  }

  // 方案 B: 减少月供（期限不变）—— 新月供 M' 基于剩余本金 newPrincipal 和原剩余期数
  let paymentB = equalPrincipalInterest(newPrincipal, monthlyRate, remainingMonths);
  // 等额本金下减少月供方案：新首月 = newPrincipal / remainingMonths + newPrincipal * rate
  if (baseData.method === 'equal_principal') {
    paymentB = newPrincipal / remainingMonths + newPrincipal * monthlyRate;
  }

  // 计算两种方案的总利息节省
  function totalInterestForScheme(newP, newM, months, method) {
    let bal = newP, total = 0;
    if (method === 'equal_principal_interest') {
      for (let i = 0; i < months; i++) {
        const interest = bal * monthlyRate;
        const princ = newM - interest;
        bal -= princ; total += interest;
        if (bal <= 0) break;
      }
    } else {
      const monthlyPrinc = newP / months;
      for (let i = 0; i < months; i++) {
        const interest = bal * monthlyRate;
        bal -= monthlyPrinc; total += interest;
        if (bal <= 0) break;
      }
    }
    return total;
  }

  // 原方案剩余利息
  const originalRemainingInterest = totalInterestForScheme(
    baseData.schedule[paidMonths].remaining,
    baseData.schedule[paidMonths].payment,
    remainingMonths,
    baseData.method
  );

  const interestA = totalInterestForScheme(newPrincipal, paymentA, newMonthsA, baseData.method);
  const interestB = totalInterestForScheme(newPrincipal, paymentB, remainingMonths, baseData.method);

  const savedA = originalRemainingInterest - interestA;
  const savedB = originalRemainingInterest - interestB;

  const originalCost = baseData.totalInterest;
  const finalInterestA = originalCost - originalRemainingInterest + interestA;
  const finalInterestB = originalCost - originalRemainingInterest + interestB;

  const html = `
    <div style="margin-top:12px;">
      <div style="background:#fff3cd;border-left:4px solid #ffc107;padding:10px 14px;border-radius:6px;font-size:13px;">
        📌 截至第 <b>${paidMonths}</b> 期，剩余本金 <b>¥${fmtMoney(remainPrincipal)}</b>；
        您计划提前还款 <b>¥${fmtMoney(prepay)}</b>，剩余本金变为 <b>¥${fmtMoney(newPrincipal)}</b>
      </div>

      <div class="row" style="margin-top:16px;">
        <!-- 方案 A -->
        <div class="card" style="flex:1;margin:0 8px 0 0;min-width:280px;">
          <h4 style="margin:0 0 10px;color:#4361ee;">方案 A：缩短期限（月供不变）</h4>
          <table>
            <tr><td>月供</td><td><b>¥${fmtMoney(paymentA)}</b></td></tr>
            <tr><td>剩余期数</td><td><b>${newMonthsA} 期</b>（原 ${remainingMonths} 期，少还 ${remainingMonths - newMonthsA} 期）</td></tr>
            <tr><td>后续总利息</td><td>¥${fmtMoney(interestA)}</td></tr>
            <tr><td>总利息节省</td><td><span class="diff-badge ${savedA >= 0 ? 'diff-save' : 'diff-more'}">${savedA >= 0 ? '节省' : '多花'} ¥${fmtMoney(Math.abs(savedA))}</span></td></tr>
            <tr><td>贷款总利息</td><td><b>¥${fmtMoney(finalInterestA)}</b></td></tr>
          </table>
        </div>

        <!-- 方案 B -->
        <div class="card" style="flex:1;margin:0 0 0 8px;min-width:280px;">
          <h4 style="margin:0 0 10px;color:#f72585;">方案 B：减少月供（期限不变）</h4>
          <table>
            <tr><td>新月供</td><td><b>¥${fmtMoney(paymentB)}</b>（原 ¥${fmtMoney(baseData.schedule[paidMonths].payment)}）</td></tr>
            <tr><td>月供减少</td><td><span class="diff-badge diff-save">少 ¥${fmtMoney(baseData.schedule[paidMonths].payment - paymentB)}</span></td></tr>
            <tr><td>后续总利息</td><td>¥${fmtMoney(interestB)}</td></tr>
            <tr><td>总利息节省</td><td><span class="diff-badge ${savedB >= 0 ? 'diff-save' : 'diff-more'}">${savedB >= 0 ? '节省' : '多花'} ¥${fmtMoney(Math.abs(savedB))}</span></td></tr>
            <tr><td>贷款总利息</td><td><b>¥${fmtMoney(finalInterestB)}</b></td></tr>
          </table>
        </div>
      </div>

      <div style="background:#e8f5e9;border-left:4px solid #2e7d32;padding:10px 14px;border-radius:6px;font-size:13px;margin-top:16px;">
        💡 <b>建议</b>：如果现金流充裕，<b>缩短期限</b>通常能节省更多总利息；
        如果希望减轻月供压力，<b>减少月供</b>更合适。两者都比不提前还款划算 ✅
      </div>
    </div>
  `;
  document.getElementById('prepayResult').classList.remove('hidden');
  document.getElementById('prepayResult').innerHTML = html;
}

// 页面加载时自动算一次
window.addEventListener('DOMContentLoaded', calcBase);
</script>
</body>
</html>"""


# ---------- Mock 2：待办事项清单 ----------
MOCK_TODO_LIST = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<title>待办事项清单</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 20px;
    font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
    background: #f0f2f5; min-height: 100vh; color: #1a1a2e;
  }
  h1 { margin: 0 0 16px; font-size: 22px; }
  .card {
    background: #fff; border-radius: 12px; padding: 20px;
    box-shadow: 0 2px 12px rgba(0,0,0,.06);
  }
  .input-row { display: flex; gap: 8px; margin-bottom: 16px; }
  input[type="text"] {
    flex: 1; padding: 10px 14px; font-size: 14px;
    border: 1px solid #ddd; border-radius: 8px; outline: none;
  }
  input[type="text"]:focus { border-color: #4361ee; }
  button {
    padding: 10px 18px; font-size: 14px; font-weight: 600;
    background: #4361ee; color: #fff; border: none; border-radius: 8px; cursor: pointer;
  }
  button:hover { background: #3a56d4; }
  .filter-bar { display: flex; gap: 6px; margin-bottom: 12px; }
  .filter-btn {
    background: #e9ecef; color: #555; padding: 6px 14px; font-size: 13px;
  }
  .filter-btn.active { background: #4361ee; color: #fff; }
  ul { list-style: none; padding: 0; margin: 0; }
  li {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 12px; border-bottom: 1px solid #f0f0f0;
    font-size: 14px;
  }
  li.done .text { text-decoration: line-through; color: #aaa; }
  li input[type="checkbox"] { width: 18px; height: 18px; cursor: pointer; }
  li .text { flex: 1; }
  li .del {
    background: #fee; color: #c33; font-size: 12px; padding: 4px 10px;
  }
  .stats { color: #888; font-size: 12px; margin-top: 12px; }
  .empty { text-align: center; color: #aaa; padding: 30px 0; }
</style>
</head>
<body>
  <h1>✅ 待办事项清单</h1>
  <div class="card">
    <div class="input-row">
      <input type="text" id="newItem" placeholder="输入一个待办事项，回车添加..." />
      <button onclick="addItem()">添加</button>
    </div>
    <div class="filter-bar">
      <button class="filter-btn active" data-f="all" onclick="setFilter('all', this)">全部</button>
      <button class="filter-btn" data-f="active" onclick="setFilter('active', this)">未完成</button>
      <button class="filter-btn" data-f="done" onclick="setFilter('done', this)">已完成</button>
      <button class="filter-btn" style="background:#fee;color:#c33;margin-left:auto;" onclick="clearDone()">清除已完成</button>
    </div>
    <ul id="list"></ul>
    <div class="stats" id="stats"></div>
  </div>

<script>
const STORAGE_KEY = 'minitool_todos';
let todos = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
let filter = 'all';

function save() { localStorage.setItem(STORAGE_KEY, JSON.stringify(todos)); }

function render() {
  const ul = document.getElementById('list');
  let filtered = todos;
  if (filter === 'active') filtered = todos.filter(t => !t.done);
  else if (filter === 'done') filtered = todos.filter(t => t.done);

  if (filtered.length === 0) {
    ul.innerHTML = '<div class="empty">暂无事项 ～</div>';
  } else {
    ul.innerHTML = filtered.map(t => `
      <li class="${t.done ? 'done' : ''}">
        <input type="checkbox" ${t.done ? 'checked' : ''} onchange="toggle(${t.id})" />
        <span class="text">${escapeHtml(t.text)}</span>
        <button class="del" onclick="remove(${t.id})">删除</button>
      </li>
    `).join('');
  }
  const activeCount = todos.filter(t => !t.done).length;
  document.getElementById('stats').textContent = `共 ${todos.length} 项 · 待办 ${activeCount} 项 · 已完成 ${todos.length - activeCount} 项`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function addItem() {
  const input = document.getElementById('newItem');
  const text = input.value.trim();
  if (!text) return;
  todos.unshift({ id: Date.now(), text, done: false });
  save(); render();
  input.value = ''; input.focus();
}

function toggle(id) {
  todos = todos.map(t => t.id === id ? {...t, done: !t.done} : t);
  save(); render();
}

function remove(id) {
  todos = todos.filter(t => t.id !== id);
  save(); render();
}

function clearDone() {
  todos = todos.filter(t => !t.done);
  save(); render();
}

function setFilter(f, btn) {
  filter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  render();
}

document.getElementById('newItem').addEventListener('keydown', e => {
  if (e.key === 'Enter') addItem();
});

// 首次加载示例数据
if (todos.length === 0) {
  todos = [
    { id: 1, text: '试用小工具生成器 🎉', done: true },
    { id: 2, text: '点一下复选框试试', done: false },
    { id: 3, text: '在输入框里加自己的待办', done: false }
  ];
  save();
}
render();
</script>
</body>
</html>"""


# ---------- Mock 3：JSON 格式化工具 ----------
MOCK_JSON_FORMATTER = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<title>JSON 格式化工具</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 20px;
    font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
    background: #f0f2f5; min-height: 100vh; color: #1a1a2e;
  }
  h1 { margin: 0 0 16px; font-size: 22px; }
  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
  button {
    padding: 8px 16px; font-size: 13px; font-weight: 600;
    border: none; border-radius: 6px; cursor: pointer;
  }
  .btn-primary { background: #4361ee; color: #fff; }
  .btn-primary:hover { background: #3a56d4; }
  .btn-secondary { background: #e9ecef; color: #333; }
  .btn-secondary:hover { background: #dee2e6; }
  textarea {
    width: 100%; min-height: 320px; padding: 14px;
    font-family: "Consolas", "Monaco", monospace; font-size: 13px;
    border: 1px solid #ddd; border-radius: 8px; outline: none; resize: vertical;
    background: #fff;
  }
  textarea:focus { border-color: #4361ee; }
  .status {
    margin-top: 10px; padding: 8px 12px; border-radius: 6px;
    font-size: 13px; display: none;
  }
  .ok { background: #d4edda; color: #155724; display: block; }
  .err { background: #f8d7da; color: #721c24; display: block; }
</style>
</head>
<body>
  <h1>🧹 JSON 格式化工具</h1>
  <div class="toolbar">
    <button class="btn-primary" onclick="beautify()">✨ 美化</button>
    <button class="btn-secondary" onclick="minify()">📦 压缩</button>
    <button class="btn-secondary" onclick="validate()">✅ 校验</button>
    <button class="btn-secondary" onclick="sortKeys()">🔤 按键名排序</button>
    <button class="btn-secondary" onclick="copyAll()">📋 复制</button>
    <button class="btn-secondary" onclick="clearAll()">🗑 清空</button>
    <button class="btn-secondary" onclick="loadSample()">📝 加载示例</button>
  </div>
  <textarea id="editor" placeholder='在这里粘贴 JSON，例如：{"name":"小明","age":18}'></textarea>
  <div id="status" class="status"></div>

<script>
function setStatus(msg, type) {
  const s = document.getElementById('status');
  s.textContent = msg;
  s.className = 'status ' + type;
}

function getJSON() {
  const raw = document.getElementById('editor').value.trim();
  if (!raw) { setStatus('请先输入 JSON 内容', 'err'); return null; }
  try {
    return JSON.parse(raw);
  } catch (e) {
    setStatus('❌ JSON 解析失败: ' + e.message, 'err');
    return null;
  }
}

function beautify() {
  const obj = getJSON();
  if (obj === null) return;
  document.getElementById('editor').value = JSON.stringify(obj, null, 2);
  setStatus('✅ 格式化成功', 'ok');
}

function minify() {
  const obj = getJSON();
  if (obj === null) return;
  document.getElementById('editor').value = JSON.stringify(obj);
  setStatus('✅ 压缩成功', 'ok');
}

function validate() {
  const raw = document.getElementById('editor').value.trim();
  if (!raw) { setStatus('内容为空', 'err'); return; }
  try {
    JSON.parse(raw);
    setStatus('✅ 这是一个合法的 JSON', 'ok');
  } catch (e) {
    setStatus('❌ 非法 JSON: ' + e.message, 'err');
  }
}

function sortKeys() {
  const obj = getJSON();
  if (obj === null) return;
  const sorted = sortObject(obj);
  document.getElementById('editor').value = JSON.stringify(sorted, null, 2);
  setStatus('✅ 已按键名字典序排序', 'ok');
}

function sortObject(o) {
  if (Array.isArray(o)) return o.map(sortObject);
  if (o && typeof o === 'object') {
    return Object.keys(o).sort().reduce((acc, k) => {
      acc[k] = sortObject(o[k]); return acc;
    }, {});
  }
  return o;
}

function copyAll() {
  const ta = document.getElementById('editor');
  ta.select();
  document.execCommand('copy');
  setStatus('✅ 已复制到剪贴板', 'ok');
}

function clearAll() {
  document.getElementById('editor').value = '';
  document.getElementById('status').className = 'status';
}

function loadSample() {
  document.getElementById('editor').value = '{"name":"小明","age":18,"hobbies":["编程","阅读"],"address":{"city":"上海","district":"浦东"},"active":true}';
  setStatus('已加载示例 JSON', 'ok');
}

// 自动校验提示
document.getElementById('editor').addEventListener('input', () => {
  const raw = document.getElementById('editor').value.trim();
  if (!raw) return;
  try {
    JSON.parse(raw);
    setStatus('✅ 实时校验: 合法 JSON', 'ok');
  } catch (e) {
    setStatus('⚠️ 尚未完成或格式有误: ' + e.message, 'err');
  }
});
</script>
</body>
</html>"""


# 把 3 个 mock 放在一个列表里，便于按 prompt 关键词匹配
MOCK_EXAMPLES = [
    {"keywords": ["房贷", "按揭", "月供", "还款", "mortgage", "贷款"],
     "html": MOCK_MORTGAGE_CALCULATOR,
     "name": "房贷计算器"},
    {"keywords": ["待办", "todo", "清单", "任务", "备忘录", "事项"],
     "html": MOCK_TODO_LIST,
     "name": "待办事项清单"},
    {"keywords": ["json", "格式化", "校验", "压缩", "json 工具"],
     "html": MOCK_JSON_FORMATTER,
     "name": "JSON 格式化工具"},
]


# ===========================================================================
# 大模型调用（OpenAI 兼容格式）
# ===========================================================================
def _call_llm(prompt: str) -> Optional[str]:
    """调用大模型，成功返回 HTML 字符串，失败返回 None。"""
    try:
        import httpx

        system_prompt = (
            "你是一个前端工具生成器。用户会描述一个小工具的需求，"
            "请你输出一段完整的、自包含的单文件 HTML 代码。"
            "要求：\n"
            "1. 必须以 <!DOCTYPE html> 开头，包含完整的 <html><head><body> 结构；\n"
            "2. 所有 CSS 和 JS 必须内联（不依赖外部 CDN）；\n"
            "3. 使用原生 JavaScript，不要用 React/Vue/Angular；\n"
            "4. 样式美观、交互可用、中文界面；\n"
            "5. 只输出 HTML 代码本身，不要任何解释、Markdown 代码块标记（```html```）或额外文字。"
        )

        payload = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
        }

        url = f"{LLM_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        # 连接 5 秒 + 整体 10 秒超时，假 key 或网络问题快速失败，别让前端一直等
        logger.info(f"调用大模型: model={LLM_MODEL}, url={url}")
        resp = httpx.post(url, headers=headers, json=payload,
                          timeout=httpx.Timeout(10.0, connect=5.0))
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"].strip()

        # 清洗可能存在的 markdown 代码块标记
        content = re.sub(r"^```(?:html|HTML)?\s*", "", content)
        content = re.sub(r"\s*```\s*$", "", content)

        if "<!DOCTYPE html>" not in content.lower() and "<html" not in content.lower():
            logger.warning("大模型返回内容看起来不是 HTML，可能格式有问题")
        return content

    except Exception as e:
        logger.error(f"大模型调用失败: {e}")
        return None


# ===========================================================================
# 核心入口：generate_app(prompt) -> str
# ===========================================================================
def generate_app(prompt: str) -> str:
    """
    根据用户描述生成一段完整的单文件 HTML 工具页面。

    - 有 LLM_API_KEY 时优先调用大模型
    - 无 API Key 或大模型失败时，根据 prompt 关键词匹配返回预置 mock
    - 完全离线也能跑通

    函数签名保持稳定，方便未来替换成其他大模型 / 多模态生成方案。
    """
    # --- 1. 尝试真实大模型 ---
    if LLM_API_KEY:
        logger.info(f"检测到 LLM_API_KEY，尝试调用大模型 (prompt={prompt[:50]}...)")
        html = _call_llm(prompt)
        if html:
            logger.info("大模型调用成功")
            return html, "llm"
        logger.warning("大模型调用失败，回退到 mock 模式")
    else:
        logger.info("未检测到 LLM_API_KEY，直接进入 mock 模式")

    # --- 2. Mock 模式：按关键词匹配，匹配不到就随机 ---
    lower_prompt = prompt.lower()
    for example in MOCK_EXAMPLES:
        for kw in example["keywords"]:
            if kw.lower() in lower_prompt:
                logger.info(f"mock 匹配命中: {example['name']} (关键词={kw})")
                return example["html"], "mock"

    # 兜底：返回房贷计算器（最复杂、最有用的示例）
    logger.info("无关键词命中，默认返回房贷计算器示例")
    return MOCK_MORTGAGE_CALCULATOR, "mock"


# ===========================================================================
# FastAPI 应用
# ===========================================================================
app = FastAPI(title="小工具生成器", version="1.0.0")


class GenerateRequest(BaseModel):
    prompt: str


@app.post("/api/generate")
async def api_generate(req: GenerateRequest):
    """生成工具 HTML 的核心接口"""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")

    html, mode = generate_app(req.prompt.strip())
    return JSONResponse({
        "html": html,
        "mode": mode,  # "llm" 或 "mock"
        "prompt": req.prompt.strip(),
    })


@app.get("/api/health")
async def api_health():
    """健康检查 + 当前模式"""
    return {
        "status": "ok",
        "llm_configured": bool(LLM_API_KEY),
        "supabase_configured": bool(SUPABASE_URL),
        "mock_examples": [e["name"] for e in MOCK_EXAMPLES],
    }


# 挂载静态文件（前端页面）
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    """返回前端首页"""
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"message": "前端未找到，请在 static/index.html 放入首页"})


# ===========================================================================
# 启动入口
# ===========================================================================
if __name__ == "__main__":
    import uvicorn
    logger.info(f"========== 小工具生成器启动 ==========")
    logger.info(f"监听地址 : 0.0.0.0:{PORT}")
    logger.info(f"LLM 配置 : {'已配置' if LLM_API_KEY else '未配置（将走 mock 模式）'}")
    logger.info(f"Supabase : {'已配置' if SUPABASE_URL else '未配置'}")
    logger.info(f"=====================================")
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
