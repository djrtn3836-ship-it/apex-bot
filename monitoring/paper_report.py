"""APEX BOT -   24   
DB trade_history / daily_performance / signal_log  
HTML + JSON +    ."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import get_settings
from utils.helpers import now_kst

def _safe_parse_timestamp(df):
    """(T /  )"""
    if 'timestamp' in df.columns:
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
        except Exception:
            try:
                df['timestamp'] = pd.to_datetime(df['timestamp'], infer_datetime_format=True)
            except Exception:
                df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    return df


# ── 색상 팔레트 (HTML용) ──────────────────────────────────────────
COLORS = {
    "bg":        "#0a0e1a",
    "card":      "#141928",
    "border":    "#2d3654",
    "header":    "#1a1f35",
    "accent":    "#00d4ff",
    "green":     "#00ff88",
    "red":       "#ff4757",
    "text":      "#e0e6f0",
    "muted":     "#8892b0",
    "grad1":     "#1a1f35",
    "grad2":     "#252d4a",
}


class PaperReport:
    """& HTML  

    :
        report = PaperReport()
        report.generate()          # reports/  HTML + JSON 
        report.print_summary()     #"""

    VERSION = "3.0.0"

    def __init__(self, db_path: Optional[str] = None,
                 output_dir: Optional[str] = None,
                 hours: int = 24):
        settings = get_settings()
        self.db_path = db_path or str(
            Path(settings.database.db_path).resolve()
        )
        self.output_dir = Path(output_dir or "reports/paper")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.hours = hours                        # 분석 기간 (기본 24h)
        self._since: datetime = now_kst() - timedelta(hours=hours)
        self._now:   datetime = now_kst()

    # ================================================================
    #  Public API
    # ================================================================

    def generate(self) -> Dict:
        """generate 실행"""
        logger.info(f" {self.hours}     ...")

        trades     = self._load_trades()
        signals    = self._load_signals()
        daily_perf = self._load_daily_performance()

        metrics  = self._calc_metrics(trades, daily_perf)
        strategy = self._calc_strategy_breakdown(trades)
        coin     = self._calc_coin_breakdown(trades)
        hourly   = self._calc_hourly_pnl(trades)
        drawdown = self._calc_drawdown(trades, metrics["initial_capital"])

        data = {
            "meta": {
                "version":    self.VERSION,
                "hours":      self.hours,
                "since":      self._since.isoformat(),
                "until":      self._now.isoformat(),
                "generated":  self._now.strftime("%Y-%m-%d %H:%M:%S KST"),
                "mode":       "PAPER",
            },
            "metrics":           metrics,
            "strategy_breakdown": strategy,
            "coin_breakdown":    coin,
            "hourly_pnl":        hourly,
            "drawdown_series":   drawdown,
            "recent_trades":     self._serialize_trades(trades.tail(30)),
            "signal_stats":      self._calc_signal_stats(signals),
        }

        ts        = self._now.strftime("%Y%m%d_%H%M%S")
        json_path = self.output_dir / f"paper_report_{ts}.json"
        html_path = self.output_dir / f"paper_report_{ts}.html"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        self._generate_html(data, html_path)
        self.print_summary(data)

        logger.success(f"✅ 리포트 생성 완료\n   JSON: {json_path}\n   HTML: {html_path}")
        return data

    def print_summary(self, data: Optional[Dict] = None) -> None:
        """print_summary 실행"""
        if data is None:
            trades     = self._load_trades()
            daily_perf = self._load_daily_performance()
            metrics    = self._calc_metrics(trades, daily_perf)
            data       = {"metrics": metrics,
                          "strategy_breakdown": self._calc_strategy_breakdown(trades),
                          "coin_breakdown": self._calc_coin_breakdown(trades)}

        m   = data["metrics"]
        sep = "=" * 60
        pnl_sign = "+" if m["total_pnl_pct"] >= 0 else ""

        print(f"\n{sep}")
        print(f"   APEX BOT    {self.hours} ")
        print(sep)
        print(f"     : {data['meta']['since'][:16] if 'meta' in data else ''}"
              f"  →  {data['meta']['until'][:16] if 'meta' in data else ''}")
        print(f"   : ₩{m['initial_capital']:>15,.0f}")
        print(f"   : ₩{m['current_capital']:>15,.0f}  "
              f"({pnl_sign}{m['total_pnl_pct']:.2f}%)")
        print(f"     : ₩{m['total_pnl_krw']:>+15,.0f}")
        print(sep)
        print(f"     : {m['total_trades']:>5}  "
              f"( {m['buy_count']} /  {m['sell_count']})")
        print(f"         : {m['win_rate']:>6.1f}%  "
              f"( {m['win_count']} /  {m['loss_count']})")
        print(f"     : {m['avg_win_pct']:>+6.2f}%  /  "
              f"  : {m['avg_loss_pct']:>+6.2f}%")
        print(f"     : {m['profit_factor']:>6.2f}  "
              f": {m['expectancy']:>+6.4f}")
        print(f"     : {m['sharpe_ratio']:>6.3f}  "
              f": {m['sortino_ratio']:>6.3f}")
        print(f"   : {m['max_drawdown_pct']:>5.2f}%")
        print(f"     : ₩{m['total_fees_krw']:>10,.0f}")
        print(sep)

        # 전략별
        sb = data.get("strategy_breakdown", {})
        if sb:
            print("  [ ]")
            for sname, sv in sorted(sb.items(),
                                    key=lambda x: x[1].get("pnl_pct", 0),
                                    reverse=True):
                bar = "▲" if sv["pnl_pct"] >= 0 else "▼"
                print(f"    {bar} {sname:<22} "
                      f":{sv['trades']:>3}  "
                      f":{sv['win_rate']:>5.1f}%  "
                      f":{sv['pnl_pct']:>+6.2f}%")

        # 코인별
        cb = data.get("coin_breakdown", {})
        if cb:
            print("  [ ]")
            for cname, cv in sorted(cb.items(),
                                    key=lambda x: x[1].get("pnl_pct", 0),
                                    reverse=True):
                bar = "▲" if cv["pnl_pct"] >= 0 else "▼"
                print(f"    {bar} {cname:<12} "
                      f":{cv['trades']:>3}  "
                      f":{cv['win_rate']:>5.1f}%  "
                      f":{cv['pnl_pct']:>+6.2f}%")
        print(sep + "\n")

    # ================================================================
    #  DB Loaders
    # ================================================================

    def _load_trades(self) -> pd.DataFrame:
        try:
            conn = sqlite3.connect(self.db_path)
            df   = pd.read_sql_query(
                """SELECT * FROM trade_history
                   WHERE timestamp >= ? AND mode = 'paper'
                   ORDER BY timestamp ASC""",
                conn,
                params=[self._since.strftime("%Y-%m-%d %H:%M:%S")],
            )
            conn.close()
            if df.empty:
                return df
            df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
            df["profit_rate"] = pd.to_numeric(df["profit_rate"], errors="coerce").fillna(0)
            df["amount_krw"]  = pd.to_numeric(df["amount_krw"],  errors="coerce").fillna(0)
            df["fee"]         = pd.to_numeric(df["fee"],          errors="coerce").fillna(0)
            return df
        except Exception as e:
            logger.warning(f"trade_history  : {e}")
            return pd.DataFrame()

    def _load_signals(self) -> pd.DataFrame:
        try:
            conn = sqlite3.connect(self.db_path)
            df   = pd.read_sql_query(
                """SELECT * FROM signal_log
                   WHERE timestamp >= ?
                   ORDER BY timestamp ASC""",
                conn,
                params=[self._since.strftime("%Y-%m-%d %H:%M:%S")],
            )
            conn.close()
            return df
        except Exception as e:
            logger.warning(f"signal_log  : {e}")
            return pd.DataFrame()

    def _load_daily_performance(self) -> pd.DataFrame:
        try:
            conn = sqlite3.connect(self.db_path)
            df   = pd.read_sql_query(
                "SELECT * FROM daily_performance ORDER BY date DESC LIMIT 30",
                conn,
            )
            conn.close()
            return df
        except Exception as e:
            logger.warning(f"daily_performance  : {e}")
            return pd.DataFrame()

    # ================================================================
    #  Metric Calculations
    # ================================================================

    def _calc_metrics(self, trades: pd.DataFrame,
                      daily_perf: pd.DataFrame) -> Dict:
        settings = get_settings()
        # initial_capital은 upbit_adapter 페이퍼 시작값 (100만원 기본)
        initial  = 1_000_000.0

        if trades.empty:
            return self._empty_metrics(initial)

        sell_trades = trades[trades["side"] == "SELL"]
        buy_trades  = trades[trades["side"] == "BUY"]

        returns = sell_trades["profit_rate"].tolist()  # %
        wins    = [r for r in returns if r > 0]
        losses  = [r for r in returns if r < 0]

        win_rate     = len(wins) / len(returns) * 100 if returns else 0
        avg_win      = float(np.mean(wins))  if wins   else 0.0
        avg_loss     = float(np.mean(losses)) if losses else 0.0
        profit_factor = (
            (sum(wins) / abs(sum(losses))) if losses and wins else
            (float("inf") if wins else 0.0)
        )
        expectancy = (win_rate / 100 * avg_win
                      - (1 - win_rate / 100) * abs(avg_loss))

        # 샤프 / 소르티노
        r_arr     = np.array(returns) / 100
        sharpe    = float(r_arr.mean() / r_arr.std() * math.sqrt(365)
                          if r_arr.std() > 0 else 0.0)
        down_arr  = r_arr[r_arr < 0]
        sortino   = float(r_arr.mean() / down_arr.std() * math.sqrt(365)
                          if len(down_arr) > 0 and down_arr.std() > 0 else 0.0)

        # 총 손익
        total_pnl_krw = float(sell_trades["amount_krw"].sum() * 0 +
                               sell_trades.apply(
                                   lambda row: row["amount_krw"] * row["profit_rate"] / 100, axis=1
                               ).sum())
        current_capital = initial + total_pnl_krw
        total_pnl_pct   = (total_pnl_krw / initial * 100) if initial > 0 else 0

        # 최대 드로다운 (daily_performance 사용)
        max_dd = 0.0
        if not daily_perf.empty and "max_drawdown" in daily_perf.columns:
            max_dd = float(daily_perf["max_drawdown"].max() or 0)
        elif returns:
            equity = initial
            peak   = initial
            for r in returns:
                equity *= (1 + r / 100)
                peak    = max(peak, equity)
                dd      = (equity - peak) / peak * 100
                max_dd  = min(max_dd, dd)
            max_dd = abs(max_dd)

        # 연속 승/패
        max_cw = max_cl = cw = cl = 0
        for r in returns:
            if r > 0:
                cw += 1; cl = 0; max_cw = max(max_cw, cw)
            else:
                cl += 1; cw = 0; max_cl = max(max_cl, cl)

        return {
            "initial_capital":   initial,
            "current_capital":   current_capital,
            "total_pnl_krw":     total_pnl_krw,
            "total_pnl_pct":     total_pnl_pct,
            "total_trades":      len(trades),
            "buy_count":         len(buy_trades),
            "sell_count":        len(sell_trades),
            "win_count":         len(wins),
            "loss_count":        len(losses),
            "win_rate":          win_rate,
            "avg_win_pct":       avg_win,
            "avg_loss_pct":      avg_loss,
            "profit_factor":     min(profit_factor, 999.0),
            "expectancy":        expectancy,
            "sharpe_ratio":      sharpe,
            "sortino_ratio":     sortino,
            "max_drawdown_pct":  max_dd,
            "max_consec_wins":   max_cw,
            "max_consec_losses": max_cl,
            "total_fees_krw":    float(trades["fee"].sum()),
        }

    def _empty_metrics(self, initial: float) -> Dict:
        return {
            "initial_capital": initial, "current_capital": initial,
            "total_pnl_krw": 0, "total_pnl_pct": 0,
            "total_trades": 0, "buy_count": 0, "sell_count": 0,
            "win_count": 0, "loss_count": 0, "win_rate": 0,
            "avg_win_pct": 0, "avg_loss_pct": 0,
            "profit_factor": 0, "expectancy": 0,
            "sharpe_ratio": 0, "sortino_ratio": 0,
            "max_drawdown_pct": 0, "max_consec_wins": 0,
            "max_consec_losses": 0, "total_fees_krw": 0,
        }

    def _calc_strategy_breakdown(self, trades: pd.DataFrame) -> Dict:
        if trades.empty or "strategy" not in trades.columns:
            return {}
        result = {}
        for strat, grp in trades.groupby("strategy"):
            sells  = grp[grp["side"] == "SELL"]
            rets   = sells["profit_rate"].tolist()
            wins   = [r for r in rets if r > 0]
            losses = [r for r in rets if r < 0]
            pnl    = float(sells.apply(
                lambda r: r["amount_krw"] * r["profit_rate"] / 100, axis=1
            ).sum()) if len(sells) else 0
            result[str(strat)] = {
                "trades":    len(grp),
                "win_rate":  len(wins) / len(rets) * 100 if rets else 0,
                "pnl_krw":   pnl,
                "pnl_pct":   float(np.mean(rets)) if rets else 0,
                "avg_win":   float(np.mean(wins))   if wins   else 0,
                "avg_loss":  float(np.mean(losses)) if losses else 0,
            }
        return result

    def _calc_coin_breakdown(self, trades: pd.DataFrame) -> Dict:
        if trades.empty:
            return {}
        result = {}
        for market, grp in trades.groupby("market"):
            sells  = grp[grp["side"] == "SELL"]
            rets   = sells["profit_rate"].tolist()
            wins   = [r for r in rets if r > 0]
            pnl    = float(sells.apply(
                lambda r: r["amount_krw"] * r["profit_rate"] / 100, axis=1
            ).sum()) if len(sells) else 0
            coin   = str(market).replace("KRW-", "")
            result[coin] = {
                "trades":   len(grp),
                "win_rate": len(wins) / len(rets) * 100 if rets else 0,
                "pnl_krw":  pnl,
                "pnl_pct":  float(np.mean(rets)) if rets else 0,
            }
        return result

    def _calc_hourly_pnl(self, trades: pd.DataFrame) -> List[Dict]:
        if trades.empty:
            return []
        sells = trades[trades["side"] == "SELL"].copy()
        if sells.empty:
            return []
        sells["hour"] = sells["timestamp"].dt.strftime("%Y-%m-%d %H:00")
        hourly = []
        for h, grp in sells.groupby("hour"):
            pnl = float(grp.apply(
                lambda r: r["amount_krw"] * r["profit_rate"] / 100, axis=1
            ).sum())
            hourly.append({
                "hour": str(h),
                "pnl_krw": pnl,
                "trades":  len(grp),
            })
        return hourly

    def _calc_drawdown(self, trades: pd.DataFrame,
                       initial: float) -> List[Dict]:
        sells = trades[trades["side"] == "SELL"] if not trades.empty else trades
        if sells.empty:
            return []
        equity = initial
        peak   = initial
        series = []
        for _, row in sells.iterrows():
            equity += row["amount_krw"] * row["profit_rate"] / 100
            peak    = max(peak, equity)
            dd      = (equity - peak) / peak * 100
            series.append({
                "ts":     row["timestamp"].isoformat(),
                "equity": round(equity, 0),
                "dd_pct": round(dd, 4),
            })
        return series

    def _calc_signal_stats(self, signals: pd.DataFrame) -> Dict:
        if signals.empty:
            return {"total": 0, "executed": 0, "execution_rate": 0,
                    "by_type": {}, "by_market": {}}
        total    = len(signals)
        executed = int(signals["executed"].sum()) if "executed" in signals.columns else 0
        by_type  = {}
        if "signal_type" in signals.columns:
            for st, grp in signals.groupby("signal_type"):
                by_type[str(st)] = int(len(grp))
        by_market = {}
        if "market" in signals.columns:
            for m, grp in signals.groupby("market"):
                by_market[str(m).replace("KRW-", "")] = int(len(grp))
        return {
            "total":          total,
            "executed":       executed,
            "execution_rate": executed / total * 100 if total else 0,
            "by_type":        by_type,
            "by_market":      by_market,
        }

    def _serialize_trades(self, df: pd.DataFrame) -> List[Dict]:
        if df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "timestamp":  str(r.get("timestamp", "")),
                "market":     str(r.get("market", "")),
                "side":       str(r.get("side", "")),
                "price":      float(r.get("price", 0)),
                "amount_krw": float(r.get("amount_krw", 0)),
                "profit_rate": float(r.get("profit_rate", 0)),
                "strategy":   str(r.get("strategy", "")),
                "reason":     str(r.get("reason", "")),
            })
        return rows

    # ================================================================
    #  HTML Generation
    # ================================================================

    def _generate_html(self, data: Dict, path: Path) -> None:
        m    = data["metrics"]
        meta = data["meta"]

        # ── 카드 색상 헬퍼 ──────────────────────────────────────────
        def color(val):
            return COLORS["green"] if val >= 0 else COLORS["red"]

        def fmt_pct(val, show_sign=True):
            sign = "+" if val >= 0 and show_sign else ""
            return f"{sign}{val:.2f}%"

        def fmt_krw(val, show_sign=False):
            sign = "+" if val >= 0 and show_sign else ""
            return f"₩{sign}{int(val):,}"

        # ── 차트 데이터 ─────────────────────────────────────────────
        dd_ser = data.get("drawdown_series", [])
        equity_labels = json.dumps(
            [x["ts"][-8:-3] for x in dd_ser[-72:]])          # 최대 72포인트
        equity_values = json.dumps(
            [x["equity"] for x in dd_ser[-72:]])
        dd_values     = json.dumps(
            [x["dd_pct"] for x in dd_ser[-72:]])

        hourly = data.get("hourly_pnl", [])
        hourly_labels = json.dumps([x["hour"][-5:] for x in hourly])
        hourly_values = json.dumps([x["pnl_krw"]   for x in hourly])
        hourly_colors = json.dumps(
            [COLORS["green"] if x["pnl_krw"] >= 0 else COLORS["red"]
             for x in hourly])

        # ── 전략별 테이블 행 ─────────────────────────────────────────
        def strategy_rows():
            sb = data.get("strategy_breakdown", {})
            if not sb:
                return '<tr><td colspan="6" style="text-align:center;color:#8892b0">데이터 없음</td></tr>'
            rows = ""
            for sname, sv in sorted(sb.items(),
                                    key=lambda x: x[1].get("pnl_pct", 0),
                                    reverse=True):
                c = color(sv["pnl_pct"])
                rows += (
                    f'<tr><td>{sname}</td>'
                    f'<td>{sv["trades"]}</td>'
                    f'<td>{sv["win_rate"]:.1f}%</td>'
                    f'<td style="color:{c}">{fmt_pct(sv["pnl_pct"])}</td>'
                    f'<td style="color:{color(sv["avg_win"])}">{fmt_pct(sv["avg_win"])}</td>'
                    f'<td style="color:{color(sv["avg_loss"])}">{fmt_pct(sv["avg_loss"])}</td></tr>'
                )
            return rows

        # ── 코인별 테이블 행 ─────────────────────────────────────────
        def coin_rows():
            cb = data.get("coin_breakdown", {})
            if not cb:
                return '<tr><td colspan="4" style="text-align:center;color:#8892b0">데이터 없음</td></tr>'
            rows = ""
            for cname, cv in sorted(cb.items(),
                                    key=lambda x: x[1].get("pnl_pct", 0),
                                    reverse=True):
                c = color(cv["pnl_pct"])
                rows += (
                    f'<tr><td>{cname}</td>'
                    f'<td>{cv["trades"]}</td>'
                    f'<td>{cv["win_rate"]:.1f}%</td>'
                    f'<td style="color:{c}">{fmt_pct(cv["pnl_pct"])}</td></tr>'
                )
            return rows

        # ── 거래 내역 행 ─────────────────────────────────────────────
        def trade_rows():
            trades = data.get("recent_trades", [])
            if not trades:
                return '<tr><td colspan="7" style="text-align:center;color:#8892b0">거래 없음</td></tr>'
            rows = ""
            for t in reversed(trades):
                side_label = "매수" if t["side"] == "BUY" else "매도"
                side_col   = COLORS["green"] if t["side"] == "BUY" else COLORS["red"]
                pnl_col    = color(t["profit_rate"])
                ts         = str(t["timestamp"])[-8:-3] if len(str(t["timestamp"])) > 8 else str(t["timestamp"])
                rows += (
                    f'<tr>'
                    f'<td>{ts}</td>'
                    f'<td>{str(t["market"]).replace("KRW-","")}</td>'
                    f'<td style="color:{side_col}">{side_label}</td>'
                    f'<td>₩{int(t["price"]):,}</td>'
                    f'<td>₩{int(t["amount_krw"]):,}</td>'
                    f'<td style="color:{pnl_col}">{fmt_pct(t["profit_rate"])}</td>'
                    f'<td>{t["strategy"]}</td>'
                    f'</tr>'
                )
            return rows

        # ── 신호 통계 ────────────────────────────────────────────────
        ss = data.get("signal_stats", {})

        pnl_color   = color(m["total_pnl_pct"])
        pf_str      = f"{m['profit_factor']:.2f}" if m["profit_factor"] < 100 else "∞"
        grade, grade_color, grade_desc = self._grade(m)

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>APEX BOT {meta['hours']}h  · {meta['generated']}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{COLORS['bg']};color:{COLORS['text']};font-family:'Segoe UI',sans-serif;font-size:14px}}
a{{color:{COLORS['accent']};text-decoration:none}}
.header{{background:linear-gradient(135deg,{COLORS['grad1']},{COLORS['grad2']});
         padding:24px 40px;border-bottom:2px solid {COLORS['border']};
         display:flex;align-items:center;gap:20px}}
