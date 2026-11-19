# QA CSV REST API

测试命令与兼容性验证均提供 `GET …/export` 直接下载带 UTF-8 BOM 的 CSV，使用网站登录的 `hpc_session` Cookie。不需要在客户端把 JSON 转成 CSV。页面不提供 QA 回填模板下载按钮，后端 `template=true` 接口仍保留。

| 用途 | 接口 | 参数 |
| --- | --- | --- |
| 测试命令 JSON | `GET /api/qa/test-commands` | `release_id` 必填，`hpc_version` 默认 `latest`，也可填写具体补丁版本 |
| 测试命令 CSV | `GET /api/qa/test-commands/export` | 同上；默认隐藏 py_label/torch_label，并合并仅 GPU 不同的行，与网页普通导出一致 |
| QA 回填模板 CSV | `GET /api/qa/test-commands/export?template=true` | 同上，另加 `template=true`；保留完整列和逐 GPU 行，另存为 Excel 后用于 QA log 上传 |
| 查询兼容性组件 | `GET /api/component-compatibility/components` | 无；读取 `components[].component` 获取当前组件选项 |
| 兼容性 CSV | `GET /api/lookup/latest-running/export` | 可选 `component`、`arch`、`os`、`chip`、`search`；先筛选，再合并 GPU |

测试命令接口沿用 QA 只读权限（RM、Owner、QA、Guest）；兼容性接口沿用兼容性验证权限（RM、Owner、QA）。响应为 `text/csv; charset=utf-8`，包含 `Content-Disposition: attachment` 和 `Cache-Control: no-store`。测试命令普通导出的文件名为 `test_commands_latest.csv` 或 `test_commands_<具体版本>.csv`，模板增加 `_template` 后缀；兼容性文件名为 `compatibility.csv`。

测试命令缺少镜像时仍返回带 `<hpc_version>` 占位符的命令；空测试范围返回只有表头的 CSV。Harbor/Gerrit 请求失败按错误响应返回；存在镜像解析错误、导致命令生成不完整时，CSV 接口返回 502 及具体原因，不将不完整结果当作成功文件下载。原 JSON 接口继续保留逐 GPU 原始行、完整列及 `errors`。

## 完整下载脚本

下面的脚本默认访问 `http://127.0.0.1:3577`，通过 LDAP 登录，自动获取网站默认周期的 ID，分别下载测试命令、兼容性组件列表、兼容性 CSV。使用本地账号时，将 `login_path` 改为 `/api/login`。密码隐藏输入，Cookie 使用临时文件并在退出时删除。

```bash
bash <<'BASH'
set -euo pipefail

site_base='http://127.0.0.1:3577'
login_path='/api/login/ldap'
cookie_file=$(mktemp)
trap 'rm -f "$cookie_file"' EXIT

read -r -p '网站用户名：' download_username </dev/tty
python3 -c '
import sys, json, getpass
print(json.dumps({
    "username": sys.argv[1],
    "password": getpass.getpass("网站密码：")
}))
' "$download_username" |
curl --noproxy '*' -fsS -c "$cookie_file" \
  -H 'Content-Type: application/json' \
  --data-binary @- "$site_base$login_path" >/dev/null

release_id=$(
  curl --noproxy '*' -fsS -b "$cookie_file" "$site_base/api/state" |
  python3 -c '
import json, sys
release = json.load(sys.stdin).get("release")
if not release:
    sys.exit("当前没有发布周期")
print(release["id"])
'
)

# 下载测试命令 CSV，不再需要 Python 转换。
# 如需 QA 回填模板，在此 curl 增加 --data-urlencode 'template=true'。
curl --noproxy '*' -fsS --get -b "$cookie_file" \
  --data-urlencode "release_id=$release_id" \
  --data-urlencode 'hpc_version=latest' \
  "$site_base/api/qa/test-commands/export" \
  -o test_commands.csv

# 查询组件选项。
curl --noproxy '*' -fsS -b "$cookie_file" \
  "$site_base/api/component-compatibility/components" \
  -o compatibility_components.json
python3 -c '
import json
with open("compatibility_components.json", encoding="utf-8") as f:
    print(json.dumps(json.load(f)["components"], ensure_ascii=False, indent=2))
'

# 下载兼容性 CSV；删除对应参数行即可取消筛选。
curl --noproxy '*' -fsS --get -b "$cookie_file" \
  --data-urlencode 'component=driver-sdk' \
  --data-urlencode 'arch=amd64' \
  --data-urlencode 'chip=C500' \
  "$site_base/api/lookup/latest-running/export" \
  -o compatibility.csv

echo '已生成 test_commands.csv、compatibility_components.json、compatibility.csv'
BASH
```

`component` 使用组件列表返回的完整字符串。包含空格或 `+` 的值继续通过 `--data-urlencode` 传入，例如 `--data-urlencode 'component=torch 2.8 + py312'`。
