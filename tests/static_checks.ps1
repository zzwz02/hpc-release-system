$ErrorActionPreference = "Stop"

function Assert-Contains {
    param(
        [string]$Path,
        [string]$Pattern
    )
    if (-not (Select-String -LiteralPath $Path -Pattern $Pattern -Encoding UTF8 -Quiet)) {
        throw "Missing pattern '$Pattern' in $Path"
    }
}

Assert-Contains "index.html" "/api/state"
Assert-Contains "index.html" "/api/import-initial"
Assert-Contains "index.html" "/api/app-info"
Assert-Contains "index.html" "/api/apps/update"
Assert-Contains "index.html" "/api/apps/qa-pass"
Assert-Contains "index.html" "/api/login"
Assert-Contains "index.html" "/api/releases/lock"
Assert-Contains "index.html" "fetch"
Assert-Contains "index.html" "test_cmd"
Assert-Contains "index.html" "app_info"
Assert-Contains "index.html" "owner_added"
Assert-Contains "README.md" "/api/*"
Assert-Contains "README.md" "Release lock"
Assert-Contains "release_system_plan.md" "AppInfoSnapshot"
Assert-Contains "release_system_state_machine.svg" "app_info"
Assert-Contains "release_system/core.py" "def apply_app_info"
Assert-Contains "release_system/core.py" "def run_admission_check"
Assert-Contains "release_system/core.py" "def add_new_app_request"
Assert-Contains "release_system/core.py" "def mark_qa_passed"
Assert-Contains "release_system/core.py" "def authenticate"
Assert-Contains "release_system/core.py" "def gerrit_push_plan"
Assert-Contains "release_system/core.py" "def parse_csv_text"
Assert-Contains "server.py" "ThreadingHTTPServer"
Assert-Contains "server.py" "/api/gerrit/plan"

Write-Output 'STATIC_CHECKS_OK'