.header h1{{color:{COLORS['accent']};font-size:26px;letter-spacing:1px}}
.header .badge{{background:{COLORS['accent']};color:#000;padding:4px 14px;
               border-radius:20px;font-size:12px;font-weight:700}}
.header .meta{{color:{COLORS['muted']};font-size:13px;margin-left:auto;text-align:right}}
.section{{padding:24px 40px}}
.section-title{{color:{COLORS['muted']};font-size:11px;text-transform:uppercase;
               letter-spacing:2px;margin-bottom:16px;border-bottom:1px solid {COLORS['border']};
               padding-bottom:8px}}
.grid-4{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px}}
.grid-3{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:16px}}
.grid-2{{display:grid;grid-template-columns:repeat(2,1fr);gap:20px;margin-bottom:16px}}
.card{{background:{COLORS['card']};border:1px solid {COLORS['border']};
       border-radius:14px;padding:20px;transition:border-color .2s}}
.card:hover{{border-color:{COLORS['accent']}40}}
.card .label{{color:{COLORS['muted']};font-size:11px;text-transform:uppercase;
              letter-spacing:1px;margin-bottom:6px}}
.card .value{{font-size:26px;font-weight:700;line-height:1.1}}
.card .sub{{color:{COLORS['muted']};font-size:11px;margin-top:4px}}
.grade-card{{background:linear-gradient(135deg,{COLORS['card']},{COLORS['header']});
             border:2px solid {grade_color};border-radius:14px;padding:24px;text-align:center}}
