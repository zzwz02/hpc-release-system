"""Read-only JIRA acquisition for automatic weekly reports; no local token files."""
from __future__ import annotations

import urllib.error
from urllib.parse import quote, urlencode

from app.domain import auto_weekly_report as rules
from app.integrations import jira, llm

# Avoid unbounded server-side acquisition. Hitting a budget is an explicit failure.
MAX_ISSUES = 2000
PAGE_SIZE = 100


class Reader:
    def __init__(self, cancel: llm.CancelToken):
        self.cancel = cancel
        self.warnings: list[str] = []

    def check(self):
        if self.cancel.cancelled:
            raise llm.LLMCancelled("自动周报已中止")

    def get(self, path: str):
        self.check()
        cfg = jira._require_config()
        try:
            result = jira._request(cfg["JIRA_BASE_URL"], cfg["JIRA_TOKEN"], "GET", path, timeout=20, max_bytes=16 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"JIRA 读取失败 HTTP {exc.code}，请检查权限和查询条件") from exc
        self.check()
        return result

    def search(self, jql: str, fields: str = "*all", expand: str = "") -> list[dict]:
        results = []
        seen = set()
        while True:
            page = self.get("/rest/api/2/search?" + urlencode({"jql": jql, "fields": fields, "expand": expand,
                                                             "startAt": len(results), "maxResults": PAGE_SIZE}))
            rows = page.get("issues", [])
            total = page.get("total", 0)
            if total > MAX_ISSUES:
                raise ValueError(f"JIRA 查询超过 {MAX_ISSUES} 条，请缩小时间范围")
            if len({row["key"] for row in rows}) != len(rows) or any(row["key"] in seen for row in rows):
                raise ValueError("JIRA 分页重复，请重试")
            seen.update(row["key"] for row in rows)
            results.extend(rows)
            if len(results) >= total:
                return results
            if not rows:
                raise ValueError("JIRA 分页提前结束，未获得完整数据")

    def complete(self, issue: dict, opts: dict) -> dict:
        key = quote(issue["key"], safe="")
        f = issue["fields"]
        comment = f.get("comment") or {}
        comments = comment.get("comments", [])
        if comment.get("total", 0) > len(comments):
            comments = []
            while True:
                page = self.get(f"/rest/api/2/issue/{key}/comment?startAt={len(comments)}&maxResults=100")
                rows = page.get("comments", [])
                comments.extend(rows)
                if len(comments) >= page["total"]:
                    break
                if not rows or len(comments) > 10000:
                    raise ValueError(f"{issue['key']} 评论分页不完整或超出预算")
        # An edited comment is its current body, not a historical snapshot. Never
        # import a body edited after the requested end into that week's evidence.
        filtered = []
        for c in comments:
            created, updated = c.get("created", ""), c.get("updated") or c.get("created", "")
            if updated and rules.local_time(updated) >= rules.local_time(opts["since"]) and created and rules.local_time(created) < rules.local_time(opts["until"]):
                if rules.local_time(updated) >= rules.local_time(opts["until"]):
                    self.warnings.append(f"{issue['key']} 有窗口后编辑的评论，无法还原历史正文，未引用")
                else:
                    filtered.append(c)
        f["comment"] = {"comments": filtered}
        changelog = issue.get("changelog") or {}
        histories = changelog.get("histories", [])
        if changelog.get("total", 0) > len(histories):
            histories = []
            while True:
                page = self.get(f"/rest/api/2/issue/{key}/changelog?startAt={len(histories)}&maxResults=100")
                rows = page.get("values", page.get("histories", []))
                histories.extend(rows)
                if len(histories) >= page.get("total", 0):
                    break
                if not rows or len(histories) > 10000:
                    raise ValueError(f"{issue['key']} 变更记录分页不完整或超出预算")
        issue["changelog"] = {"histories": [h for h in histories if rules.in_window(h["created"], opts)]}
        f["attachment"] = [a for a in f.get("attachment", []) if rules.in_window(a.get("created", ""), opts)]
        return issue

    def collect(self, opts: dict, progress) -> tuple[list[dict], list[dict], list[dict]]:
        group = opts["group"]
        parts = [f'assignee was in membersOf("{group}")', f'assignee in membersOf("{group}")',
                 f'creator in membersOf("{group}")', f'reporter in membersOf("{group}")', f'reporter was in membersOf("{group}")']
        def query(clauses):
            return f'({" OR ".join(clauses)}) AND updated >= "{opts["since"]}" ORDER BY key'
        progress("正在查询 JIRA 工单")
        try:
            issues = self.search(query(parts), expand="changelog")
        except RuntimeError as exc:
            if "HTTP 400" not in str(exc):
                raise
            issues = self.search(query(parts[:-1]), expand="changelog")
            self.warnings.append("JIRA 不支持 reporter was in，已去掉该条件重试")
        fields = self.get("/rest/api/2/field")
        link_field = next((f["id"] for f in fields if (f.get("schema") or {}).get("custom", "").endswith(":gh-epic-link")), None)
        name_field = next((f["id"] for f in fields if (f.get("schema") or {}).get("custom", "").endswith(":gh-epic-label")), None)
        raw_epics = self.search('project = PHS AND issuetype = Epic ORDER BY key')
        epics = [{"key": e["key"], "name": e["fields"].get(name_field) or e["fields"]["summary"]} for e in raw_epics]
        cache = {i["key"]: i["fields"] for i in issues + raw_epics}
        epic_keys = {e["key"] for e in epics}
        def resolve(key, seen):
            if key in epic_keys:
                return key
            if key in seen or len(seen) > 12:
                return None
            seen.add(key)
            if key not in cache:
                cache[key] = self.get(f"/rest/api/2/issue/{quote(key, safe='')}?fields=summary,parent,issuetype" + (f",{link_field}" if link_field else ""))["fields"]
            f = cache[key]
            return f.get(link_field) or (resolve(f["parent"]["key"], seen) if f.get("parent") else None)
        for n, issue in enumerate(issues, 1):
            progress(f"读取工单、评论和关联关系 {n}/{len(issues)}")
            self.complete(issue, opts)
            issue["epic_key"] = resolve(issue["key"], set())
            f = issue["fields"]
            links = [l.get("outwardIssue") or l.get("inwardIssue") for l in f.get("issuelinks", [])]
            if f.get("parent"):
                links.append(f["parent"])
            issue["linked_main"] = [{"key": l["key"], "summary": (l.get("fields") or cache.get(l["key"], {})).get("summary", "")}
                                    for l in links if l and l["key"].startswith(("PHS-", "CCPM-"))]
        # Group membership endpoints often require admin access. A fallback is
        # explicit, and names are not guessed from pinyin.
        members = []
        try:
            while True:
                page = self.get("/rest/api/2/group/member?" + urlencode({"groupname": group, "startAt": len(members), "maxResults": 100}))
                rows = page.get("values", [])
                members.extend(rows)
                if page.get("isLast") or len(members) >= page.get("total", 0):
                    break
                if not rows:
                    raise ValueError("组成员分页不完整")
        except RuntimeError:
            # The selected issues may currently be assigned to outsiders. Use
            # each member field's own group query rather than treating every
            # assignee in the weekly search as a department member.
            found = {}
            try:
                for field in ("assignee", "creator", "reporter"):
                    for item in self.search(f'{field} in membersOf("{group}") AND updated >= -180d ORDER BY key', fields=field):
                        person = item["fields"].get(field)
                        if person and person.get("name"):
                            found[person["name"]] = person
                members = list(found.values())
                self.warnings.append("组成员接口不可用，名单由近 180 天成员字段查询推导，可能遗漏半年无活动的成员")
            except RuntimeError:
                members = []
                self.warnings.append("组成员名单及回退查询不可用，无法确认部门归属；不写无法确认的同事姓名")
        return issues, epics, members
