from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nicegui import ui

from pipeline_paths import run_path
from ui_backend import (
    available_run_dates,
    backup_before_collection_rerun,
    bulk_exclude_unreviewed,
    finalize_digest,
    read_digest,
    read_review_items,
    read_text,
    save_digest,
    snapshot,
    update_review_item,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
LAGOS_TZ = timezone(timedelta(hours=1))
DEMO_MODE = os.environ.get("NEWS_UI_DEMO") == "1"


def current_run_date() -> str:
    """Return today's pipeline date in Nigeria (UTC+1), evaluated at call time."""
    return datetime.now(LAGOS_TZ).strftime("%Y-%m-%d")
DECISION_LABELS = {
    "include": "纳入",
    "maybe": "备选",
    "exclude": "排除",
    "unreviewed": "未审核",
}
STATUS_LABELS = {
    "complete": ("已完成", "positive"),
    "running": ("进行中", "warning"),
    "error": ("失败", "negative"),
    "unknown": ("未标记", "grey"),
    "pending": ("未开始", "grey"),
}


@dataclass
class Job:
    run_date: str
    kind: str
    label: str
    status: str = "running"
    lines: list[str] = field(default_factory=list)
    error: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: datetime | None = None
    pid: int | None = None

    def append(self, line: str) -> None:
        clean = line.rstrip()
        if clean:
            self.lines.append(clean)
            self.lines = self.lines[-500:]


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def active(self, run_date: str) -> Job | None:
        job = self.jobs.get(run_date)
        return job if job and job.status == "running" else None

    def latest(self, run_date: str) -> Job | None:
        return self.jobs.get(run_date)

    def start(self, run_date: str, kind: str, label: str, commands: list[list[str]]) -> Job:
        if DEMO_MODE:
            raise RuntimeError("演示模式仅使用虚构数据，采集与模型调用已关闭。")
        if self.active(run_date):
            raise RuntimeError("该日期已有任务正在运行。")
        job = Job(run_date=run_date, kind=kind, label=label)
        self.jobs[run_date] = job
        asyncio.create_task(self._run(job, commands))
        return job

    async def _run(self, job: Job, commands: list[list[str]]) -> None:
        log_path = ROOT_DIR / "logs" / f"ui_{job.run_date}_{job.kind}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"[{datetime.now():%Y-%m-%d %H:%M:%S}] START {job.label}\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            for command in commands:
                job.append(f"> {' '.join(command)}")
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(ROOT_DIR),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    creationflags=creationflags,
                )
                job.pid = process.pid
                assert process.stdout is not None
                while line := await process.stdout.readline():
                    decoded = line.decode("utf-8", errors="replace").rstrip()
                    job.append(decoded)
                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(decoded + "\n")
                return_code = await process.wait()
                if return_code != 0:
                    raise RuntimeError(f"命令退出码为 {return_code}: {' '.join(command)}")
            job.status = "complete"
            job.append(f"[{datetime.now():%H:%M:%S}] {job.label}完成")
        except Exception as exc:  # surfaced in the UI and persisted in the UI log
            job.status = "error"
            job.error = str(exc)
            job.append(f"ERROR: {exc}")
        finally:
            job.ended_at = datetime.now()
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"[{job.ended_at:%Y-%m-%d %H:%M:%S}] END status={job.status}\n")


JOBS = JobManager()


def collection_commands(run_date: str) -> list[list[str]]:
    python = str(PYTHON if PYTHON.exists() else Path(sys.executable))
    return [[python, "scripts/collect.py", "--run-date", run_date]]


def generation_commands(run_date: str) -> list[list[str]]:
    python = str(PYTHON if PYTHON.exists() else Path(sys.executable))
    return [
        [python, "src/prepare_llm_input.py", "--run-date", run_date],
        [python, "src/generate_digest.py", "--run-date", run_date],
        [python, "src/pipeline_metrics.py", "--run-date", run_date],
    ]


def status_badge(status: str) -> None:
    label, color = STATUS_LABELS[status]
    ui.badge(label, color=color)


def notify_error(exc: Exception) -> None:
    ui.notify(str(exc), type="negative", multi_line=True, timeout=8000)


def review_score(item: Any) -> float:
    try:
        return float(item.score)
    except (TypeError, ValueError):
        return 0.0


def next_unreviewed_item_id(items: list[Any], current_id: int) -> int | None:
    """Return the next unreviewed item, wrapping once to the start."""
    if not items:
        return None
    ids = [item.id for item in items]
    try:
        current_index = ids.index(current_id)
    except ValueError:
        current_index = -1
    ordered = items[current_index + 1 :] + items[: current_index + 1]
    return next((item.id for item in ordered if item.id != current_id and item.decision == "unreviewed"), None)


def workflow_info(snap: Any, job: Job | None) -> dict[str, Any]:
    if job and job.status == "running" and job.kind == "collection":
        return {"stage": 1, "progress": 0.12, "title": "正在采集今天的新闻", "detail": "系统会自动完成发现、初筛、全文抓取与去重。", "action": "查看运行日志"}
    if snap.collection_status != "complete":
        return {"stage": 1, "progress": 0.0, "title": "从新闻采集开始", "detail": "启动今天的新闻发现、初筛与全文抓取。", "action": "开始今天流程"}
    if snap.review_status != "complete":
        return {"stage": 2, "progress": 0.25, "title": "下一步：审核新闻", "detail": f"还有 {snap.unreviewed} 条新闻需要判断，完成后才能生成摘要。", "action": "继续新闻审核"}
    if job and job.status == "running" and job.kind == "digest":
        return {"stage": 3, "progress": 0.62, "title": "正在生成新闻摘要", "detail": "系统正在整理已纳入新闻，请稍候。", "action": "查看摘要状态"}
    if snap.digest_status != "complete":
        return {"stage": 3, "progress": 0.5, "title": "下一步：生成摘要", "detail": f"审核已完成，已选出 {snap.included + snap.maybe} 条新闻。", "action": "前往生成摘要"}
    if snap.finalized_status != "complete":
        return {"stage": 4, "progress": 0.75, "title": "今天还差最后一步", "detail": "摘要已经生成，检查内容并完成整理定稿。", "action": "打开摘要并定稿"}
    return {"stage": 5, "progress": 1.0, "title": "今天的新闻流程已完成", "detail": "采集、审核、摘要和定稿均已完成。", "action": "查看最终摘要"}