.grade-card .grade{{font-size:52px;font-weight:900;color:{grade_color}}}
.grade-card .grade-desc{{color:{COLORS['muted']};font-size:13px;margin-top:6px}}
.chart-card{{background:{COLORS['card']};border:1px solid {COLORS['border']};
             border-radius:14px;padding:20px}}
.chart-card .chart-title{{color:{COLORS['muted']};font-size:11px;text-transform:uppercase;
                           letter-spacing:1px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;background:{COLORS['card']};
       border-radius:12px;overflow:hidden}}
th{{background:{COLORS['header']};padding:10px 14px;text-align:left;
    color:{COLORS['muted']};font-size:11px;text-transform:uppercase;letter-spacing:1px}}
td{{padding:10px 14px;border-bottom:1px solid {COLORS['border']};font-size:13px}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:{COLORS['header']}80}}
.footer{{padding:16px 40px;color:{COLORS['muted']};font-size:11px;
         border-top:1px solid {COLORS['border']};text-align:center}}
@media(max-width:900px){{
  .grid-4,.grid-3{{grid-template-columns:repeat(2,1fr)}}
  .grid-2{{grid-template-columns:1fr}}
  .section{{padding:16px 16px}}
  .header{{padding:16px}}
}}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div>
    <h1> APEX BOT</h1>
    <div style="color:{COLORS['muted']};font-size:13px;margin-top:4px">
        {meta['hours']}  
    </div>
  </div>
  <span class="badge">PAPER MODE</span>
  <div class="meta">
     : {meta['generated']}<br>
     : {meta['since'][:16]}  →  {meta['until'][:16]}
  </div>
