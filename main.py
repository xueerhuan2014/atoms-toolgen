"""
小工具生成器 — FastAPI 后端入口

核心职责：
  1. POST /api/generate       接收 prompt，返回一段可直接在 iframe 中渲染的单文件 HTML
  2. POST /api/generate_race  赛马模式：asyncio 并发 3 路生成，单路失败不影响其余
  3. GET  /                   返回 static/index.html 前端页面

设计原则：
  - generate_app(prompt) -> str 独立封装，便于替换为真实大模型调用
  - 所有密钥 / 配置从环境变量读取，代码不含任何真实敏感信息
  - Mock 模式：无 API Key 或调用失败时，自动返回 3 个预置示例 HTML
"""

import os
import re
import json
import time
import random
import asyncio
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

# 加载 .env（若存在）
load_dotenv()

import db  # noqa: E402  必须在 load_dotenv() 之后导入，保证 db 模块读到环境变量

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

# 超时与重试：单次请求超时 + 失败后最多再试 2 次（可用环境变量覆盖）
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))            # 生成单次超时
LLM_TIMEOUT_ITERATE = float(os.environ.get("LLM_TIMEOUT_ITERATE", "90"))  # 修改单次超时（输入更长）
LLM_MAX_RETRIES = max(0, int(os.environ.get("LLM_MAX_RETRIES", "2")))
LLM_RETRY_BACKOFF = float(os.environ.get("LLM_RETRY_BACKOFF", "1.5"))

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
def _build_llm_payload(prompt: str, temperature: float = 0.7, style_hint: str = "") -> dict:
    """组装 OpenAI 兼容的 chat/completions 请求体。style_hint 用于赛马模式区分风格。"""
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
    user_content = prompt if not style_hint else f"{prompt}\n{style_hint}"
    return {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
    }


def _clean_llm_html(content: str) -> Optional[str]:
    """
    清洗并校验大模型返回内容，失败返回 None。

    模型常见的不听话输出（都会被处理）：
    1. 纯 HTML；
    2. 整体包在 ```html ... ``` 里；
    3. 前面一段说明文字，再 ```html，再 HTML（围栏在中间）；
    4. HTML 后面还跟一段说明文字。
    做法：先去掉第一个围栏及其之前的内容，再从第一个 HTML 起始标记裁到最后一个 </html>。
    """
    content = content.strip()

    # 1) 定位第一个 markdown 围栏，丢弃围栏及其之前的所有说明文字
    fence = re.search(r"```(?:html|HTML)?\s*\n?", content)
    if fence:
        content = content[fence.end():]

    # 2) 裁剪到第一个 HTML 起始标记（防止围栏被写坏时仍有前置文字）
    lower = content.lower()
    start = lower.find("<!doctype html")
    if start == -1:
        start = lower.find("<html")
    if start > 0:
        content = content[start:]

    # 3) 裁剪到最后一个 </html>，丢弃后面的解释文字
    lower = content.lower()
    end = lower.rfind("</html>")
    if end != -1:
        content = content[: end + len("</html>")]

    content = content.strip()

    if not content:
        logger.error("大模型返回空内容")
        return None
    if "<!DOCTYPE html>" not in content.lower() and "<html" not in content.lower():
        logger.error(f"大模型返回非 HTML 内容（前200字): {content[:200]}")
        return None
    return content


def _friendly_status_reason(status_code: int) -> str:
    """把 HTTP 状态码翻译成用户能看懂的原因。"""
    if status_code in (401, 403):
        return "API Key 无效或没有权限"
    if status_code == 429:
        return "模型服务限流或额度已耗尽"
    if status_code == 408:
        return "模型服务响应超时"
    if status_code >= 500:
        return f"模型服务端异常（{status_code}）"
    return f"模型服务返回错误（{status_code}）"


def _is_retryable_status(status_code: int) -> bool:
    """只有超时/限流/服务端异常值得重试；鉴权、参数类错误重试无意义。"""
    return status_code in (408, 429) or status_code >= 500


def _llm_headers() -> dict:
    return {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }


def _extract_llm_html(resp) -> tuple[Optional[str], Optional[str]]:
    """从响应中取出并清洗 HTML，返回 (html, 失败原因)。"""
    try:
        raw = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"解析模型响应失败: {type(e).__name__}: {e}")
        return None, "模型返回格式异常"

    content = _clean_llm_html(raw or "")
    if content is None:
        return None, "模型返回内容不是有效网页"
    return content, None


