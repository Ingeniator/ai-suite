"""
annotator-mock TUI — workflow overseer
──────────────────────────────────────
Live 3-panel view of projects, tasks and annotation results.

Usage:
    python3 tui.py                        # connects to localhost:8010
    python3 tui.py --base http://host:8010

Keys:
    Tab / Shift+Tab   switch focus between panels
    ↑ / ↓            navigate rows
    Enter             pin selected row (project → filter tasks; task → show results)
    r                 force refresh
    q / Ctrl+C        quit
"""

import argparse
import asyncio
import collections
from typing import Optional

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    RichLog,
    Static,
)

# ── palette ───────────────────────────────────────────────────────────────────
STATE_STYLE = {
    "INITIAL":  "dim",
    "RUNNING":  "bold yellow",
    "STOPPED":  "bold orange3",
    "DONE":     "bold green",
    "ARCHIVE":  "dim",
}

LABEL_STYLE = {
    "AGREE":    "green",
    "DISAGREE": "red",
    "PARTIAL":  "yellow",
}


def _state(s: str) -> str:
    style = STATE_STYLE.get(s, "")
    return f"[{style}]{s}[/]" if style else s


def _label(l: str) -> str:
    style = LABEL_STYLE.get(l, "")
    return f"[{style}]{l}[/]" if style else l


def _bar(done: int, total: int, width: int = 12) -> str:
    if total == 0:
        return "[dim]" + "─" * width + "[/]"
    filled = int(width * done / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * done / total)
    color = "green" if done == total else "yellow"
    return f"[{color}]{bar}[/] {pct}%"


# ── widgets ───────────────────────────────────────────────────────────────────

class SectionLabel(Static):
    DEFAULT_CSS = """
    SectionLabel {
        background: $boost;
        color: $text-muted;
        padding: 0 1;
        text-style: bold;
    }
    """


class ResultsPanel(RichLog):
    DEFAULT_CSS = """
    ResultsPanel {
        height: 1fr;
        border: solid $primary-darken-2;
        padding: 0 1;
    }
    """

    def render_results(
        self,
        task: Optional[dict],
        assignments: list[dict],
        stats: Optional[dict],
    ) -> None:
        self.clear()
        if not task:
            self.write("[dim]↑ select a task in the TASKS panel to see annotation results[/]")
            return

        name  = task.get("name", task["uid"][:12])
        state = task.get("state", "?")
        total = task.get("items_total", 0)
        done  = int(task.get("items_done", 0))

        self.write(
            f"[bold]Task:[/] {name}  "
            f"[bold]State:[/] {_state(state)}  "
            f"[bold]Progress:[/] {done}/{total} {_bar(done, total)}"
        )
        self.write("")

        if not assignments:
            if state == "RUNNING":
                self.write("[yellow]annotation in progress — refresh to update[/]")
            elif state == "INITIAL":
                self.write("[dim]task not started yet[/]")
            else:
                self.write("[dim]no assignments[/]")
            return

        # per-annotator summary
        self.write("[bold underline]Annotator summary[/]")
        for asgn in assignments:
            name_a = asgn.get("marker_name", asgn["marker_id"][:8])
            counts: dict[str, int] = collections.Counter(
                item["result"]["label"] for item in asgn.get("items", [])
            )
            parts = "  ".join(
                f"{_label(lbl)} {counts.get(lbl, 0)}"
                for lbl in ("AGREE", "DISAGREE", "PARTIAL")
            )
            self.write(f"  {name_a:<20} {parts}")

        # per-item consensus
        self.write("")
        self.write("[bold underline]Per-item consensus[/]")

        # group items across annotators
        item_votes: dict[str, list[str]] = collections.defaultdict(list)
        item_meta:  dict[str, dict]      = {}
        for asgn in assignments:
            for item in asgn.get("items", []):
                iid = item["id"]
                item_votes[iid].append(item["result"]["label"])
                if iid not in item_meta:
                    data = item.get("data") or {}
                    item_meta[iid] = {
                        "file": item.get("file_name", ""),
                        "trace_id": data.get("trace_id", iid[:8]),
                        "score": data.get("score", ""),
                        "verdict": data.get("verdict", ""),
                    }

        for iid, votes in item_meta.items():
            c = collections.Counter(item_votes[iid])
            consensus = c.most_common(1)[0][0]
            agree    = c.get("AGREE", 0)
            disagree = c.get("DISAGREE", 0)
            partial  = c.get("PARTIAL", 0)
            meta = item_meta[iid]
            tid  = meta["trace_id"]
            sc   = f" score={meta['score']}" if meta["score"] != "" else ""
            vrd  = f" [{meta['verdict']}]" if meta["verdict"] else ""
            self.write(
                f"  [cyan]{tid}[/]{sc}{vrd}  "
                f"A={agree} D={disagree} P={partial}  "
                f"→ {_label(consensus)}"
            )