</div>

<!--   -->
<div class="section">
  <div class="section-title">  </div>
  <div class="grid-4">

    <div class="grade-card">
      <div class="grade">{grade}</div>
      <div style="color:{grade_color};font-size:15px;font-weight:700;margin-top:4px">
        {grade_desc}
      </div>
      <div class="grade-desc">  (··DD )</div>
    </div>

    <div class="card">
      <div class="label"> </div>
      <div class="value" style="color:{pnl_color}">
        {fmt_pct(m['total_pnl_pct'], show_sign=True)}
      </div>
      <div class="sub">{fmt_krw(m['total_pnl_krw'], show_sign=True)}</div>
    </div>

    <div class="card">
      <div class="label"> </div>
      <div class="value">{fmt_krw(m['current_capital'])}</div>
      <div class="sub">: {fmt_krw(m['initial_capital'])}</div>
    </div>

    <div class="card">
      <div class="label"> </div>
      <div class="value" style="color:{COLORS['red']}">
        -{m['max_drawdown_pct']:.2f}%
      </div>
      <div class="sub"> : -10%</div>
    </div>

  </div>

  <div class="grid-4">
    <div class="card">
      <div class="label"> </div>
      <div class="value">{m['total_trades']}</div>
      <div class="sub"> {m['buy_count']} /  {m['sell_count']}</div>
    </div>
    <div class="card">
      <div class="label"></div>
      <div class="value" style="color:{'#00ff88' if m['win_rate']>=50 else '#ff4757'}">
        {m['win_rate']:.1f}%
      </div>
      <div class="sub"> {m['win_count']} /  {m['loss_count']}</div>
    </div>
    <div class="card">
      <div class="label"> </div>
      <div class="value" style="color:{'#00ff88' if m['sharpe_ratio']>=1 else '#ff4757'}">
        {m['sharpe_ratio']:.3f}
      </div>
      <div class="sub"> : {m['sortino_ratio']:.3f}</div>
    </div>
    <div class="card">
      <div class="label"> (PF)</div>
      <div class="value" style="color:{'#00ff88' if m['profit_factor']>=1 else '#ff4757'}">
        {pf_str}
      </div>
      <div class="sub"> : {m['expectancy']:+.4f}</div>
    </div>
  </div>

  <div class="grid-4">
    <div class="card">
      <div class="label">  </div>
      <div class="value" style="color:{COLORS['green']}">{m['avg_win_pct']:+.2f}%</div>
    </div>
    <div class="card">
      <div class="label">  </div>
      <div class="value" style="color:{COLORS['red']}">{m['avg_loss_pct']:+.2f}%</div>
    </div>
    <div class="card">
      <div class="label">  </div>
      <div class="value" style="color:{COLORS['green']}">{m['max_consec_wins']}</div>
    </div>
    <div class="card">
      <div class="label">  </div>
      <div class="value" style="color:{COLORS['red']}">{m['max_consec_losses']}</div>
    </div>
  </div>