def _post_llm(url: str, payload: dict, timeout_seconds: float, tag: str):
    """同步调用 + 超时 + 重试。

    成功返回 (response, None)；失败返回 (None, 用户可读原因)。
    """
    import httpx

    reason = "模型服务暂时不可用"
    for attempt in range(1, LLM_MAX_RETRIES + 2):
        try:
            resp = httpx.post(url, headers=_llm_headers(), json=payload,
                              timeout=httpx.Timeout(timeout_seconds, connect=5.0))
        except httpx.TimeoutException:
            reason = f"模型响应超时（超过 {int(timeout_seconds)} 秒）"
            logger.warning(f"{tag} 第 {attempt} 次调用超时")
        except httpx.ConnectError as e:
            reason = "网络连接失败（DNS 或网络不可达）"
            logger.warning(f"{tag} 第 {attempt} 次连接失败: {e}")
        except Exception as e:
            reason = "网络请求异常"
            logger.warning(f"{tag} 第 {attempt} 次调用异常: {type(e).__name__}: {e}")
        else:
            if resp.status_code < 400:
                return resp, None
            reason = _friendly_status_reason(resp.status_code)
            if not _is_retryable_status(resp.status_code):
                logger.error(f"{tag} 调用失败且不可重试: {reason}")
                return None, reason
            logger.warning(f"{tag} 第 {attempt} 次调用失败: {reason}")

        if attempt <= LLM_MAX_RETRIES:
            time.sleep(LLM_RETRY_BACKOFF * attempt)

    logger.error(f"{tag} 重试 {LLM_MAX_RETRIES} 次后仍失败: {reason}")
    return None, reason


async def _post_llm_async(url: str, payload: dict, timeout_seconds: float, tag: str):
    """_post_llm 的异步版本，供赛马模式并发调用。"""
    import httpx

    reason = "模型服务暂时不可用"
    for attempt in range(1, LLM_MAX_RETRIES + 2):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_seconds, connect=5.0)
            ) as client:
                resp = await client.post(url, headers=_llm_headers(), json=payload)
        except httpx.TimeoutException:
            reason = f"模型响应超时（超过 {int(timeout_seconds)} 秒）"
            logger.warning(f"{tag} 第 {attempt} 次调用超时")
        except httpx.ConnectError as e:
            reason = "网络连接失败（DNS 或网络不可达）"
            logger.warning(f"{tag} 第 {attempt} 次连接失败: {e}")
        except Exception as e:
            reason = "网络请求异常"
            logger.warning(f"{tag} 第 {attempt} 次调用异常: {type(e).__name__}: {e}")
        else:
            if resp.status_code < 400:
                return resp, None
            reason = _friendly_status_reason(resp.status_code)
            if not _is_retryable_status(resp.status_code):
                logger.error(f"{tag} 调用失败且不可重试: {reason}")
                return None, reason
            logger.warning(f"{tag} 第 {attempt} 次调用失败: {reason}")

        if attempt <= LLM_MAX_RETRIES:
            await asyncio.sleep(LLM_RETRY_BACKOFF * attempt)

    logger.error(f"{tag} 重试 {LLM_MAX_RETRIES} 次后仍失败: {reason}")
    return None, reason


def _call_llm(prompt: str) -> tuple[Optional[str], Optional[str]]:
    """单路生成。成功返回 (html, None)，失败返回 (None, 用户可读原因)。"""
    if not LLM_API_KEY:
        return None, "未配置 API Key"

    url = f"{LLM_BASE_URL}/chat/completions"
    logger.info(f"调用大模型: model={LLM_MODEL}, url={url}")
    resp, reason = _post_llm(url, _build_llm_payload(prompt), LLM_TIMEOUT, "单路生成")
    if resp is None:
        return None, reason

    content, reason = _extract_llm_html(resp)
    if content:
        logger.info(f"大模型返回 OK: {len(content)} 字符")
    return content, reason


async def _call_llm_async(prompt: str, temperature: float = 0.7,
                          style_hint: str = "") -> tuple[Optional[str], Optional[str]]:
    """_call_llm 的异步版本，供赛马模式用 asyncio.gather 并发调用。"""
    if not LLM_API_KEY:
        return None, "未配置 API Key"

    url = f"{LLM_BASE_URL}/chat/completions"
    resp, reason = await _post_llm_async(
        url, _build_llm_payload(prompt, temperature, style_hint), LLM_TIMEOUT, "赛马一路"
    )
    if resp is None:
        return None, reason

    content, reason = _extract_llm_html(resp)
    if content:
        logger.info(f"赛马一路返回 OK: {len(content)} 字符 (temperature={temperature})")
    return content, reason