def build_main_page(theme: str = "classic") -> None:
    if DEMO_MODE:
        ui.label("原版网页工作台 · 虚构数据演示 · 可试用审核与编辑；联网采集和模型调用已关闭").classes(
            "w-full px-5 py-2 text-xs bg-amber-100 text-amber-950"
        )
    initial_date = current_run_date()
    state = {
        "run_date": initial_date,
        "known_today": initial_date,
        "review_item_id": None,
        "history_loaded": False,
        "log_code": None,
        "log_scroll": None,
        "log_follow": None,
        "workflow_signature": None,
        "active_tab": "工作台",
    }
    dates = sorted(set([initial_date, *available_run_dates(ROOT_DIR)]), reverse=True)
    if theme == "studio":
        ui.colors(primary="#1f7b5f")

    ui.add_css(
        """
        :root { --ink: #172033; --muted: #69758a; --line: #e3e8ef; --canvas: #f5f7fa; --surface: #fff; --nav: #111c31; --nav-soft: #1c2a43; --accent: #2f6fed; }
        html, body { background: var(--canvas); color: var(--ink); font-family: Inter, "Segoe UI", "Microsoft YaHei UI", sans-serif; }
        body { margin: 0; }
        .nicegui-content { padding: 0 !important; gap: 0 !important; }
        .app-shell { min-height: 100vh; background: var(--canvas); }
        .app-sidebar { width: 224px; min-width: 224px; height: 100vh; position: sticky; top: 0; background: linear-gradient(180deg, #111c31 0%, #0d1728 100%); color: white; padding: 22px 16px 18px; border-right: 1px solid rgba(255,255,255,.04); }
        .brand-mark { width: 38px; height: 38px; border-radius: 11px; display: flex; align-items: center; justify-content: center; background: linear-gradient(145deg, #3b82f6, #1d4ed8); box-shadow: 0 8px 22px rgba(37,99,235,.28); }
        .nav-tabs .q-tabs__content { gap: 6px; }
        .nav-tabs .q-tab { justify-content: flex-start; min-height: 46px; padding: 0 14px; border-radius: 10px; color: #aebbd0; font-weight: 500; }
        .nav-tabs .q-tab--active { background: var(--nav-soft); color: white; }
        .nav-tabs .q-tab__indicator { display: none; }
        .app-main { min-width: 0; min-height: 100vh; max-width: calc(100vw - 224px); }
        .topbar { height: 70px; min-height: 70px; padding: 0 28px; background: rgba(255,255,255,.96); border-bottom: 1px solid var(--line); backdrop-filter: blur(12px); }
        .content-panels { background: transparent !important; }
        .content-panels > .q-panel > div { padding: 24px 28px 34px !important; max-width: 1680px; margin: 0 auto; }
        .q-card { border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 1px 2px rgba(15,23,42,.04) !important; }
        .metric-card { min-width: 0; border: 1px solid var(--line); padding: 15px 17px !important; background: rgba(255,255,255,.88); }
        .step-card { min-width: 0; border: 1px solid var(--line); padding: 16px 17px !important; background: white; }
        .step-card:hover, .metric-card:hover { border-color: #c7d2e1; box-shadow: 0 6px 18px rgba(15,23,42,.06) !important; }
        .step-index { width: 29px; height: 29px; border-radius: 9px; background: #eef4ff; color: var(--accent); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }
        .action-card { background: linear-gradient(135deg, #ffffff 35%, #f3f7ff 100%); }
        .log-output { width: 100%; min-height: 100%; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: 9px; background: #111827; color: #d8e2f0; padding: 14px; font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px; line-height: 1.55; }
        .pipeline-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
        .metrics-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; }
        .review-table thead tr { background: #f8fafc; }
        .review-table th { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .04em; }
        .review-table tbody tr { cursor: pointer; transition: background .15s ease; }
        .review-table tbody tr:hover { background: #eff6ff; }
        .review-detail { position: sticky; top: 16px; max-height: calc(100vh - 118px); overflow-y: auto; background: white; }
        .article-excerpt { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.75; font-size: 14px; border: 1px solid #e8edf4; }
        .editor { background: white; border-radius: 12px; }
        .editor textarea { font-family: Consolas, "Microsoft YaHei UI", monospace !important; line-height: 1.68; font-size: 14px; }
        .section-eyebrow { color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
        .page-title { font-size: 25px; line-height: 1.25; font-weight: 650; letter-spacing: -.02em; color: #111827; }
        .page-subtitle { font-size: 13px; color: var(--muted); }
        .caps { font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
        .workflow-now { border: 1px solid #cfe0ff; background: #eef5ff; }
        .workflow-now .q-linear-progress { border-radius: 999px; }
        .stage-active { border-color: #f4bd4f !important; box-shadow: 0 0 0 3px rgba(244,189,79,.15) !important; }
        .stage-active .step-index { background: #fff3cf; color: #9a6a00; }
        .stage-pulse { width: 8px; height: 8px; border-radius: 50%; background: #e8aa25; animation: stage-pulse 1.8s ease-in-out infinite; }
        @keyframes stage-pulse { 0%,100% { opacity: .45; transform: scale(.85); } 50% { opacity: 1; transform: scale(1); } }
        .atlas-page { min-height: 100vh; padding: 18px; background: #eaf1f4; }
        .atlas-shell { min-height: calc(100vh - 36px); overflow: hidden; border-radius: 22px; background: #f9fbfc; box-shadow: 0 20px 60px rgba(25,54,65,.12); }
        .atlas-topbar { min-height: 84px; padding: 0 26px; background: #0c3b46; color: white; }
        .atlas-brand { font-size: 18px; font-weight: 800; letter-spacing: -.02em; }
        .atlas-brand-accent { color: #ffd166; }
        .atlas-tabs .q-tabs__content { gap: 4px; }
        .atlas-tabs .q-tab { min-height: 40px; border-radius: 9px; color: #8fb0b8; padding: 0 15px; }
        .atlas-tabs .q-tab--active { color: white; background: rgba(255,255,255,.08); }
        .atlas-tabs .q-tab__indicator { display: none; }
        .atlas-date .q-field__control { border-radius: 999px !important; color: white; }
        .atlas-date .q-field__native, .atlas-date .q-field__label, .atlas-date .q-field__marginal { color: white !important; }
        .atlas-content { background: transparent !important; }
        .atlas-content > .q-panel > div { padding: 24px 26px 32px !important; max-width: 1680px; margin: 0 auto; }
        .atlas-hero { border: 0; border-radius: 17px; background: #ffd166; padding: 21px !important; }
        .atlas-hero-title { font-size: 27px; font-weight: 750; letter-spacing: -.035em; color: #0c3b46; text-wrap: balance; }
        .atlas-action { border-radius: 999px !important; background: #0c3b46 !important; color: white !important; }
        .atlas-progress-ring { width: 108px; height: 108px; padding: 12px; border-radius: 50%; display: grid; place-items: center; }
        .atlas-progress-ring-inner { width: 100%; height: 100%; border-radius: 50%; display: grid; place-items: center; background: #ffd166; color: #0c3b46; font-size: 22px; font-weight: 750; font-variant-numeric: tabular-nums; }
        .atlas-pipeline { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 10px; }
        .atlas-stage { min-width: 0; border: 1px solid #d9e4e8; border-left: 4px solid #20a77c; border-radius: 13px; background: white; padding: 15px; }
        .atlas-stage.active { border-left-color: #efb646; border-color: #efc66b; box-shadow: 0 0 0 3px rgba(239,182,70,.13) !important; }
        .atlas-stage.pending { border-left-color: #c9d5da; }
        .atlas-metrics { display: grid; grid-template-columns: repeat(6,minmax(0,1fr)); gap: 1px; border: 1px solid #dce7ea; background: #dce7ea; }
        .atlas-metric { min-width: 0; background: white; padding: 14px 13px; }
        .atlas-log { border: 0; border-radius: 15px; background: #0d2d35; color: white; }
        .atlas-log .log-output { background: #0d2d35; color: #b8cbd0; }
        .atlas-log .q-switch__label { color: #a9c8c2; }
        .studio-page { min-height: 100vh; background: #edf1ef; }
        .studio-sidebar { width: 232px; min-width: 232px; height: 100vh; position: sticky; top: 0; padding: 26px 18px 20px; background: #102c27; color: #f6faf8; border-right: 1px solid rgba(255,255,255,.08); }
        .studio-monogram { width: 42px; height: 42px; border: 1px solid rgba(255,255,255,.28); border-radius: 50%; display: grid; place-items: center; font-family: Georgia, serif; font-size: 14px; font-weight: 700; letter-spacing: -.04em; color: #f4c968; }
        .studio-brand { font-family: "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif; font-size: 16px; font-weight: 720; letter-spacing: -.02em; }
        .studio-nav .q-tabs__content { gap: 7px; }
        .studio-nav .q-tab { justify-content: flex-start; min-height: 48px; padding: 0 14px; border-radius: 8px; color: #a9beb7; font-size: 13px; }
        .studio-nav .q-tab--active { color: #fff; background: rgba(255,255,255,.095); box-shadow: inset 3px 0 #f0bd52; }
        .studio-nav .q-tab__indicator { display: none; }
        .studio-main { min-width: 0; max-width: calc(100vw - 232px); min-height: 100vh; }
        .studio-topbar { min-height: 78px; padding: 0 34px; background: rgba(250,252,251,.94); border-bottom: 1px solid #d9e1dd; backdrop-filter: blur(14px); }
        .studio-date .q-field__control { border-radius: 8px !important; background: #fff; }
        .studio-content { background: transparent !important; }
        .studio-content > .q-panel > div { padding: 30px 34px 42px !important; max-width: 1540px; margin: 0 auto; }
        .studio-kicker { font-family: "Cascadia Mono", Consolas, monospace; font-size: 10px; font-weight: 650; letter-spacing: .14em; color: #547069; text-transform: uppercase; }
        .studio-status { overflow: hidden; border: 1px solid #ced9d4; border-radius: 14px; background: #fbfcfb; box-shadow: 0 12px 32px rgba(16,44,39,.055) !important; }
        .studio-status::before { content: ""; display: block; height: 4px; background: linear-gradient(90deg, #238566 0%, #238566 var(--studio-progress), #e1e7e4 var(--studio-progress), #e1e7e4 100%); }
        .studio-status-body { padding: 26px 28px 24px; }
        .studio-status-title { max-width: 760px; font-family: "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif; font-size: clamp(25px, 2.1vw, 34px); line-height: 1.2; font-weight: 720; letter-spacing: -.035em; color: #142821; text-wrap: balance; }
        .studio-status-copy { max-width: 720px; font-size: 13px; line-height: 1.7; color: #64756f; }
        .studio-page .studio-action { min-height: 42px; padding: 0 18px !important; border-radius: 8px !important; background: #163f35 !important; color: #fff !important; font-weight: 650; box-shadow: 0 7px 16px rgba(22,63,53,.14) !important; }
        .studio-sequence { min-width: 150px; padding-left: 26px; border-left: 1px solid #d9e1dd; }
        .studio-sequence-number { font-family: Georgia, "Times New Roman", serif; font-size: 42px; line-height: 1; letter-spacing: -.06em; color: #173d34; font-variant-numeric: tabular-nums; }
        .studio-rail { position: relative; display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 0; padding: 0 12px; }
        .studio-rail::before { content: ""; position: absolute; top: 20px; left: 13%; right: 13%; height: 1px; background: #ccd7d2; }
        .studio-stage { position: relative; min-width: 0; padding: 0 12px; background: transparent !important; border: 0 !important; box-shadow: none !important; }
        .studio-stage-node { position: relative; z-index: 1; width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; margin-bottom: 13px; border: 1px solid #c8d3ce; background: #f7f9f8; color: #7f918a; font-family: "Cascadia Mono", Consolas, monospace; font-size: 11px; }
        .studio-stage.complete .studio-stage-node { border-color: #238566; background: #238566; color: #fff; }
        .studio-stage.active .studio-stage-node { border: 5px solid #f8e6bc; background: #e4aa31; color: #172d27; box-shadow: 0 0 0 1px #d69b22; }
        .studio-stage-title { font-size: 13px; font-weight: 680; color: #21342e; }
        .studio-stage-detail { min-height: 32px; margin-top: 5px; font-size: 11px; line-height: 1.5; color: #75857f; }
        .studio-stage-state { margin-top: 9px; font-size: 10px; font-weight: 700; letter-spacing: .06em; color: #7c8b86; }
        .studio-stage.active .studio-stage-state { color: #9a6810; }
        .studio-stage.complete .studio-stage-state { color: #238566; }
        .studio-data-strip { border-top: 1px solid #d5dfda; border-bottom: 1px solid #d5dfda; padding: 16px 0; }
        .studio-metrics { display: grid; grid-template-columns: repeat(6,minmax(0,1fr)); }
        .studio-metric { position: relative; min-width: 0; padding: 0 18px; border-right: 1px solid #d8e0dc; }
        .studio-metric:first-child { padding-left: 0; }
        .studio-metric:last-child { border-right: 0; }
        .studio-metric-label { font-size: 10px; color: #74847e; }
        .studio-metric-value { margin-top: 3px; font-family: "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif; font-size: 24px; font-weight: 680; letter-spacing: -.035em; color: #18342c; font-variant-numeric: tabular-nums; }
        .studio-log { border: 0 !important; border-radius: 13px !important; background: #142e29 !important; color: #f0f5f3; box-shadow: 0 14px 34px rgba(16,44,39,.1) !important; }
        .studio-log .log-output { background: #142e29; color: #b9cbc5; }
        .studio-log .q-switch__label { color: #b9cbc5; }
        .studio-page .q-card { border-color: #d5dfda; border-radius: 12px; }
        .studio-page .section-eyebrow, .studio-page .text-primary { color: #1f7b5f !important; }
        .studio-page .bg-primary { background: #1f7b5f !important; }
        .studio-page .review-detail { border-top: 4px solid #1f7b5f; }
        .studio-page .review-table thead tr { background: #f2f5f3; }
        .studio-page .review-table tbody tr:hover { background: #edf6f2; }
        .studio-page .review-table td { vertical-align: top; line-height: 1.45; }
        .review-position { font-variant-numeric: tabular-nums; }
        .q-btn:focus-visible, .q-tab:focus-visible, .q-field:focus-within { outline: 3px solid rgba(47,111,237,.32); outline-offset: 2px; }
        @media (prefers-reduced-motion: reduce) { .stage-pulse { animation: none; } }
        @media (max-width: 1250px) { .pipeline-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .metrics-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
        @media (max-width: 1350px) { .studio-page .review-layout { flex-direction: column; } .studio-page .review-list, .studio-page .review-detail { width: 100% !important; } .studio-page .review-detail { position: static; max-height: none; } }
        @media (max-width: 1100px) { .app-sidebar { width: 186px; min-width: 186px; } .app-main { max-width: calc(100vw - 186px); } .content-panels > .q-panel > div { padding: 18px !important; } .atlas-pipeline { grid-template-columns: repeat(2,minmax(0,1fr)); } .atlas-metrics { grid-template-columns: repeat(3,minmax(0,1fr)); } .studio-sidebar { width: 196px; min-width: 196px; } .studio-main { max-width: calc(100vw - 196px); } .studio-content > .q-panel > div { padding: 24px !important; } }
        @media (max-width: 780px) { .studio-sidebar { display: none; } .studio-main { max-width: 100vw; } .studio-topbar { padding: 0 18px; } .studio-content > .q-panel > div { padding: 18px !important; } .studio-rail, .studio-metrics { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 20px 0; } .studio-rail::before { display: none; } .studio-metric:nth-child(2n) { border-right: 0; } .studio-sequence { padding-left: 0; border-left: 0; } }
        """
    )

    def selected_date() -> str:
        return state["run_date"]

    def refresh_date() -> None:
        state["run_date"] = str(date_select.value)
        state["review_item_id"] = None
        dashboard_panel.refresh()
        review_panel.refresh()
        digest_panel.refresh()
        history_panel.refresh()

    def current_workflow_signature() -> tuple[Any, ...]:
        run_date = selected_date()
        snap = snapshot(ROOT_DIR, run_date)
        job = JOBS.latest(run_date)
        return (
            run_date,
            snap.collection_status,
            snap.review_status,
            snap.digest_status,
            snap.finalized_status,
            snap.included,
            snap.maybe,
            snap.unreviewed,
            tuple(sorted(snap.counts.items())),
            job.kind if job else "",
            job.status if job else "",
        )

    @ui.refreshable
    def workflow_overview() -> None:
        run_date = selected_date()
        snap = snapshot(ROOT_DIR, run_date)
        job = JOBS.latest(run_date)
        info = workflow_info(snap, job)
        state["workflow_signature"] = current_workflow_signature()
        collection_status = "running" if job and job.kind == "collection" and job.status == "running" else snap.collection_status
        digest_status = "running" if job and job.kind == "digest" and job.status == "running" else snap.digest_status
        statuses = [collection_status, snap.review_status, digest_status, snap.finalized_status]

        if theme == "studio":
            completed_steps = 4 if info["stage"] == 5 else max(0, info["stage"] - 1)
            display_stage = min(info["stage"], 4)
            progress_percent = round(info["progress"] * 100)
            with ui.card().classes("studio-status w-full").style(f"--studio-progress: {progress_percent}%"):
                with ui.row().classes("studio-status-body w-full items-center no-wrap gap-7"):
                    with ui.column().classes("min-w-0 flex-1 gap-2"):
                        ui.label(f"{run_date} · DAILY HANDOFF").classes("studio-kicker")
                        ui.label(info["title"]).classes("studio-status-title")
                        ui.label(info["detail"]).classes("studio-status-copy")
                        with ui.row().classes("items-center gap-2 mt-2"):
                            ui.button(info["action"], icon="arrow_forward", color="primary", on_click=go_to_next_step).props("unelevated no-caps").classes("studio-action")
                            if snap.collection_status == "complete" and run_date == current_run_date() and not JOBS.active(run_date):
                                ui.button("重新采集", icon="restart_alt", on_click=lambda: rerun_dialog.open()).props("flat dense no-caps color=teal-9")
                    with ui.column().classes("studio-sequence items-end gap-1"):
                        ui.label(f"{display_stage:02d} / 04").classes("studio-sequence-number")
                        ui.label("当前交接阶段").classes("studio-kicker")
                        ui.label(f"{completed_steps} 个阶段已完成").classes("text-xs text-slate-500")
        elif theme == "atlas":
            completed_steps = 4 if info["stage"] == 5 else max(0, info["stage"] - 1)
            progress_percent = round(info["progress"] * 100)
            with ui.card().classes("atlas-hero w-full"):
                with ui.row().classes("w-full items-center gap-6"):
                    with ui.column().classes("min-w-0 flex-1 gap-1"):
                        ui.label("TODAY'S STATUS").classes("caps text-amber-900")
                        ui.label(info["title"]).classes("atlas-hero-title")
                        ui.label(info["detail"]).classes("text-sm text-amber-950")
                        with ui.row().classes("items-center gap-2 mt-3"):
                            ui.button(info["action"], icon="arrow_forward", color="blue-grey-10", on_click=go_to_next_step).props("unelevated").classes("atlas-action")
                            if snap.collection_status == "complete" and run_date == current_run_date() and not JOBS.active(run_date):
                                ui.button("重新采集", icon="restart_alt", on_click=lambda: rerun_dialog.open()).props("flat dense color=blue-grey-10")
                    with ui.column().classes("items-center gap-1"):
                        with ui.element("div").classes("atlas-progress-ring").style(
                            f"background: conic-gradient(#0c3b46 {progress_percent}%, rgba(255,255,255,.48) 0)"
                        ):
                            with ui.element("div").classes("atlas-progress-ring-inner"):
                                ui.label(f"{progress_percent}%")
                        ui.label(f"{completed_steps} / 4 个阶段已完成").classes("text-xs font-medium text-amber-950")
        else:
            with ui.card().classes("workflow-now w-full"):
                with ui.row().classes("w-full items-center gap-5"):
                    with ui.column().classes("min-w-0 flex-1 gap-1"):
                        ui.label("当前进度").classes("text-xs font-semibold text-blue-700")
                        ui.label(info["title"]).classes("text-xl font-semibold text-slate-900")
                        ui.label(info["detail"]).classes("text-sm text-slate-600")
                        ui.linear_progress(value=info["progress"], color="primary").props("rounded size=8px").classes("mt-2")
                    ui.button(info["action"], icon="arrow_forward", on_click=go_to_next_step).props("unelevated")
                    if snap.collection_status == "complete" and run_date == current_run_date() and not JOBS.active(run_date):
                        ui.button("重新采集", icon="restart_alt", on_click=lambda: rerun_dialog.open()).props("flat")

        pipeline_class = "studio-rail" if theme == "studio" else ("atlas-pipeline" if theme == "atlas" else "pipeline-grid")
        with ui.element("div").classes(f"{pipeline_class} w-full"):
            if theme == "studio":
                studio_step_card("1", "新闻采集", statuses[0], f"{snap.counts['discovered']} 条发现" if snap.counts["discovered"] else "发现、初筛与抓取", info["stage"] == 1)
                studio_step_card("2", "人工审核", statuses[1], review_progress(snap), info["stage"] == 2)
                studio_step_card("3", "生成摘要", statuses[2], "整理已纳入新闻", info["stage"] == 3)
                studio_step_card("4", "整理定稿", statuses[3], "同步总览与编号", info["stage"] == 4)
            elif theme == "atlas":
                atlas_step_card("1", "新闻采集", statuses[0], f"{snap.counts['discovered']} 条发现" if snap.counts["discovered"] else "发现、初筛与抓取", info["stage"] == 1)
                atlas_step_card("2", "人工审核", statuses[1], review_progress(snap), info["stage"] == 2)
                atlas_step_card("3", "生成摘要", statuses[2], "整理已纳入新闻", info["stage"] == 3)
                atlas_step_card("4", "整理定稿", statuses[3], "同步总览与编号", info["stage"] == 4)
            else:
                step_card("1", "新闻采集", statuses[0], "发现、初筛、抓取与去重", info["stage"] == 1)
                step_card("2", "人工审核", statuses[1], review_progress(snap), info["stage"] == 2)
                step_card("3", "生成摘要", statuses[2], "准备输入并调用 DeepSeek", info["stage"] == 3)
                step_card("4", "整理定稿", statuses[3], "同步总览、编号与格式", info["stage"] == 4)

        metrics_class = "studio-metrics" if theme == "studio" else ("atlas-metrics" if theme == "atlas" else "metrics-grid")
        metric_wrapper = "studio-data-strip w-full" if theme == "studio" else "w-full"
        with ui.element("div").classes(metric_wrapper):
            if theme == "studio":
                ui.label("采集漏斗 · SOURCE FUNNEL").classes("studio-kicker mb-3")
            with ui.element("div").classes(f"{metrics_class} w-full"):
                metric_card("发现", snap.counts["discovered"], theme)
                metric_card("RSS 初筛", snap.counts["prescreened"], theme)
                metric_card("LLM 抓取", snap.counts["prefiltered"], theme)
                metric_card("全文抓取", snap.counts["fetched"], theme)
                metric_card("待审核", snap.counts["review"], theme)
                metric_card("最终纳入", snap.included + snap.maybe, theme)

        with ui.dialog() as rerun_dialog, ui.card().classes("max-w-xl"):
            ui.label("重新采集今天新闻？").classes("text-xl font-medium")
            ui.label("当前审核决策、摘要和定稿状态会先备份，然后从新闻发现重新运行。")
            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=rerun_dialog.close).props("flat")
                ui.button("备份并重新采集", icon="restart_alt", color="warning", on_click=lambda: confirm_collection_rerun(run_date, rerun_dialog))

    @ui.refreshable
    def dashboard_panel() -> None:
        run_date = selected_date()
        snap = snapshot(ROOT_DIR, run_date)
        job = JOBS.latest(run_date)
        with ui.column().classes("w-full gap-4"):
            if theme == "classic":
                with ui.row().classes("w-full items-end justify-between"):
                    with ui.column().classes("gap-1"):
                        ui.label("DAILY PIPELINE").classes("section-eyebrow")
                        ui.label(f"{run_date} 流程概览").classes("page-title")
                        ui.label("按顺序完成采集、审核、摘要和定稿；状态每 2 秒自动同步。 ").classes("page-subtitle")
                    ui.button("刷新概览", icon="refresh", on_click=dashboard_panel.refresh).props("flat dense")

            workflow_overview()

            log_classes = "studio-log" if theme == "studio" else ("atlas-log" if theme == "atlas" else "log-card")
            with ui.card().classes(f"{log_classes} w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("运行日志").classes("text-lg font-medium")
                    with ui.row().classes("items-center gap-3"):
                        follow_toggle = ui.switch("自动跟随最新", value=True).props("dense color=primary")
                        if job:
                            status_badge(job.status)
                log_text = current_log(run_date, job)
                with ui.scroll_area().classes("w-full h-96") as log_scroll:
                    log_code = ui.label(log_text or "尚无日志。").classes("log-output")
                ui.button("跳到最新", icon="vertical_align_bottom", on_click=lambda: log_scroll.scroll_to(percent=1)).props("flat dense")
                state["log_code"] = log_code
                state["log_scroll"] = log_scroll
                state["log_follow"] = follow_toggle

    @ui.refreshable
    def review_panel() -> None:
        run_date = selected_date()
        review_path = run_path(ROOT_DIR, run_date, "review.md")
        items = read_review_items(review_path)
        reviewable = sorted((item for item in items if item.reviewable), key=review_score, reverse=True)
        with ui.column().classes("w-full gap-3"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("EDITORIAL REVIEW").classes("section-eyebrow")
                    ui.label("新闻审核").classes("page-title")
                    ui.label("左侧选择新闻，右侧阅读全文信息并直接作出决策。").classes("page-subtitle")
                ui.button("刷新", icon="refresh", on_click=review_panel.refresh).props("flat")

            if not review_path.exists():
                with ui.card().classes("w-full bg-blue-50 shadow-none"):
                    ui.label("尚无可审核内容").classes("font-semibold text-blue-950")
                    ui.label("该日期还没有 review.md，请先在工作台完成新闻采集。").classes("text-sm text-blue-900")
                return

            decision_counts = count_decisions(reviewable)
            reviewed_count = len(reviewable) - decision_counts["unreviewed"]
            with ui.row().classes("w-full items-center gap-3"):
                compact_stat("纳入", decision_counts["include"], "positive")
                compact_stat("备选", decision_counts["maybe"], "warning")
                compact_stat("排除", decision_counts["exclude"], "negative")
                compact_stat("未审核", decision_counts["unreviewed"], "grey")
                ui.space()
                ui.label(f"审核进度 {reviewed_count} / {len(reviewable)}").classes("text-sm font-medium text-slate-700")
                ui.linear_progress(
                    value=reviewed_count / len(reviewable) if reviewable else 0,
                    color="positive" if decision_counts["unreviewed"] == 0 else "primary",
                ).props("rounded size=7px").classes("w-40")
                if len(items) > len(reviewable):
                    ui.badge(f"{len(items) - len(reviewable)} 条抓取问题", color="grey").props("outline")

            if not reviewable:
                ui.label("该日期没有可审核的新闻。").classes("text-warning")
                return

            valid_ids = {item.id for item in reviewable}
            if state.get("review_item_id") not in valid_ids:
                first_unreviewed = next((item for item in reviewable if item.decision == "unreviewed"), reviewable[0])
                state["review_item_id"] = first_unreviewed.id
            selected_item = next(item for item in reviewable if item.id == state["review_item_id"])

            rows = []
            for position, item in enumerate(reviewable, start=1):
                row = item.to_row()
                row["position"] = position
                row["decision_label"] = DECISION_LABELS[item.decision]
                try:
                    row["score_number"] = int(float(item.score))
                except ValueError:
                    row["score_number"] = 0
                rows.append(row)
            columns = [
                {"name": "position", "label": "编号", "field": "position", "sortable": True, "align": "right", "style": "width:48px"},
                {"name": "decision_label", "label": "决策", "field": "decision_label", "sortable": True, "align": "left", "style": "width:58px"},
                {"name": "score_number", "label": "分数", "field": "score_number", "sortable": True, "align": "right", "style": "width:48px"},
                {"name": "display_title", "label": "标题", "field": "display_title", "sortable": True, "align": "left", "style": "min-width:250px;width:44%"},
                {"name": "source", "label": "来源", "field": "source", "sortable": True, "align": "left", "style": "min-width:120px;width:22%"},
                {"name": "level", "label": "等级", "field": "level", "sortable": True, "align": "left", "style": "width:60px"},
            ]
            with ui.row().classes("review-layout w-full no-wrap items-start gap-4"):
                with ui.card().classes("review-list w-[48%] min-w-0 p-0"):
                    search = ui.input("搜索标题或来源", placeholder="输入标题或来源…").props("dense outlined clearable name=review_search autocomplete=off").classes("w-full px-3 pt-3")
                    table = ui.table(
                        columns=columns,
                        rows=rows,
                        row_key="id",
                        pagination={"rowsPerPage": 12, "sortBy": "position", "descending": False},
                    ).classes("review-table w-full").props("flat dense bordered wrap-cells separator=horizontal")
                    search.bind_value(table, "filter")
                    table.on(
                        "row-click",
                        handler=lambda event: choose_review_item(event.args),
                        js_handler="(event, row) => emit(row.id)",
                    )
                    with ui.row().classes("w-full justify-end px-3 pb-3"):
                        ui.button(
                            "未审核全部设为排除",
                            icon="playlist_remove",
                            on_click=lambda: bulk_dialog.open(),
                        ).props("flat dense color=negative").set_enabled(decision_counts["unreviewed"] > 0)

                with ui.card().classes("review-detail w-[52%] min-w-0"):
                    render_review_detail(run_date, selected_item, reviewable)

            with ui.dialog() as bulk_dialog, ui.card():
                ui.label("确认批量排除？").classes("text-lg font-medium")
                ui.label(f"这会把当前 {decision_counts['unreviewed']} 条未审核新闻全部设为排除，已有决策不会改变。")
                with ui.row().classes("justify-end w-full"):
                    ui.button("取消", on_click=bulk_dialog.close).props("flat")
                    ui.button("确认排除", color="negative", on_click=lambda: confirm_bulk_exclude(run_date, bulk_dialog))

    @ui.refreshable
    def digest_panel() -> None:
        run_date = selected_date()
        snap = snapshot(ROOT_DIR, run_date)
        text = read_digest(ROOT_DIR, run_date)
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("DIGEST STUDIO").classes("section-eyebrow")
                    ui.label("日报生成与定稿").classes("page-title")
                    ui.label("生成后可直接编辑；定稿会自动整理总览、栏目、编号和空行。").classes("page-subtitle")
                with ui.row().classes("items-center"):
                    if snap.finalized_status == "complete":
                        ui.badge("已定稿", color="positive")
                    elif snap.digest_exists:
                        ui.badge("待定稿", color="warning")

            can_generate = (
                not DEMO_MODE
                and snap.review_status == "complete"
                and snap.included + snap.maybe > 0
                and not JOBS.active(run_date)
            )
            with ui.card().classes("w-full shadow-none"):
                with ui.row().classes("w-full items-center gap-3"):
                    ui.button(
                        "生成新闻摘要" if not snap.digest_exists else "重新生成摘要",
                        icon="auto_awesome",
                        on_click=lambda: generation_dialog.open(),
                    ).props("unelevated").set_enabled(can_generate)
                    ui.button("刷新内容", icon="refresh", on_click=digest_panel.refresh).props("flat")
                    ui.space()
                    if snap.review_status != "complete":
                        ui.label(f"还有 {snap.unreviewed} 条新闻未审核").classes("text-sm text-warning")
                    elif snap.included + snap.maybe == 0:
                        ui.label("至少需要纳入或备选 1 条新闻").classes("text-sm text-warning")

            if not text:
                with ui.card().classes("w-full bg-blue-50 shadow-none"):
                    ui.label("尚未生成今日摘要").classes("font-medium text-blue-900")
                    ui.label(
                        "完成全部审核且至少纳入 1 条新闻后，上方“生成新闻摘要”按钮会变为可用。"
                    ).classes("text-sm text-blue-900")

            with ui.card().classes("w-full"):
                editor = ui.textarea(value=text, placeholder="日报生成后会显示在这里…")
                editor.classes("editor w-full").style("min-height: 58vh").props("outlined autogrow name=digest_editor autocomplete=off")
                with ui.row().classes("w-full items-center gap-3"):
                    ui.button("保存草稿", icon="save", on_click=lambda: save_editor(run_date, editor)).set_enabled(bool(text))
                    ui.button("整理并定稿", icon="done_all", color="positive", on_click=lambda: finalize_editor(run_date, editor)).set_enabled(bool(text))
                    ui.space()
                    digest_path = run_path(ROOT_DIR, run_date, "llm_output.md")
                    ui.button("下载 Markdown", icon="download", on_click=lambda: ui.download(str(digest_path))).props("flat").set_enabled(digest_path.exists())

            with ui.dialog() as generation_dialog, ui.card().classes("max-w-lg"):
                ui.label("生成新闻摘要").classes("text-xl font-medium")
                ui.label("已纳入的新闻正文将发送给当前配置的 DeepSeek API。重新生成会覆盖现有摘要文件。")
                with ui.row().classes("justify-end w-full"):
                    ui.button("取消", on_click=generation_dialog.close).props("flat")
                    ui.button(
                        "同意并生成",
                        icon="auto_awesome",
                        on_click=lambda: confirm_generation(run_date, generation_dialog),
                    )

    @ui.refreshable
    def history_panel() -> None:
        if not state["history_loaded"]:
            with ui.column().classes("w-full gap-4"):
                with ui.column().classes("gap-0"):
                    ui.label("ARCHIVE").classes("section-eyebrow")
                    ui.label("历史运行记录").classes("page-title")
                    ui.label("进入历史记录页面后再加载全部日期统计，避免拖慢工作台首屏。").classes("page-subtitle")
                with ui.card().classes("w-full items-center justify-center py-12 shadow-none"):
                    ui.spinner(size="28px")
                    ui.label("正在准备历史记录…").classes("text-sm text-slate-500")
            return
        rows = []
        for run_date in available_run_dates(ROOT_DIR):
            snap = snapshot(ROOT_DIR, run_date)
            rows.append(
                {
                    "run_date": run_date,
                    "discovered": snap.counts["discovered"],
                    "review": snap.counts["review"],
                    "included": snap.included + snap.maybe,
                    "collection": STATUS_LABELS[snap.collection_status][0],
                    "review_status": STATUS_LABELS[snap.review_status][0],
                    "digest": STATUS_LABELS[snap.digest_status][0],
                    "finalized": STATUS_LABELS[snap.finalized_status][0],
                }
            )
        columns = [
            {"name": "run_date", "label": "日期", "field": "run_date", "sortable": True},
            {"name": "discovered", "label": "发现", "field": "discovered", "sortable": True},
            {"name": "review", "label": "待审核", "field": "review", "sortable": True},
            {"name": "included", "label": "纳入", "field": "included", "sortable": True},
            {"name": "collection", "label": "采集", "field": "collection"},
            {"name": "review_status", "label": "审核", "field": "review_status"},
            {"name": "digest", "label": "摘要", "field": "digest"},
            {"name": "finalized", "label": "定稿", "field": "finalized"},
        ]
        with ui.column().classes("w-full gap-4"):
            with ui.column().classes("gap-0"):
                ui.label("ARCHIVE").classes("section-eyebrow")
                ui.label("历史运行记录").classes("page-title")
                ui.label("按日期回看每次新闻流程的采集、审核与定稿状态。").classes("page-subtitle")
            with ui.card().classes("w-full p-0"):
                ui.table(columns=columns, rows=rows, row_key="run_date", pagination=20).classes("w-full").props("flat dense separator=horizontal")

    def step_card(number: str, title: str, status: str, detail: str, active: bool = False) -> None:
        active_class = "stage-active" if active else ""
        with ui.card().classes(f"step-card {active_class}"):
            with ui.row().classes("w-full items-start justify-between"):
                with ui.row().classes("items-center gap-3"):
                    with ui.element("div").classes("step-index"):
                        ui.label(number)
                    ui.label(title).classes("font-semibold text-slate-800")
                if active:
                    with ui.row().classes("items-center gap-2"):
                        with ui.element("span").classes("stage-pulse"):
                            pass
                        ui.label("当前").classes("text-xs font-semibold text-amber-800")
                else:
                    status_badge(status)
            ui.label(detail).classes("mt-2 text-xs leading-relaxed text-slate-500")

    def atlas_step_card(number: str, title: str, status: str, detail: str, active: bool) -> None:
        state_class = "active" if active else ("" if status == "complete" else "pending")
        with ui.card().classes(f"atlas-stage {state_class}"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(f"步骤 {number}").classes("text-[10px] text-slate-500")
                if active:
                    with ui.row().classes("items-center gap-1"):
                        with ui.element("span").classes("stage-pulse"):
                            pass
                        ui.label("当前").classes("text-[10px] font-semibold text-amber-800")
                else:
                    ui.label("已完成" if status == "complete" else "等待").classes("text-[10px] text-slate-500")
            ui.label(title).classes("mt-2 text-sm font-semibold text-slate-900")
            ui.label(detail).classes("mt-2 text-xs text-slate-500")

    def studio_step_card(number: str, title: str, status: str, detail: str, active: bool) -> None:
        state_class = "active" if active else ("complete" if status == "complete" else "pending")
        state_label = "正在进行" if active else ("已交接" if status == "complete" else "等待前序")
        with ui.card().classes(f"studio-stage {state_class}"):
            with ui.element("div").classes("studio-stage-node"):
                if status == "complete" and not active:
                    ui.icon("check", size="16px")
                else:
                    ui.label(f"0{number}")
            ui.label(title).classes("studio-stage-title")
            ui.label(detail).classes("studio-stage-detail")
            ui.label(state_label).classes("studio-stage-state")

    def metric_card(label: str, value: int, current_theme: str = "classic") -> None:
        if current_theme == "studio":
            with ui.element("div").classes("studio-metric"):
                ui.label(label).classes("studio-metric-label")
                ui.label(str(value)).classes("studio-metric-value")
            return
        if current_theme == "atlas":
            with ui.element("div").classes("atlas-metric"):
                ui.label(label).classes("text-[10px] text-slate-500")
                ui.label(str(value)).classes("mt-1 text-xl font-semibold text-slate-900 review-position")
            return
        with ui.card().classes("metric-card"):
            ui.label(label).classes("text-xs font-medium text-slate-500")
            ui.label(str(value)).classes("mt-1 text-2xl font-semibold tracking-tight text-slate-900")

    def compact_stat(label: str, value: int, color: str) -> None:
        with ui.row().classes("items-center gap-1 rounded border border-slate-200 bg-white px-3 py-1"):
            ui.label(label).classes("text-sm text-grey-7")
            ui.badge(str(value), color=color)

    def review_progress(snap: Any) -> str:
        if not snap.review_exists:
            return "等待采集完成"
        return f"纳入 {snap.included} / 备选 {snap.maybe} / 未审核 {snap.unreviewed}"

    def count_decisions(items: list[Any]) -> dict[str, int]:
        counts = {key: 0 for key in DECISION_LABELS}
        for item in items:
            counts[item.decision] += 1
        return counts

    def current_log(run_date: str, job: Job | None) -> str:
        if job and job.lines:
            return "\n".join(job.lines[-120:])
        daily_log = ROOT_DIR / "logs" / f"daily_{run_date}.log"
        lines = read_text(daily_log).splitlines()
        return "\n".join(lines[-120:])

    def refresh_live_log() -> None:
        log_code = state.get("log_code")
        log_scroll = state.get("log_scroll")
        follow_toggle = state.get("log_follow")
        if not log_code or log_code.is_deleted:
            return
        run_date = selected_date()
        latest = current_log(run_date, JOBS.latest(run_date)) or "尚无日志。"
        if latest == log_code.text:
            return
        log_code.set_text(latest)
        if log_scroll and not log_scroll.is_deleted and follow_toggle and follow_toggle.value:
            log_scroll.scroll_to(percent=1)

    def refresh_live_state() -> None:
        today = current_run_date()
        previous_today = state.get("known_today")
        if today != previous_today:
            state["known_today"] = today
            refreshed_dates = sorted(set([today, *available_run_dates(ROOT_DIR)]), reverse=True)
            if selected_date() == previous_today:
                state["run_date"] = today
                state["review_item_id"] = None
                date_select.set_options(refreshed_dates, value=today)
                dashboard_panel.refresh()
                review_panel.refresh()
                digest_panel.refresh()
                history_panel.refresh()
            else:
                date_select.set_options(refreshed_dates)
        refresh_live_log()
        signature = current_workflow_signature()
        if signature != state.get("workflow_signature"):
            active_tab = state.get("active_tab", "工作台")
            workflow_overview.refresh()
            tabs.value = active_tab
            tabs.update()

    def go_to_next_step() -> None:
        run_date = selected_date()
        snap = snapshot(ROOT_DIR, run_date)
        job = JOBS.latest(run_date)
        info = workflow_info(snap, job)
        if info["stage"] == 1:
            if job and job.status == "running":
                log_scroll = state.get("log_scroll")
                if log_scroll and not log_scroll.is_deleted:
                    log_scroll.scroll_to(percent=1)
                return
            if run_date != current_run_date():
                ui.notify("历史日期只读，请切换到今天后启动采集。", type="warning")
                return
            start_collection(run_date)
            return
        target_tab = review_tab if info["stage"] == 2 else digest_tab
        state["active_tab"] = "新闻审核" if info["stage"] == 2 else "摘要定稿"
        tabs.value = target_tab
        tabs.update()

    def start_collection(run_date: str) -> None:
        try:
            JOBS.start(run_date, "collection", "新闻采集", collection_commands(run_date))
            ui.notify("新闻采集已在后台启动。", type="positive")
            workflow_overview.refresh()
        except Exception as exc:
            notify_error(exc)

    def confirm_collection_rerun(run_date: str, dialog: Any) -> None:
        try:
            backup_dir = backup_before_collection_rerun(ROOT_DIR, run_date)
            state["review_item_id"] = None
            dialog.close()
            JOBS.start(run_date, "collection", "重新采集", collection_commands(run_date))
            ui.notify(f"已备份旧结果并开始重新采集：{backup_dir.name}", type="positive")
            workflow_overview.refresh()
            review_panel.refresh()
            digest_panel.refresh()
        except Exception as exc:
            notify_error(exc)

    def choose_review_item(raw_id: Any) -> None:
        value = raw_id[0] if isinstance(raw_id, (list, tuple)) and raw_id else raw_id
        if isinstance(value, dict):
            value = value.get("id")
        try:
            state["review_item_id"] = int(value)
            review_panel.refresh()
        except (TypeError, ValueError):
            ui.notify("无法识别所选新闻。", type="negative")

    def move_review_item(reviewable: list[Any], current_id: int, offset: int) -> None:
        ids = [item.id for item in reviewable]
        try:
            target_index = ids.index(current_id) + offset
        except ValueError:
            return
        if 0 <= target_index < len(ids):
            state["review_item_id"] = ids[target_index]
            review_panel.refresh()

    def set_item_decision(run_date: str, item: Any, decision: str) -> None:
        try:
            current_items = sorted(
                (candidate for candidate in read_review_items(run_path(ROOT_DIR, run_date, "review.md")) if candidate.reviewable),
                key=review_score,
                reverse=True,
            )
            next_id = next_unreviewed_item_id(current_items, item.id) if decision != "unreviewed" else None
            if item.decision != decision:
                update_review_item(ROOT_DIR, run_date, item.id, decision=decision)
            if next_id is not None:
                state["review_item_id"] = next_id
                ui.notify(f"已设为“{DECISION_LABELS[decision]}”，进入下一条未审核新闻。", type="positive")
            else:
                state["review_item_id"] = item.id
                message = "审核已全部完成，可以生成摘要。" if decision != "unreviewed" else "已清除该条决策。"
                ui.notify(message, type="positive" if decision != "unreviewed" else "info")
            review_panel.refresh()
            workflow_overview.refresh()
            digest_panel.refresh()
        except Exception as exc:
            notify_error(exc)

    def render_review_detail(run_date: str, item: Any, reviewable: list[Any]) -> None:
        index = next(index for index, candidate in enumerate(reviewable) if candidate.id == item.id)
        reviewed_count = sum(candidate.decision != "unreviewed" for candidate in reviewable)
        with ui.row().classes("w-full items-center gap-3 rounded-lg bg-slate-50 px-3 py-2"):
            ui.badge(f"第 {index + 1} / {len(reviewable)} 条", color="primary").props("outline")
            ui.label(f"已审核 {reviewed_count} 条，还剩 {len(reviewable) - reviewed_count} 条").classes("text-xs text-slate-600")
            ui.linear_progress(
                value=reviewed_count / len(reviewable),
                color="positive" if reviewed_count == len(reviewable) else "primary",
            ).props("rounded size=6px").classes("flex-1")
        with ui.row().classes("w-full items-start justify-between gap-3"):
            with ui.column().classes("min-w-0 flex-1 gap-1"):
                ui.label(item.final_title or item.suggested_title or item.title).classes("text-xl font-semibold leading-snug")
                with ui.row().classes("items-center gap-2 text-sm text-grey-7"):
                    ui.label(item.source or "未知来源")
                    ui.label(f"分数 {item.score or '-'}")
                    ui.badge(item.level, color="primary").props("outline")
                    category = item.final_category or item.suggested_category
                    if category:
                        ui.label(category)
            ui.badge(DECISION_LABELS[item.decision], color=decision_color(item.decision)).classes("text-sm")

        if item.url:
            ui.link("打开原文", item.url, new_tab=True).classes("text-primary")

        with ui.row().classes("w-full items-center gap-2 py-2"):
            decision_button(run_date, item, "include", "纳入", "check", "positive")
            decision_button(run_date, item, "maybe", "备选", "bookmark", "warning")
            decision_button(run_date, item, "exclude", "排除", "close", "negative")
            decision_button(run_date, item, "unreviewed", "清除", "restart_alt", "grey")
            ui.space()
            ui.button("上一条", icon="chevron_left", on_click=lambda: move_review_item(reviewable, item.id, -1)).props("flat dense").set_enabled(index > 0)
            ui.button("下一条", icon="chevron_right", on_click=lambda: move_review_item(reviewable, item.id, 1)).props("flat dense").set_enabled(index < len(reviewable) - 1)

        ui.separator()
        ui.label("正文摘录").classes("text-lg font-medium mt-2")
        with ui.element("div").classes("article-excerpt w-full rounded bg-slate-50 p-4 text-slate-800"):
            ui.label(item.excerpt or "该条目在 review.md 中没有正文摘录。")

        if item.evidence_snippets:
            ui.label("证据句").classes("text-base font-medium mt-2")
            with ui.column().classes("w-full gap-1"):
                for snippet in item.evidence_snippets:
                    ui.label(f"• {snippet}").classes("text-sm text-slate-700")

        if item.watch_reason:
            with ui.card().classes("w-full bg-blue-50 shadow-none"):
                ui.label("为什么值得关注").classes("font-medium text-blue-900")
                ui.label(item.watch_reason).classes("text-sm text-blue-900")

        if item.cross_day_duplicate or item.duplicates_collapsed or item.duplicate_sources:
            with ui.card().classes("w-full bg-amber-50 shadow-none"):
                ui.label("重复报道提醒").classes("font-medium text-amber-900")
                if item.cross_day_duplicate:
                    ui.label(item.cross_day_duplicate).classes("text-sm")
                if item.duplicates_collapsed:
                    ui.label(f"已合并：{item.duplicates_collapsed} 篇").classes("text-sm")
                if item.duplicate_sources:
                    ui.label(f"其他来源：{item.duplicate_sources}").classes("text-sm")

        if item.decision in {"include", "maybe"}:
            ui.separator().classes("my-2")
            ui.label("入选信息").classes("text-lg font-medium")
            category_input = ui.input("最终分类", value=item.final_category or item.suggested_category).props("dense outlined name=final_category autocomplete=off").classes("w-full")
            title_input = ui.input("最终标题", value=item.final_title or item.suggested_title).props("dense outlined name=final_title autocomplete=off").classes("w-full")
            notes_input = ui.textarea("备注", value=item.notes).props("dense outlined autogrow name=review_notes autocomplete=off").classes("w-full")
            ui.button(
                "保存入选信息",
                icon="save",
                on_click=lambda: save_review_metadata(run_date, item.id, category_input, title_input, notes_input),
            ).props("outline")

        with ui.expansion("展开系统评分详情", icon="analytics").classes("w-full"):
            with ui.grid(columns=2).classes("w-full gap-2 text-sm"):
                ui.label(f"新闻类型：{item.news_type or '-'}")
                ui.label(f"项目阶段：{item.stage or '-'}")
                ui.label(f"抓取状态：{item.fetch or '-'}")
                ui.label(f"建议分类：{item.suggested_category or '-'}")
            if item.matched_keywords:
                ui.label("匹配关键词").classes("font-medium mt-2")
                ui.label("、".join(item.matched_keywords)).classes("text-sm text-grey-8")
            if item.scoring_reasons:
                ui.label("评分理由").classes("font-medium mt-2")
                for reason in item.scoring_reasons:
                    ui.label(f"• {reason}").classes("text-sm text-grey-8")

    def decision_color(decision: str) -> str:
        return {"include": "positive", "maybe": "warning", "exclude": "negative", "unreviewed": "grey"}[decision]

    def decision_button(run_date: str, item: Any, decision: str, label: str, icon: str, color: str) -> None:
        button = ui.button(label, icon=icon, color=color, on_click=lambda: set_item_decision(run_date, item, decision))
        button.props("unelevated" if item.decision == decision else "outline")

    def save_review_metadata(run_date: str, item_id: int, category: Any, title: Any, notes: Any) -> None:
        try:
            update_review_item(
                ROOT_DIR,
                run_date,
                item_id,
                final_category=str(category.value or ""),
                final_title=str(title.value or ""),
                notes=str(notes.value or ""),
            )
            ui.notify("入选信息已保存。", type="positive")
            review_panel.refresh()
        except Exception as exc:
            notify_error(exc)

    def confirm_bulk_exclude(run_date: str, dialog: Any) -> None:
        try:
            count = bulk_exclude_unreviewed(ROOT_DIR, run_date)
            dialog.close()
            ui.notify(f"已排除 {count} 条未审核新闻。", type="positive")
            review_panel.refresh()
            workflow_overview.refresh()
        except Exception as exc:
            notify_error(exc)

    def confirm_generation(run_date: str, dialog: Any) -> None:
        try:
            dialog.close()
            JOBS.start(run_date, "digest", "日报生成", generation_commands(run_date))
            ui.notify("日报生成已在后台启动。", type="positive")
            workflow_overview.refresh()
        except Exception as exc:
            notify_error(exc)

    def save_editor(run_date: str, editor: Any) -> None:
        try:
            save_digest(ROOT_DIR, run_date, str(editor.value or ""))
            ui.notify("摘要草稿已保存并同步。", type="positive")
            workflow_overview.refresh()
        except Exception as exc:
            notify_error(exc)

    def finalize_editor(run_date: str, editor: Any) -> None:
        try:
            formatted = finalize_digest(ROOT_DIR, run_date, str(editor.value or ""))
            editor.value = formatted
            editor.update()
            ui.notify("定稿完成：总览、栏目、编号和格式已同步。", type="positive")
            workflow_overview.refresh()
        except Exception as exc:
            notify_error(exc)

    def load_history_on_open(event: Any) -> None:
        state["active_tab"] = str(event.value)
        tabs.value = event.value
        if str(event.value) == "历史记录" and not state["history_loaded"]:
            state["history_loaded"] = True
            history_panel.refresh()

    if theme == "studio":
        with ui.row().classes("studio-page w-full no-wrap items-start gap-0"):
            with ui.column().classes("studio-sidebar gap-0"):
                with ui.row().classes("items-center gap-3 px-2"):
                    with ui.element("div").classes("studio-monogram"):
                        ui.label("NG")
                    with ui.column().classes("gap-0"):
                        ui.label("Nigeria News").classes("studio-brand")
                        ui.label("Editorial operations").classes("text-[10px] tracking-wide text-emerald-100/60")

                ui.label("TODAY'S DESK").classes("px-3 mt-10 mb-3 studio-kicker !text-emerald-100/45")
                with ui.tabs().props("vertical inline-label align=left").classes("studio-nav w-full") as tabs:
                    dashboard_tab = ui.tab("今日流程", icon="route")
                    review_tab = ui.tab("新闻审核", icon="fact_check")
                    digest_tab = ui.tab("摘要定稿", icon="edit_note")
                    history_tab = ui.tab("历史记录", icon="history")
                tabs.on_value_change(load_history_on_open)

                ui.space()
                with ui.column().classes("w-full gap-3"):
                    with ui.row().classes("items-center gap-2 px-3"):
                        with ui.element("span").classes("stage-pulse"):
                            pass
                        ui.label("状态每 2 秒同步").classes("text-[11px] text-emerald-100/70")
                    with ui.row().classes("px-3 gap-3"):
                        ui.link("当前 C 版", "/").classes("text-[10px] text-emerald-100/55 no-underline")
                        ui.link("经典版", "/classic").classes("text-[10px] text-emerald-100/55 no-underline")

            with ui.column().classes("studio-main flex-1 gap-0"):
                with ui.row().classes("studio-topbar w-full items-center justify-between no-wrap"):
                    with ui.column().classes("gap-0"):
                        ui.label("每日新闻运营").classes("text-[19px] font-semibold tracking-tight text-slate-900")
                        ui.label("从采集到定稿，一条连续的编辑交接线").classes("text-[11px] text-slate-500")
                    with ui.row().classes("items-center gap-3"):
                        with ui.row().classes("items-center gap-2 text-slate-500"):
                            ui.icon("schedule", size="16px")
                            ui.label("尼日利亚时间").classes("text-xs")
                        date_select = ui.select(dates, value=initial_date, label="运行日期").props("dense outlined options-dense").classes("studio-date w-44")

                with ui.tab_panels(tabs, value=dashboard_tab).classes("studio-content w-full flex-1"):
                    with ui.tab_panel(dashboard_tab):
                        dashboard_panel()
                    with ui.tab_panel(review_tab):
                        review_panel()
                    with ui.tab_panel(digest_tab):
                        digest_panel()
                    with ui.tab_panel(history_tab):
                        history_panel()
    elif theme == "atlas":
        with ui.column().classes("atlas-page w-full gap-0"):
            with ui.column().classes("atlas-shell w-full gap-0"):
                with ui.row().classes("atlas-topbar w-full items-center no-wrap gap-6"):
                    with ui.row().classes("items-center gap-1"):
                        ui.label("NIGERIA").classes("atlas-brand")
                        ui.label("NEWS").classes("atlas-brand atlas-brand-accent")
                    with ui.tabs().classes("atlas-tabs") as tabs:
                        dashboard_tab = ui.tab("工作台")
                        review_tab = ui.tab("新闻审核")
                        digest_tab = ui.tab("摘要定稿")
                        history_tab = ui.tab("历史记录")
                    tabs.on_value_change(load_history_on_open)
                    ui.space()
                    ui.link("返回经典版", "/classic").classes("text-xs text-cyan-100 no-underline")
                    date_select = ui.select(dates, value=initial_date, label="尼日利亚运行日期").props("dense outlined options-dense").classes("atlas-date w-48")

                with ui.tab_panels(tabs, value=dashboard_tab).classes("atlas-content w-full flex-1"):
                    with ui.tab_panel(dashboard_tab):
                        dashboard_panel()
                    with ui.tab_panel(review_tab):
                        review_panel()
                    with ui.tab_panel(digest_tab):
                        digest_panel()
                    with ui.tab_panel(history_tab):
                        history_panel()
    else:
        with ui.row().classes("app-shell w-full no-wrap items-start gap-0"):
            with ui.column().classes("app-sidebar gap-0"):
                with ui.row().classes("items-center gap-3 px-2"):
                    with ui.element("div").classes("brand-mark"):
                        ui.icon("newspaper", size="sm").classes("text-white")
                    with ui.column().classes("gap-0"):
                        ui.label("Nigeria News").classes("text-base font-semibold tracking-wide")
                        ui.label("编辑工作台").classes("text-xs text-slate-400")

                ui.label("每日流程").classes("px-3 mt-10 mb-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500")
                with ui.tabs().props("vertical inline-label align=left").classes("nav-tabs w-full") as tabs:
                    dashboard_tab = ui.tab("工作台", icon="space_dashboard")
                    review_tab = ui.tab("新闻审核", icon="fact_check")
                    digest_tab = ui.tab("摘要定稿", icon="edit_note")
                    history_tab = ui.tab("历史记录", icon="history")
                tabs.on_value_change(load_history_on_open)

                ui.space()
                with ui.column().classes("w-full gap-2"):
                    ui.link("试用 C 版界面", "/preview-c").classes("px-3 text-xs text-blue-300 no-underline")
                    with ui.row().classes("items-center gap-2 px-3 py-3 rounded-lg bg-slate-800/70"):
                        ui.icon("circle", size="8px").classes("text-emerald-400")
                        with ui.column().classes("gap-0"):
                            ui.label("本地服务运行中").classes("text-xs text-slate-200")
                            ui.label("localhost:8080").classes("text-[11px] text-slate-500")

            with ui.column().classes("app-main flex-1 gap-0"):
                with ui.row().classes("topbar w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label("每日新闻运营").classes("text-lg font-semibold text-slate-900")
                        ui.label("采集、审核、生成与定稿").classes("text-xs text-slate-500")
                    with ui.row().classes("items-center gap-3"):
                        ui.badge("尼日利亚时间", color="blue-grey").props("outline")
                        date_select = ui.select(dates, value=initial_date, label="运行日期").props("dense outlined options-dense").classes("w-44")

                with ui.tab_panels(tabs, value=dashboard_tab).classes("content-panels w-full flex-1"):
                    with ui.tab_panel(dashboard_tab):
                        dashboard_panel()
                    with ui.tab_panel(review_tab):
                        review_panel()
                    with ui.tab_panel(digest_tab):
                        digest_panel()
                    with ui.tab_panel(history_tab):
                        history_panel()

    date_select.on_value_change(lambda _: refresh_date())

    ui.timer(2.0, refresh_live_state)


@ui.page("/")
def main_page() -> None:
    build_main_page("atlas")


@ui.page("/classic")
def classic_page() -> None:
    build_main_page("classic")


@ui.page("/preview-c")
def preview_c_page() -> None:
    build_main_page("atlas")


@ui.page("/preview-c2")
def preview_c2_page() -> None:
    build_main_page("studio")


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="Nigeria News 工作台",
        favicon="📰",
        host="127.0.0.1",
        port=8080,
        reload=False,
        show=True,
        storage_secret=os.environ.get("NEWS_UI_STORAGE_SECRET") or secrets.token_urlsafe(32),
    )