</div>

<!--  -->
<div class="section">
  <div class="section-title"> &  </div>
  <div class="grid-2">
    <div class="chart-card">
      <div class="chart-title">   </div>
      <canvas id="equityChart" height="200"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-title">  </div>
      <canvas id="ddChart" height="200"></canvas>
    </div>
  </div>
  <div class="chart-card" style="margin-top:14px">
    <div class="chart-title">⏱   (KRW)</div>
    <canvas id="hourlyChart" height="120"></canvas>
  </div>
</div>

<!--   -->
<div class="section">
  <div class="section-title">  </div>
  <table>
    <thead>
      <tr><th></th><th></th><th></th>
          <th> </th><th> </th><th> </th></tr>
    </thead>
    <tbody>{strategy_rows()}</tbody>
  </table>
</div>

<!--   -->
<div class="section">
  <div class="section-title">  </div>
  <table>
    <thead>
      <tr><th></th><th></th><th></th><th> </th></tr>
    </thead>
    <tbody>{coin_rows()}</tbody>
  </table>
</div>

<!--   -->
<div class="section">
  <div class="section-title"> &  </div>
  <div class="grid-4">
    <div class="card">
      <div class="label">  </div>
      <div class="value">{ss.get('total', 0)}</div>
    </div>
    <div class="card">
      <div class="label"> </div>
      <div class="value">{ss.get('executed', 0)}</div>
    </div>
    <div class="card">
      <div class="label"> </div>
      <div class="value">{ss.get('execution_rate', 0):.1f}%</div>
    </div>
    <div class="card">
      <div class="label"> </div>
      <div class="value" style="color:{COLORS['red']}">
        ₩{int(m['total_fees_krw']):,}
      </div>
    </div>
  </div>