def _call_llm_iterate(current_html: str, requirement: str) -> tuple[Optional[str], Optional[str]]:
    """
    连续修改：把「当前 HTML + 用户新要求」一起发给模型，在原 HTML 基础上改。
    成功返回 (修改后的完整 HTML, None)，失败返回 (None, 用户可读原因)，不破坏原版本。
    """
    system_prompt = (
        "你是一个前端工具修改器。你会收到一段现有的单文件 HTML 代码和用户的修改要求。"
        "请在保留原有功能与整体结构的基础上，仅按修改要求做最小必要调整，输出修改后的完整 HTML。"
        "要求：\n"
        "1. 必须以 <!DOCTYPE html> 开头，包含完整的 <html><head><body> 结构；\n"
        "2. 所有 CSS 和 JS 必须内联，使用原生 JavaScript；\n"
        "3. 只输出修改后的完整 HTML 代码本身，不要任何解释或 Markdown 代码块标记。"
    )
    user_content = (
        f"现有 HTML 代码：\n```html\n{current_html}\n```\n\n"
        f"修改要求：{requirement}\n"
        "请输出修改后的完整 HTML。"
    )
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.4,  # 修改场景低温度，减少跑偏
    }

    url = f"{LLM_BASE_URL}/chat/completions"
    logger.info(f"连续修改调用大模型: 原文 {len(current_html)} 字符, 要求: {requirement[:50]}...")
    resp, reason = _post_llm(url, payload, LLM_TIMEOUT_ITERATE, "连续修改")
    if resp is None:
        return None, reason

    content, reason = _extract_llm_html(resp)
    if content:
        logger.info(f"连续修改返回 OK: {len(content)} 字符")
    return content, reason


# ===========================================================================
# 核心入口：generate_app(prompt) -> str
# ===========================================================================
def generate_app(prompt: str) -> tuple[str, str, Optional[str]]:
    """
    根据用户描述生成一段完整的单文件 HTML 工具页面。

    - 有 LLM_API_KEY 时优先调用大模型
    - 无 API Key 或大模型失败（含重试后仍失败）时，回退预置 mock，保证流程不中断
    - 返回 (html, mode, notice)：notice 为回退原因（成功时为 None）

    函数签名保持稳定，方便未来替换成其他大模型 / 多模态生成方案。
    """
    # --- 1. 尝试真实大模型 ---
    fallback_reason = None
    if LLM_API_KEY:
        logger.info(f"检测到 LLM_API_KEY，尝试调用大模型 (prompt={prompt[:50]}...)")
        html, fallback_reason = _call_llm(prompt)
        if html:
            logger.info("大模型调用成功")
            return html, "llm", None
        logger.warning(f"大模型调用失败（{fallback_reason}），回退到 mock 模式")
    else:
        fallback_reason = "未配置 API Key"
        logger.info("未检测到 LLM_API_KEY，直接进入 mock 模式")

    # --- 2. Mock 模式：按关键词匹配，匹配不到就随机 ---
    lower_prompt = prompt.lower()
    for example in MOCK_EXAMPLES:
        for kw in example["keywords"]:
            if kw.lower() in lower_prompt:
                logger.info(f"mock 匹配命中: {example['name']} (关键词={kw})")
                return example["html"], "mock", fallback_reason

    # 兜底：返回房贷计算器（最复杂、最有用的示例）
    logger.info("无关键词命中，默认返回房贷计算器示例")
    return MOCK_MORTGAGE_CALCULATOR, "mock", fallback_reason


# ===========================================================================
# 赛马模式：同一需求，asyncio 并发 3 路生成，任意一路失败不影响其余
# ===========================================================================
RACE_VARIANTS = [
    {"label": "简洁实用", "temperature": 0.4,
     "style_hint": "本版本风格要求：极简实用，布局紧凑克制，信息密度高，少装饰。"},
    {"label": "美观现代", "temperature": 0.7,
     "style_hint": "本版本风格要求：视觉美观现代，善用圆角、阴影、渐变，留白舒适。"},
    {"label": "创意大胆", "temperature": 1.0,
     "style_hint": "本版本风格要求：大胆有创意，配色鲜明个性，可加入有趣的微交互与动画。"},
]