# ── main app ──────────────────────────────────────────────────────────────────

class AnnotatorTUI(App):
    TITLE = "annotator-mock"
    SUB_TITLE = "workflow overseer"
    CSS = """
    Screen { layout: vertical; }

    #top-row  { height: 1fr; layout: horizontal; }

    #project-pane { width: 36;  border: solid $primary-darken-2; }
    #task-pane    { width: 1fr; border: solid $primary-darken-2; }
    #results-pane { height: 16; border: solid $primary-darken-2; }

    DataTable { height: 1fr; }

    SectionLabel { dock: top; }

    ResultsPanel { height: 1fr; padding: 0 1; border: none; }
    """

    BINDINGS = [
        Binding("q",         "quit",    "Quit"),
        Binding("r",         "refresh", "Refresh"),
        Binding("tab",       "focus_next",   "Next panel",  show=False),
        Binding("shift+tab", "focus_previous", "Prev panel", show=False),
    ]

    # reactive state
    _projects:   reactive[list] = reactive([], recompose=False)
    _tasks:      reactive[list] = reactive([], recompose=False)
    _all_tasks:  reactive[list] = reactive([], recompose=False)
    _assignments: reactive[list] = reactive([], recompose=False)
    _stats:       reactive[dict | None] = reactive(None, recompose=False)

    selected_project: reactive[Optional[str]] = reactive(None)
    selected_task:    reactive[Optional[str]] = reactive(None)

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=5)
        self.sub_title = f"connecting to {self.base_url} …"
        self._do_refresh()
        self.set_interval(2, self.action_refresh)

    async def on_unmount(self) -> None:
        if self._client:
            await self._client.aclose()

    # ── compose ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="top-row"):
            with Vertical(id="project-pane"):
                yield SectionLabel("PROJECTS")
                yield DataTable(id="project-table", cursor_type="row")
            with Vertical(id="task-pane"):
                yield SectionLabel("TASKS")
                yield DataTable(id="task-table", cursor_type="row")
        with Vertical(id="results-pane"):
            yield SectionLabel(" RESULTS")
            yield ResultsPanel(id="results-log", highlight=True, markup=True)
        yield Footer()

    def on_ready(self) -> None:
        pt = self.query_one("#project-table", DataTable)
        pt.add_columns("Project", "Tasks", "Pools")

        tt = self.query_one("#task-table", DataTable)
        tt.add_columns("Name", "State", "Progress", "Overlap", "Dataset")

    # ── data fetching ──────────────────────────────────────────────────────

    @work(exclusive=True)
    async def _do_refresh(self) -> None:
        if not self._client:
            return
        try:
            projects_r  = await self._client.get("/api/v0/markup_project?size=200")
            tasks_r     = await self._client.get("/api/v0/tasks?size=200")
            projects    = projects_r.json().get("items", [])
            all_tasks   = tasks_r.json().get("items", [])
        except Exception as exc:
            err_msg = str(exc)
            self.sub_title = f"connection error — {err_msg}"
            # surface error in results panel so it's visible without a subtitle
            panel = self.query_one(ResultsPanel)
            panel.clear()
            panel.write(f"[bold red]Connection error[/]")
            panel.write(f"[red]{err_msg}[/]")
            panel.write("")
            panel.write(f"[dim]Retrying every 2 s — base URL: {self.base_url}[/]")
            return

        self._projects  = projects
        self._all_tasks = all_tasks

        # filter tasks by selected project
        if self.selected_project:
            self._tasks = [t for t in all_tasks if t["project_id"] == self.selected_project]
        else:
            self._tasks = all_tasks

        # fetch results for selected task
        if self.selected_task:
            try:
                asgn_r  = await self._client.get(f"/api/v0/assignments?task_id={self.selected_task}&size=200")
                stats_r = await self._client.get(f"/api/v0/statistics/task/{self.selected_task}")
                self._assignments = asgn_r.json().get("items", [])
                self._stats       = stats_r.json()
            except Exception:
                self._assignments = []
                self._stats       = None

        self._repaint()
        total_tasks = len(all_tasks)
        running = sum(1 for t in all_tasks if t["state"] == "RUNNING")
        done    = sum(1 for t in all_tasks if t["state"] == "DONE")
        running_s = f"⟳ {running}" if running else "0"
        done_s    = f"✓ {done}"    if done    else "0"
        self.sub_title = (
            f"{len(projects)} projects  {total_tasks} tasks  "
            f"running {running_s}  done {done_s}"
        )

    def _repaint(self) -> None:
        self._repaint_projects()
        self._repaint_tasks()
        self._repaint_results()

    def _repaint_projects(self) -> None:
        pt = self.query_one("#project-table", DataTable)
        saved_row = pt.cursor_row
        pt.clear()
        task_count = collections.Counter(t["project_id"] for t in self._all_tasks)
        for p in self._projects:
            pid    = p["uid"]
            name   = p.get("name", pid[:12])
            n_tasks = task_count.get(pid, 0)
            n_pools = len(p.get("pool_ids", []))
            marker = "▶ " if pid == self.selected_project else "  "
            pt.add_row(f"{marker}{name}", str(n_tasks), str(n_pools), key=pid)
        try:
            pt.move_cursor(row=saved_row)
        except Exception:
            pass

    def _repaint_tasks(self) -> None:
        tt = self.query_one("#task-table", DataTable)
        saved_row = tt.cursor_row
        tt.clear()
        for t in self._tasks:
            tid     = t["uid"]
            name    = t.get("name", tid[:12])
            state   = t.get("state", "?")
            total   = t.get("items_total", 0)
            done_n  = int(t.get("items_done", 0))
            overlap = t.get("overlap", 1)
            ds      = (t.get("dataset_id") or "─")[:8]
            marker  = "▶ " if tid == self.selected_task else "  "
            tt.add_row(
                f"{marker}{name}",
                _state(state),
                _bar(done_n, total, width=10),
                f"×{overlap}",
                ds,
                key=tid,
            )
        try:
            tt.move_cursor(row=saved_row)
        except Exception:
            pass

    def _repaint_results(self) -> None:
        task = next(
            (t for t in self._all_tasks if t["uid"] == self.selected_task), None
        )
        self.query_one(ResultsPanel).render_results(task, self._assignments, self._stats)

    # ── events ─────────────────────────────────────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        key      = str(event.row_key.value)

        if table_id == "project-table":
            # toggle: click same project again → show all tasks
            if self.selected_project == key:
                self.selected_project = None
            else:
                self.selected_project = key
                # clear task selection when switching project
                self.selected_task = None
            self._do_refresh()

        elif table_id == "task-table":
            if self.selected_task == key:
                self.selected_task = None
            else:
                self.selected_task = key
            self._do_refresh()

    # ── actions ────────────────────────────────────────────────────────────

    async def action_refresh(self) -> None:
        self._do_refresh()


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="annotator-mock TUI")
    parser.add_argument(
        "--base",
        default="http://localhost:8010",
        help="Base URL of the annotator-mock service (default: http://localhost:8010)",
    )
    args = parser.parse_args()
    AnnotatorTUI(base_url=args.base).run()


if __name__ == "__main__":
    main()