</div>

<!--    -->
<div class="section">
  <div class="section-title">   ( 30)</div>
  <table>
    <thead>
      <tr><th></th><th></th><th></th><th></th>
          <th></th><th></th><th></th></tr>
    </thead>
    <tbody>{trade_rows()}</tbody>
  </table>
</div>

<div class="footer">
   APEX BOT v{self.VERSION} ·    · {meta['generated']} ·
         .
</div>

<script>
//     
const commonOpts = {{
  responsive: true,
  animation: {{ duration: 600 }},
  plugins: {{
    legend: {{ labels: {{ color: '{COLORS['muted']}', font: {{ size: 11 }} }} }},
    tooltip: {{ callbacks: {{ label: ctx => ' ' + ctx.formattedValue }} }},
  }},
  scales: {{
    x: {{ ticks: {{ color: '{COLORS['muted']}', maxTicksLimit: 10 }},
         grid: {{ color: '{COLORS['border']}' }} }},
    y: {{ ticks: {{ color: '{COLORS['muted']}' }},
         grid: {{ color: '{COLORS['border']}' }} }},
  }},
}};

//    
const eqLabels = {equity_labels};
const eqValues = {equity_values};
if (eqValues.length > 0) {{
  new Chart(document.getElementById('equityChart'), {{
    type: 'line',
    data: {{
      labels: eqLabels,
      datasets: [{{
        label: ' (₩)',
        data: eqValues,
        borderColor: '{COLORS['accent']}',
        backgroundColor: '{COLORS['accent']}18',
        borderWidth: 2, pointRadius: 0, fill: true, tension: 0.3,
      }}]
    }},
    options: {{ ...commonOpts }},
  }});
}} else {{
  document.getElementById('equityChart').parentElement.innerHTML +=
    '<p style="color:#8892b0;text-align:center;margin-top:20px">   </p>';
}}