async def generate_race(prompt: str) -> list:
    """
    并发发起 3 路大模型生成（不同温度/风格），返回 3 个结果槽位。

    每路结果: {"label", "ok", "html", "error"}；失败路 ok=False 且 html=None。
    双保险：_call_llm_async 内部吞异常返回 None + gather(return_exceptions=True)。
    """
    async def run_variant(variant: dict) -> dict:
        html, reason = await _call_llm_async(prompt, variant["temperature"], variant["style_hint"])
        if html:
            return {"label": variant["label"], "ok": True, "html": html, "error": None}
        return {"label": variant["label"], "ok": False, "html": None,
                "error": reason or "大模型调用失败或返回无效内容"}

    results = await asyncio.gather(
        *(run_variant(v) for v in RACE_VARIANTS),
        return_exceptions=True,
    )

    # 兜底：某一路抛出未捕获异常 → 该路标记失败，不影响其他路
    final = []
    for variant, r in zip(RACE_VARIANTS, results):
        if isinstance(r, Exception):
            logger.error(f"赛马 [{variant['label']}] 未捕获异常: {type(r).__name__}: {r}")
            final.append({"label": variant["label"], "ok": False, "html": None,
                          "error": f"{type(r).__name__}: {r}"})
        else:
            final.append(r)
    return final


# ===========================================================================
# FastAPI 应用
# ===========================================================================
app = FastAPI(title="小工具生成器", version="1.0.0")


# ===========================================================================
# 全局异常处理：任何接口出错都返回可读 JSON（统一 {error,status,message}），
# 前端据此提示用户，而不是拿到空响应 / HTML 错误页导致白屏
# ===========================================================================
@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request, exc):
    """业务性错误（400/404/503 等）：把 detail 透出为 message。"""
    message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
    # Starlette 内置英文文案换成中文，避免前端直接弹英文
    if exc.status_code == 405:
        message = "该接口不支持此请求方法"
    elif exc.status_code == 404 and message == "Not Found":
        message = "接口不存在"
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "status": exc.status_code, "message": message},
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request, exc):
    """请求体 / 参数不合法（422）：不返回 pydantic 原始结构，给统一可读提示。"""
    logger.warning(f"请求参数校验失败 {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=422,
        content={"error": True, "status": 422, "message": "请求参数不合法，请检查输入"},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    """兜底：任何未捕获异常都不让服务/前端崩掉，返回 500 + 可读提示。"""
    logger.error(
        f"未捕获异常 {request.method} {request.url.path}: {type(exc).__name__}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"error": True, "status": 500, "message": "服务内部错误，请稍后重试"},
    )


class GenerateRequest(BaseModel):
    prompt: str


@app.post("/api/generate")
async def api_generate(req: GenerateRequest):
    """生成工具 HTML 的核心接口（成功后自动持久化到 Supabase）"""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")

    prompt = req.prompt.strip()
    # 同步生成放到线程池：避免长耗时调用阻塞事件循环，导致其他接口（健康检查/历史列表）无响应
    html, mode, notice = await asyncio.to_thread(generate_app, prompt)

    # 持久化：写 projects + messages；失败只记日志，不影响生成主流程
    project_id = None
    if db.ENABLED:
        row = await db.insert_project(prompt, html)
        if row:
            project_id = row.get("id")
            await db.insert_message(project_id, "user", prompt)
            await db.insert_message(project_id, "assistant", html)

    return JSONResponse({
        "html": html,
        "mode": mode,  # "llm" 或 "mock"
        "prompt": prompt,
        "project_id": project_id,
        "notice": notice,  # 回退 mock 时的原因，前端用于友好提示
    })


class ProjectCreateRequest(BaseModel):
    """赛马选定后落库：最终选中版 + 各版本快照"""
    prompt: str
    final_html: str
    race_versions: Optional[list] = None
    project_id: Optional[str] = None  # 赛马生成时已建的项目 id，选中后回填而不是新建


@app.post("/api/projects")
async def api_create_project(req: ProjectCreateRequest):
    """赛马模式点选「就用这个」后，把最终版本与各版本 HTML 写入 Supabase"""
    if not db.ENABLED:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    if not req.final_html.strip():
        raise HTTPException(status_code=400, detail="final_html 不能为空")

    # 赛马已在生成时落库 → 选中后回填同一项目，避免历史里出现重复记录
    if req.project_id:
        ok = await db.update_project_html(req.project_id, req.final_html)
        if not ok:
            raise HTTPException(status_code=502, detail="Supabase 写入失败")
        await db.insert_message(req.project_id, "assistant", req.final_html)
        return {"project_id": req.project_id, "updated": True}

    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    row = await db.insert_project(req.prompt.strip(), req.final_html, req.race_versions)
    if not row:
        raise HTTPException(status_code=502, detail="Supabase 写入失败")

    project_id = row.get("id")
    await db.insert_message(project_id, "user", req.prompt.strip())
    await db.insert_message(project_id, "assistant", req.final_html)
    return {"project_id": project_id, "updated": False}


@app.get("/api/projects")
async def api_list_projects():
    """历史项目列表（不含大字段），按创建时间倒序"""
    if not db.ENABLED:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    rows = await db.list_projects()
    if rows is None:
        raise HTTPException(status_code=502, detail="Supabase 读取失败")
    return {"projects": rows}


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str):
    """单个项目完整内容 + 对话消息，用于重新打开历史应用"""
    if not db.ENABLED:
        raise HTTPException(status_code=503, detail="Supabase 未配置")
    project = await db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    messages = await db.list_messages(project_id) or []
    return {"project": project, "messages": messages}


