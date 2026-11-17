"""Isolated automatic weekly report site with deterministic JIRA/LLM responses."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import parse_qs, urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="auto-weekly-site-") as temporary:
        folder = Path(temporary)
        os.environ.update(DB_PATH=str(folder / "main.db"), ADMIN_PASSWORD_FILE=str(folder / "admin"),
                          RUNTIME_CONF_PATH=str(folder / "site.conf"),
                          ASSISTANT_DATABASE_URL=f"sqlite:///{folder}/assistant.db",
                          JIRA_AGENT_DATABASE_URL=f"sqlite:///{folder}/agent.db",
                          JIRA_AGENT_DATA_DIR=str(folder / "data"), JIRA_AGENT_RUNNER_ENABLED="false",
                          JIRA_LEARNING_RUNNER_ENABLED="false", JIRA_TESTING_RUNNER_ENABLED="false",
                          APP_INFO_SYNC_ENABLED="false")
        (folder / "site.conf").write_text("[jira]\nJIRA_BASE_URL=https://fixture-jira.test\nJIRA_TOKEN=fixture\n[llm]\nLLM_BASE_URL=http://fixture-llm.test/v1\nLLM_MODEL=fixture-model\n")
        from app.config import settings
        from app.db.connection import connect
        from app.domain.authn import hash_password
        from app.integrations import jira, llm
        from app.main import create_app
        from app.services import auto_weekly_report_service as service
        from app.timeutil import beijing_now
        from tests.test_auto_weekly_report import issue, payload
        import datetime as dt
        import uvicorn
        conn = connect(settings.db_path)
        for username, role in [("weekly_rm", "RM"), ("weekly_owner", "Owner")]:
            conn.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES (?,?,?,?)",
                         (username, hash_password("test-password"), role, username))
        conn.commit()
        conn.close()
        def request(base, token, method, path, data=None, **kwargs):
            assert base == "https://fixture-jira.test" and token == "fixture"
            assert method == "GET", "automatic weekly report is read only"
            if path.endswith("/field"):
                return [{"id": "customfield_1", "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"}}]
            if "/group/member?" in path:
                return {"values": [{"name": "alice", "displayName": "测试同事"}], "total": 1}
            if "/search?" in path:
                query = parse_qs(urlsplit(path).query)["jql"][0]
                if "issuetype = Epic" in query:
                    return {"issues": [{"key": "PHS-10", "fields": {"summary": "平台研发"}}], "total": 1}
                raw = issue()
                raw["fields"]["customfield_1"] = "PHS-10"
                stamp = (beijing_now() - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000+0800")
                raw["fields"]["comment"]["comments"][0].update(created=stamp, updated=stamp)
                if 'membersOf("image_timeout")' in query:
                    raw["fields"]["attachment"] = [
                        {"filename": name, "content": f"https://fixture-jira.test/{name}", "size": 100, "created": stamp}
                        for name in ["result.png", "next.png", "result.txt"]
                    ]
                return {"issues": [raw], "total": 1}
            raise AssertionError(path)
        jira._request = request
        def download(content_url, **_kwargs):
            if content_url.endswith(".txt"):
                return b"18/18 passed"
            import io
            from PIL import Image
            image = io.BytesIO()
            Image.new("RGB", (24, 24), "white").save(image, format="PNG")
            return image.getvalue()
        jira.download_attachment = download
        def chat(system, prompt, *, progress=None, cancel=None, **kwargs):
            assert llm.require_config()["LLM_MODEL"] == "fixture-model"
            if cancel and cancel.cancelled:
                raise llm.LLMCancelled("cancelled")
            if kwargs.get("images"):
                time.sleep(1.5)
                raise llm.LLMTimeoutError("模拟图片识别超时")
            time.sleep(0.15)
            if prompt.startswith("连接检查"):
                result = {"ok": True}
            elif "提取本周事实" in prompt:
                result = {"facts": "18 个用例通过，耗时为 A100 的 1.5 倍；精度问题待修复，需协调资源"}
            elif "原始材料片段" in prompt:
                result = {"findings": []}
            else:
                result = payload()
            if progress:
                progress({"content_chunks": 1, "reasoning_chunks": 0, "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
            return json.dumps(result, ensure_ascii=False)
        llm.chat_json = chat
        generate = service.generate
        def scenarios(conn, opts, **kwargs):
            if opts["group"] == "fail":
                raise ValueError("测试 JIRA 读取失败")
            if opts["group"] == "slow":
                kwargs["progress"]("等待测试 JIRA 返回")
                while not kwargs["cancel"].cancelled:
                    time.sleep(0.05)
                raise llm.LLMCancelled("已中止")
            return generate(conn, opts, **kwargs)
        service.generate = scenarios
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