//   
const ddValues = {dd_values};
if (ddValues.length > 0) {{
  new Chart(document.getElementById('ddChart'), {{
    type: 'line',
    data: {{
      labels: eqLabels,
      datasets: [{{
        label: ' (%)',
        data: ddValues,
        borderColor: '{COLORS['red']}',
        backgroundColor: '{COLORS['red']}18',
        borderWidth: 2, pointRadius: 0, fill: true, tension: 0.3,
      }}]
    }},
    options: {{ ...commonOpts }},
  }});
}} else {{
  document.getElementById('ddChart').parentElement.innerHTML +=
    '<p style="color:#8892b0;text-align:center;margin-top:20px">  </p>';
}}

//    
const hLabels = {hourly_labels};
const hValues = {hourly_values};
const hColors = {hourly_colors};
if (hValues.length > 0) {{
  new Chart(document.getElementById('hourlyChart'), {{
    type: 'bar',
    data: {{
      labels: hLabels,
      datasets: [{{
        label: '  (₩)',
        data: hValues,
        backgroundColor: hColors,
        borderRadius: 4,
      }}]
    }},
    options: {{ ...commonOpts }},
  }});
}} else {{
  document.getElementById('hourlyChart').parentElement.innerHTML +=
    '<p style="color:#8892b0;text-align:center;margin-top:20px">   </p>';
}}
</script>
</body>
</html>"""

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f" HTML : {path}")

    # ================================================================
    #  Grade
    # ================================================================

    def _grade(self, m: Dict) -> Tuple[str, str, str]:
        """··"""
        score = 0
        score += min(m["sharpe_ratio"] * 20, 30)         # 샤프 (max 30)
        score += min(m["win_rate"] * 0.4, 20)            # 승률 (max 20)
        score += max(0, 20 - m["max_drawdown_pct"] * 2)  # DD (max 20)
        pf     = min(m["profit_factor"], 5)
        score += min(pf * 4, 20)                          # PF (max 20)
        score += min(m["total_pnl_pct"] * 2, 10)         # 수익률 (max 10)

        if   score >= 80: return "S", COLORS["accent"],  "탁월한 성과"
        elif score >= 65: return "A", COLORS["green"],   "우수한 성과"
        elif score >= 50: return "B", "#f9ca24",         "양호한 성과"
        elif score >= 35: return "C", "#f0932b",         "보통 성과"
        elif score >= 20: return "D", COLORS["red"],     "미흡한 성과"
        else:             return "F", "#c0392b",         "성과 부진"


# ── CLI / 스케줄러 진입점 ────────────────────────────────────────────

def generate_paper_report(hours: int = 24,
                          db_path: Optional[str] = None,
                          output_dir: Optional[str] = None) -> Dict:
    """& CLI"""
    report = PaperReport(db_path=db_path, output_dir=output_dir, hours=hours)
    return report.generate()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="APEX BOT 페이퍼 트레이딩 리포트")
    parser.add_argument("--hours",      type=int, default=24,
                        help="분석 기간(시간), 기본 24")
    parser.add_argument("--db",         type=str, default=None,
                        help="DB 경로 (기본: settings에서 자동)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="출력 폴더 (기본: reports/paper)")
    parser.add_argument("--no-file",    action="store_true",
                        help="파일 저장 없이 콘솔 출력만")
    args = parser.parse_args()

    if args.no_file:
        r = PaperReport(db_path=args.db, output_dir=args.output_dir,
                        hours=args.hours)
        r.print_summary()
    else:
        generate_paper_report(hours=args.hours,
                              db_path=args.db,
                              output_dir=args.output_dir)