class IterateRequest(BaseModel):
    prompt: str


@app.post("/api/projects/{project_id}/iterate")
async def api_iterate_project(project_id: str, req: IterateRequest):
    """连续修改：当前 HTML + 新要求一起发给模型，在原 HTML 上改并写回"""
    if not db.ENABLED:
        raise HTTPException(status_code=503, detail="Supabase 未配置，无法连续修改")
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")

    project = await db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    current_html = project.get("final_html") or ""
    if not current_html:
        raise HTTPException(status_code=400, detail="该项目没有可修改的 HTML")

    requirement = req.prompt.strip()
    # 同步模型调用放线程池，避免阻塞事件循环
    new_html, reason = await asyncio.to_thread(_call_llm_iterate, current_html, requirement)
    if not new_html:
        # 修改失败不写回，原版本保持不变；把具体原因带给前端
        raise HTTPException(
            status_code=502,
            detail=f"修改失败：{reason or '大模型暂时不可用'}。原版本未受影响，可稍后重试",
        )

    await db.update_project_html(project_id, new_html)
    await db.insert_message(project_id, "user", requirement)
    await db.insert_message(project_id, "assistant", new_html)

    return {"project_id": project_id, "html": new_html, "mode": "llm"}


@app.post("/api/generate_race")
async def api_generate_race(req: GenerateRequest):
    """赛马模式接口：并发 3 路生成，返回 results 数组（单路失败不影响其余）"""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")

    prompt = req.prompt.strip()
    logger.info(f"赛马模式: 并发 {len(RACE_VARIANTS)} 路生成 (prompt={prompt[:50]}...)")
    results = await generate_race(prompt)
    ok_count = sum(1 for r in results if r["ok"])
    logger.info(f"赛马完成: {ok_count}/{len(RACE_VARIANTS)} 路成功")

    # 3 路全部落库：生成即建项目（final_html 留空），用户点选后再回填同一项目
    project_id = None
    if db.ENABLED:
        versions = [
            {"label": r["label"], "ok": r["ok"], "html": r["html"], "error": r["error"]}
            for r in results
        ]
        row = await db.insert_project(prompt, None, versions)
        if row:
            project_id = row.get("id")
            await db.insert_message(project_id, "user", prompt)
            summary = "赛马模式生成 %d 路（%d 路成功）：%s。请选择最终版本。" % (
                len(results), ok_count,
                "、".join(
                    f"{'ABC'[i]}·{r['label']}" + ("（失败）" if not r["ok"] else "")
                    for i, r in enumerate(results)
                ),
            )
            await db.insert_message(project_id, "assistant", summary)

    return JSONResponse({
        "prompt": prompt,
        "mode": "race",
        "llm_configured": bool(LLM_API_KEY),
        "ok_count": ok_count,
        "project_id": project_id,
        "results": results,
    })


@app.get("/api/health")
async def api_health():
    """健康检查 + 当前模式"""
    return {
        "status": "ok",
        "llm_configured": bool(LLM_API_KEY),
        "supabase_configured": db.ENABLED,
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
    logger.info(f"Supabase : {'已配置（持久化开启）' if db.ENABLED else '未配置（历史/连续修改不可用）'}")
    logger.info(f"=====================================")
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
